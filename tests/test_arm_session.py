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

    def __init__(self, name: str, names: list[str]) -> None:
        self.name = name
        self.joint_names = list(names)
        self.mit: list[dict] = []
        self.pos_vel: list[dict] = []
        self.mode = "mit"
        self.mode_calls: list[str] = []

    def mode_mit(self, kp=None, kd=None) -> None:
        self.mode = "mit"
        self.mode_calls.append("mit")

    def mode_pos_vel(self, vlim=None) -> None:
        self.mode = "pos_vel"
        self.mode_calls.append("pos_vel")

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
            "arm": _FakeGroup("arm", arm_names),
            "gripper": _FakeGroup("gripper", ["gripper"]) if "gripper" in names else _FakeGroup("gripper", []),
        }

    @property
    def joint_names(self) -> list[str]:
        return list(self._names)

    def get_state(self):
        z = np.zeros(len(self._names))
        return z, z.copy(), z.copy()


def test_move_is_a_mit_ramp_and_commands_stay_group_sized(monkeypatch):
    """Two real-arm facts the simulator never shows, pinned together:

    - The firmware latches its control mode at enable and ignores runtime
      switches (a post-enable mode_pos_vel left POS_VEL inert on the real
      arm), so a move is a MIT ramp interpolated by clock time — the
      executor re-issues move_to every tick and the setpoint advances.
    - Upstream's JointGroup indexes by its own joint list, so arrays must be
      sliced per group: full-arm arrays crash send_pos_vel and make the
      gripper read joint1's value.
    """
    t = [0.0]
    session = ArmSession(clock=lambda: t[0])
    arm = _FakeArm(session.joint_names)
    monkeypatch.setattr(session, "_arm", arm)

    q = {name: 0.1 * (i + 1) for i, name in enumerate(session.joint_names)}
    arm_group, grip_group = arm.groups["arm"], arm.groups["gripper"]

    session.move_to(q, duration_s=2.0)  # t=0: ramp start
    assert arm_group.mit[-1]["pos"][0] == pytest.approx(0.0, abs=1e-9)
    t[0] = 0.5
    session.move_to(q, duration_s=2.0)  # quarter: eased, below the linear 0.025
    assert arm_group.mit[-1]["pos"][0] == pytest.approx(0.1 * 0.15625, abs=1e-6)
    t[0] = 1.0
    session.move_to(q, duration_s=2.0)  # halfway
    assert arm_group.mit[-1]["pos"][0] == pytest.approx(0.05, abs=1e-9)
    t[0] = 2.0
    session.move_to(q, duration_s=2.0)  # arrived: setpoint = target
    assert arm_group.mit[-1]["pos"][0] == pytest.approx(0.1, abs=1e-9)

    # No runtime mode switching anywhere: MIT from connect to hold.
    assert arm_group.mode_calls == []
    assert grip_group.mode_calls == []

    session.hold(q)
    assert arm_group.mit[-1]["pos"].shape == (6,)
    assert grip_group.mit[-1]["pos"].shape == (1,)
    assert grip_group.mit[-1]["pos"][0] == pytest.approx(0.7)
    # The gripper carries no gravity feedforward — no calibrated mapping
    # exists from finger travel to motor torque, and zero is the honest value.
    assert grip_group.mit[-1]["tau"][0] == 0.0
