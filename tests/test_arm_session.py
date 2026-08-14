"""ArmSession wiring against the real hardware config — no CAN bus involved.

Constructing the session only *parses* the config; ``connect()`` is what would
touch the bus. These tests pin the gripper switch end to end: the failure they
guard against is upstream registering a gripper motor that is not attached,
which is what a gripper-less device used to die on at startup.
"""

import numpy as np
import pytest

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


class _FakeGroup:
    """Captures what a real JointGroup would receive, sized by its joints."""

    def __init__(self, names: list[str]) -> None:
        self.joint_names = list(names)
        self.mit: list[dict] = []
        self.pos_vel: list[dict] = []

    def send_mit(self, pos, vel=None, kp=None, kd=None, tau=None) -> None:
        self.mit.append(
            {
                "pos": np.asarray(pos).copy(),
                "kp": None if kp is None else np.asarray(kp).copy(),
                "tau": None if tau is None else np.asarray(tau).copy(),
            }
        )

    def send_pos_vel(self, pos, vlim=None) -> None:
        self.pos_vel.append(
            {
                "pos": np.asarray(pos).copy(),
                "vlim": None if vlim is None else np.asarray(vlim).copy(),
            }
        )


class _FakeArm:
    """The slice of upstream RebotArm the session touches, with no bus."""

    def __init__(self, names: list[str]) -> None:
        self._names = list(names)
        arm_names = [n for n in names if n != "gripper"]
        self.groups = {
            "arm": _FakeGroup(arm_names),
            "gripper": _FakeGroup(["gripper"]) if "gripper" in names else _FakeGroup([]),
        }

    @property
    def joint_names(self) -> list[str]:
        return list(self._names)

    def get_state(self):
        z = np.zeros(len(self._names))
        return z, z.copy(), z.copy()


def test_commands_are_sized_to_each_group(monkeypatch):
    """The real arm pays for full-arm arrays where the simulator does not:
    upstream's JointGroup indexes by its own joint list, so send_pos_vel
    crashes with IndexError past the arm group's six joints, and send_mit
    would make the gripper read joint1's value. This pins the per-group
    slicing that fixes both."""
    session = ArmSession()
    arm = _FakeArm(session.joint_names)
    monkeypatch.setattr(session, "_arm", arm)

    q = {name: 0.1 * (i + 1) for i, name in enumerate(session.joint_names)}
    session.move_to(q, duration_s=2.0)

    arm_group, grip_group = arm.groups["arm"], arm.groups["gripper"]
    assert arm_group.pos_vel[-1]["pos"].shape == (6,)
    assert arm_group.pos_vel[-1]["pos"][0] == pytest.approx(0.1)
    assert grip_group.pos_vel[-1]["pos"].shape == (1,)
    assert grip_group.pos_vel[-1]["pos"][0] == pytest.approx(0.7)

    session.hold(q)
    assert arm_group.mit[-1]["pos"].shape == (6,)
    assert grip_group.mit[-1]["pos"].shape == (1,)
    assert grip_group.mit[-1]["pos"][0] == pytest.approx(0.7)
    # The gripper carries no gravity feedforward — no calibrated mapping
    # exists from finger travel to motor torque, and zero is the honest value.
    assert grip_group.mit[-1]["tau"][0] == 0.0
