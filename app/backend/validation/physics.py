"""Offline validation adapters over the SDK's MuJoCo plant."""

import json
from pathlib import Path
from reBotArm_control_py.physics import (
    MitCommand as MitCommand,
    PhysicsPlant as PhysicsPlant,
    ModelBundle as ModelBundle,
)
from reBotArm_control_py.physics import (
    RecordingTransport as _RecordingTransport,
    load_model as _load_model,
)
from .. import assets

JOINTS = tuple(assets.arm_joint_names())
ALL_JOINTS = tuple(assets.joint_names())


class RecordingTransport(_RecordingTransport):
    def __init__(self, q0=None, clock=None):
        super().__init__(q0, clock, joint_names=ALL_JOINTS, arm_joint_names=JOINTS)


def load_model():
    return _load_model(
        urdf_path=str(assets.urdf_path()),
        arm_joint_names=JOINTS,
        fixed_joint_names=("joint_left", "joint_right"),
    )


def write_json(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
