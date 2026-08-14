"""ArmSession wiring against the real hardware config — no CAN bus involved.

Constructing the session only *parses* the config; ``connect()`` is what would
touch the bus. These tests pin the gripper switch end to end: the failure they
guard against is upstream registering a gripper motor that is not attached,
which is what a gripper-less device used to die on at startup.
"""

from backend import assets
from backend.arm.session import ArmSession


def test_session_reports_six_joints_without_the_gripper(monkeypatch):
    cfg = {**assets.hardware_config(), "gripper": False}
    monkeypatch.setattr(assets, "hardware_config", lambda: cfg)
    session = ArmSession()
    assert list(session.joint_names) == [
        "joint1",
        "joint2",
        "joint3",
        "joint4",
        "joint5",
        "joint6",
    ]


def test_session_reports_the_gripper_when_enabled(monkeypatch):
    cfg = {**assets.hardware_config(), "gripper": True}
    monkeypatch.setattr(assets, "hardware_config", lambda: cfg)

    session = ArmSession()
    assert list(session.joint_names)[-1] == "gripper"
