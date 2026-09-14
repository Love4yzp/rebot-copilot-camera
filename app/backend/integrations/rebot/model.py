"""reBot robot-model adapter and the sole direct Pinocchio boundary."""

from __future__ import annotations

import logging
from collections.abc import Mapping

import numpy as np
import pinocchio as pin
from reBotArm_control_py.kinematics.forward_kinematics import compute_fk
from reBotArm_control_py.kinematics.robot_model import load_robot_model

from ...arm.limits import LIMIT_TOLERANCE_RAD
from ...arm.robot_model import CollisionHit, LimitViolation, RobotPose

log = logging.getLogger(__name__)


class RebotRobotModel:
    """RS model loaded through upstream, with missing collision operations added."""

    def __init__(
        self,
        urdf_path: str,
        package_dir: str,
        end_effector_frame: str,
        arm_joints: tuple[str, ...],
    ) -> None:
        # Model construction deliberately goes through the upstream public API.
        # Passing the RS path is mandatory: its default resolves to the DM arm.
        self._model = load_robot_model(urdf_path=urdf_path)
        self._data = self._model.createData()
        self._ee_frame = end_effector_frame
        self._geom = pin.buildGeomFromUrdf(
            self._model,
            urdf_path,
            pin.GeometryType.COLLISION,
            package_dirs=package_dir,
        )
        self._geom.addAllCollisionPairs()
        self._drop_pairs_touching_at_rest()
        self._geom_data = self._geom.createData()
        self._q_index = {
            name: self._model.joints[self._model.getJointId(name)].idx_q
            for name in arm_joints
            if self._model.existJointName(name)
        }
        missing = [name for name in arm_joints if name not in self._q_index]
        if missing:
            raise RuntimeError(f"URDF is missing expected joints: {missing}")

    def _drop_pairs_touching_at_rest(self) -> None:
        data = self._model.createData()
        geom_data = self._geom.createData()
        pin.computeCollisions(
            self._model,
            data,
            self._geom,
            geom_data,
            pin.neutral(self._model),
            False,
        )
        structural = [
            index
            for index, result in enumerate(geom_data.collisionResults)
            if result.isCollision()
        ]
        for index in reversed(structural):
            pair = self._geom.collisionPairs[index]
            log.debug(
                "excluding structural pair %s <-> %s",
                self._geom.geometryObjects[pair.first].name,
                self._geom.geometryObjects[pair.second].name,
            )
            self._geom.removeCollisionPair(pair)

    def _to_q(self, joints: Mapping[str, float]) -> np.ndarray:
        q = pin.neutral(self._model)
        for name, value in joints.items():
            index = self._q_index.get(name)
            if index is not None:
                q[index] = value
        return q

    def check_limits(
        self,
        joints: Mapping[str, float],
        tolerance: float = LIMIT_TOLERANCE_RAD,
    ) -> list[LimitViolation]:
        violations = []
        for name, value in joints.items():
            index = self._q_index.get(name)
            if index is None:
                continue
            lower = float(self._model.lowerPositionLimit[index])
            upper = float(self._model.upperPositionLimit[index])
            if not (lower - tolerance <= value <= upper + tolerance):
                violations.append(LimitViolation(name, float(value), lower, upper))
        return violations

    def check_self_collision(self, joints: Mapping[str, float]) -> list[CollisionHit]:
        q = self._to_q(joints)
        pin.computeCollisions(
            self._model, self._data, self._geom, self._geom_data, q, True
        )
        hits = []
        for index, result in enumerate(self._geom_data.collisionResults):
            if result.isCollision():
                pair = self._geom.collisionPairs[index]
                hits.append(
                    CollisionHit(
                        self._geom.geometryObjects[pair.first].name,
                        self._geom.geometryObjects[pair.second].name,
                    )
                )
        return hits

    def check_path(
        self,
        start: Mapping[str, float],
        end: Mapping[str, float],
        samples: int = 12,
    ) -> list[CollisionHit]:
        names = set(start) | set(end)
        for step in range(1, samples + 1):
            t = step / (samples + 1)
            mid = {
                name: start.get(name, 0.0) * (1 - t) + end.get(name, 0.0) * t
                for name in names
            }
            hits = self.check_self_collision(mid)
            if hits:
                return hits
        return []

    def forward_kinematics(self, joints: Mapping[str, float]) -> RobotPose:
        position, rotation, homogeneous = compute_fk(
            self._model, self._to_q(joints), frame_name=self._ee_frame
        )
        return RobotPose(position, rotation, homogeneous)

    def limits(self) -> dict[str, tuple[float, float]]:
        return {
            name: (
                float(self._model.lowerPositionLimit[index]),
                float(self._model.upperPositionLimit[index]),
            )
            for name, index in self._q_index.items()
        }

    def collision_pair_count(self) -> int:
        return len(self._geom.collisionPairs)
