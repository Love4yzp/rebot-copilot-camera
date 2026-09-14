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

from functools import lru_cache
from typing import Mapping, Sequence

from .. import assets
from ..arm.limits import LIMIT_TOLERANCE_RAD
from ..arm.robot_model import CollisionHit, LimitViolation, RobotModel, RobotPose

#: Hardware joints that map one-to-one onto URDF joints of the same name.
#: ``gripper`` is absent on purpose — see the module docstring. Derived from
#: the hardware yaml (via assets) so the six names live in exactly one place.
ARM_JOINTS = tuple(assets.arm_joint_names())

#: How many intermediate poses to test between two waypoints. Two waypoints can
#: each be perfectly legal while the straight line between them passes through
#: the arm's own body, and the operator would find that out by hearing it.
PATH_SAMPLES = 12


class ArmModel:
    """Compatibility facade over the injected project-owned model port."""

    def __init__(self, model: RobotModel | None = None) -> None:
        self._model = model or assets.robot_model()

    def check_limits(
        self, joints: Mapping[str, float], tolerance: float = LIMIT_TOLERANCE_RAD
    ) -> list[LimitViolation]:
        return self._model.check_limits(joints, tolerance)

    def check_self_collision(self, joints: Mapping[str, float]) -> list[CollisionHit]:
        return self._model.check_self_collision(joints)

    def check_path(
        self,
        start: Mapping[str, float],
        end: Mapping[str, float],
        samples: int = PATH_SAMPLES,
    ) -> list[CollisionHit]:
        return self._model.check_path(start, end, samples)

    def forward_kinematics(self, joints: Mapping[str, float]) -> RobotPose:
        return self._model.forward_kinematics(joints)

    def limits(self) -> dict[str, tuple[float, float]]:
        return self._model.limits()

    def collision_pair_count(self) -> int:
        return self._model.collision_pair_count()


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
