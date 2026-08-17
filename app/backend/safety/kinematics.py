"""Joint limits and self-collision, both read from the URDF.

Limits come from the model, never from a hand-copied table. The previous
generation kept a table in config.py; a copy drifts from the hardware and then
gets trusted anyway.

Two things about this arm make the checks less obvious than they look, both
recorded in docs/HARDWARE_NOTES.md:

**The URDF has eight degrees of freedom against the hardware's seven joints.**
``joint1``..``joint6`` line up, but the single ``gripper`` motor drives two
prismatic finger joints (``joint_left``, ``joint_right``) whose limits are in
metres. There is no calibrated mapping from motor angle to finger travel, so
the gripper is deliberately *not* limit-checked here. Pretending otherwise
would mean inventing a conversion and then trusting it.

**``joint2`` and ``joint3`` have a lower limit of exactly 0.0**, and the arm's
rest pose is q = 0. The rest pose therefore sits precisely on the boundary, so
a naive ``lower <= q <= upper`` rejects waypoints on encoder noise alone.
:data:`LIMIT_TOLERANCE_RAD` exists for that.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Mapping, Sequence

import numpy as np
import pinocchio as pin

from .. import assets

log = logging.getLogger(__name__)

#: Slack on both ends of every joint limit. Covers encoder noise and the fact
#: that the rest pose sits exactly on joint2/joint3's lower bound.
LIMIT_TOLERANCE_RAD = 0.02

#: Hardware joints that map one-to-one onto URDF joints of the same name.
#: ``gripper`` is absent on purpose — see the module docstring. Derived from
#: the hardware yaml (via assets) so the six names live in exactly one place.
ARM_JOINTS = tuple(assets.arm_joint_names())

#: How many intermediate poses to test between two waypoints. Two waypoints can
#: each be perfectly legal while the straight line between them passes through
#: the arm's own body, and the operator would find that out by hearing it.
PATH_SAMPLES = 12


@dataclass(frozen=True)
class LimitViolation:
    joint: str
    value: float
    lower: float
    upper: float

    def __str__(self) -> str:
        return (
            f"{self.joint}={self.value:.4f} rad is outside "
            f"[{self.lower:.4f}, {self.upper:.4f}]"
        )


@dataclass(frozen=True)
class CollisionHit:
    first: str
    second: str

    def __str__(self) -> str:
        return f"{self.first} collides with {self.second}"


class ArmModel:
    """Pinocchio model of the RS arm, loaded once.

    Always constructed from :mod:`backend.assets`, never from upstream's
    default lookup — that resolves to the B601-DM arm and loads without error.
    """

    def __init__(self) -> None:
        assets.assert_rs_model()
        urdf = str(assets.urdf_path())
        package_dir = str(assets.urdf_path().parent.parent)

        self.model = pin.buildModelFromUrdf(urdf)
        self.data = self.model.createData()
        self.ee_frame = assets.end_effector_frame()

        self.geom = pin.buildGeomFromUrdf(
            self.model, urdf, pin.GeometryType.COLLISION, package_dirs=package_dir
        )
        self.geom.addAllCollisionPairs()
        self._drop_pairs_touching_at_rest()
        self.geom_data = self.geom.createData()

        self._q_index = {
            name: self.model.joints[self.model.getJointId(name)].idx_q
            for name in ARM_JOINTS
            if self.model.existJointName(name)
        }
        missing = [n for n in ARM_JOINTS if n not in self._q_index]
        if missing:
            raise RuntimeError(f"URDF is missing expected joints: {missing}")

    def _drop_pairs_touching_at_rest(self) -> None:
        """Remove pairs that are in contact with the arm at rest.

        The URDF ships no SRDF, so every link pair is a candidate, including
        adjacent links that are bolted together and therefore always touching.
        The rest pose is by definition not a self-collision, so whatever
        collides there is structural rather than a fault. On this URDF that is
        exactly the eight parent/child pairs, leaving 36 real ones.
        """
        data = self.model.createData()
        geom_data = self.geom.createData()
        pin.computeCollisions(self.model, data, self.geom, geom_data, pin.neutral(self.model), False)

        structural = [
            i for i, result in enumerate(geom_data.collisionResults) if result.isCollision()
        ]
        for index in reversed(structural):
            pair = self.geom.collisionPairs[index]
            log.debug(
                "excluding structural pair %s <-> %s",
                self.geom.geometryObjects[pair.first].name,
                self.geom.geometryObjects[pair.second].name,
            )
            self.geom.removeCollisionPair(pair)

    # ── configuration vectors ────────────────────────────────────────────────

    def to_q(self, joints: Mapping[str, float]) -> np.ndarray:
        """Build a full configuration vector, defaulting unmentioned joints to rest."""
        q = pin.neutral(self.model)
        for name, value in joints.items():
            index = self._q_index.get(name)
            if index is not None:
                q[index] = value
        return q

    # ── checks ───────────────────────────────────────────────────────────────

    def check_limits(
        self, joints: Mapping[str, float], tolerance: float = LIMIT_TOLERANCE_RAD
    ) -> list[LimitViolation]:
        violations = []
        for name, value in joints.items():
            index = self._q_index.get(name)
            if index is None:
                continue  # gripper, or an unknown name — not ours to judge
            lower = float(self.model.lowerPositionLimit[index])
            upper = float(self.model.upperPositionLimit[index])
            if not (lower - tolerance <= value <= upper + tolerance):
                violations.append(LimitViolation(name, float(value), lower, upper))
        return violations

    def check_self_collision(self, joints: Mapping[str, float]) -> list[CollisionHit]:
        q = self.to_q(joints)
        pin.computeCollisions(self.model, self.data, self.geom, self.geom_data, q, True)

        hits = []
        for i, result in enumerate(self.geom_data.collisionResults):
            if result.isCollision():
                pair = self.geom.collisionPairs[i]
                hits.append(
                    CollisionHit(
                        self.geom.geometryObjects[pair.first].name,
                        self.geom.geometryObjects[pair.second].name,
                    )
                )
        return hits

    def check_path(
        self,
        start: Mapping[str, float],
        end: Mapping[str, float],
        samples: int = PATH_SAMPLES,
    ) -> list[CollisionHit]:
        """Sample the straight line between two poses.

        Coarse on purpose. This catches an arm folding through itself on the
        way between two legal poses; it is not a proof of clearance.
        """
        names = set(start) | set(end)
        for step in range(1, samples + 1):
            t = step / (samples + 1)
            mid = {
                n: start.get(n, 0.0) * (1 - t) + end.get(n, 0.0) * t
                for n in names
            }
            hits = self.check_self_collision(mid)
            if hits:
                return hits
        return []

    def forward_kinematics(self, joints: Mapping[str, float]) -> pin.SE3:
        """Pose of the end-effector frame — where the camera is pointing.

        Returns a copy. ``data.oMf`` is live storage that the next
        forwardKinematics call overwrites, so handing it out directly means an
        earlier result silently changes under the caller.
        """
        q = self.to_q(joints)
        pin.forwardKinematics(self.model, self.data, q)
        pin.updateFramePlacements(self.model, self.data)
        return pin.SE3(self.data.oMf[self.model.getFrameId(self.ee_frame)])

    def limits(self) -> dict[str, tuple[float, float]]:
        return {
            name: (
                float(self.model.lowerPositionLimit[index]),
                float(self.model.upperPositionLimit[index]),
            )
            for name, index in self._q_index.items()
        }


@lru_cache(maxsize=1)
def arm_model() -> ArmModel:
    """The process-wide model. Loading parses 30 STL meshes, so it is cached."""
    return ArmModel()


def validate_pose(
    joints: Mapping[str, float], model: ArmModel | None = None
) -> list[str]:
    """Human-readable reasons a pose is unsafe. Empty means fine."""
    model = model or arm_model()
    return [str(v) for v in model.check_limits(joints)] + [
        str(h) for h in model.check_self_collision(joints)
    ]


def validate_sequence(
    poses: Sequence[Mapping[str, float]], model: ArmModel | None = None
) -> list[str]:
    """Check every pose, and the straight line between consecutive ones.

    Run before playback starts. Discovering an illegal pose by watching the arm
    reach it is the expensive way to find out.
    """
    model = model or arm_model()
    problems = []
    for index, pose in enumerate(poses):
        problems.extend(f"waypoint {index}: {reason}" for reason in validate_pose(pose, model))

    for index in range(len(poses) - 1):
        hits = model.check_path(poses[index], poses[index + 1])
        problems.extend(
            f"path {index}->{index + 1}: {hit}" for hit in hits
        )
    return problems
