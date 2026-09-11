"""Application safety policy over the SDK's explicit RS model."""

from functools import lru_cache
from typing import Mapping, Sequence
from reBotArm_control_py.collision import RobotModel
from .. import assets
from ..arm.limits import LIMIT_TOLERANCE_RAD

ARM_JOINTS = tuple(assets.arm_joint_names())
PATH_SAMPLES = 12

_selected_model = None


def configure_model(path):
    global _selected_model
    _selected_model = path
    arm_model.cache_clear()


class ArmModel(RobotModel):
    def __init__(self):
        assets.assert_rs_model()
        super().__init__(
            urdf_path=str(_selected_model or assets.urdf_path()),
            package_dir=str(assets.urdf_path().parent.parent),
            joint_names=ARM_JOINTS,
            structural_urdf_path=str(assets.urdf_path()),
            end_effector_frame=assets.end_effector_frame(),
        )

    def check_limits(self, joints, tolerance=LIMIT_TOLERANCE_RAD):
        return super().check_limits(joints, tolerance)


@lru_cache(maxsize=1)
def arm_model() -> ArmModel:
    """The process-wide model. Loading parses 30 STL meshes, so it is cached."""
    return ArmModel()


def validate_pose(joints: Mapping[str, float], model: ArmModel | None = None) -> list[str]:
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
        problems.extend(f"path {index}->{index + 1}: {hit}" for hit in hits)
    return problems
