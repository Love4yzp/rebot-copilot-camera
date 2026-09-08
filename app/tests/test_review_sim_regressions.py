"""Regression coverage for self-driven SimArm handoff timing."""

import pytest
import time

from backend.arm.limits import LIMIT_TOLERANCE_RAD as ARM_LIMIT_TOLERANCE_RAD
from backend.arm.sim import SimArm
from backend.actions import InlineRunner
from backend.core import Broadcaster, Controller
from backend.sequences import Pose
from backend.safety.kinematics import LIMIT_TOLERANCE_RAD
from backend.safety import SafetyLatch
from backend.shutter import SimShutter


class Clock:
    now = 0.0

    def __call__(self) -> float:
        return self.now


def test_self_driven_commit_starts_at_current_clock_after_prepare_delay():
    clock = Clock()
    arm = SimArm(("joint1",), clock=clock, tau=0.05, self_driven=True)

    candidate = arm.prepare_move({"joint1": 1.0}, 1.0)
    clock.now = 0.5
    arm.commit_move(candidate)

    state = arm.read_state()
    assert state.t == pytest.approx(0.5)
    assert state.positions["joint1"] == pytest.approx(0.0)

    clock.now = 1.0
    position = arm.read_state().positions["joint1"]
    assert 0.0 < position < 1.0
    clock.now = 1.5
    arm.read_state()
    assert arm._motion_started_at == pytest.approx(0.5)
    assert arm._q_target["joint1"] == pytest.approx(
        arm._motion.profiles["joint1"].eval(1.0)[0]
    )


def test_self_driven_delayed_handoff_rejects_stale_candidate_and_keeps_old_motion():
    clock = Clock()
    arm = SimArm(("joint1",), clock=clock, tau=0.05, self_driven=True)
    arm.move_to({"joint1": 0.5}, 2.0)
    candidate = arm.prepare_move({"joint1": 0.9}, 2.0)

    clock.now = 0.5
    arm.read_state()  # A real reference publication invalidates the handoff.
    with pytest.raises(RuntimeError, match="stale"):
        arm.commit_move(candidate)

    assert arm._motion is not None
    assert arm._motion.target["joint1"] == pytest.approx(0.5)
    clock.now = 2.5
    position = arm.read_state().positions["joint1"]
    assert 0.0 < position < 0.5
    assert 0.0 < arm._q_target["joint1"] < 0.5


def test_manual_mode_commit_keeps_injected_time_semantics():
    clock = Clock()
    arm = SimArm(("joint1",), clock=clock)
    candidate = arm.prepare_move({"joint1": 1.0}, 1.0)
    clock.now = 0.5
    arm.commit_move(candidate)

    assert arm.read_state().t == pytest.approx(0.0)
    assert arm.read_state().positions["joint1"] == pytest.approx(0.0)
    arm.step(0.5)
    assert arm.read_state().positions["joint1"] > 0.0


def test_kinematics_reexports_the_shared_limit_tolerance():
    assert LIMIT_TOLERANCE_RAD == ARM_LIMIT_TOLERANCE_RAD


def test_real_clock_self_driven_retargets_do_not_self_stale():
    arm = SimArm(("joint1",), clock=time.monotonic, self_driven=True)
    arm.move_to({"joint1": 0.1}, 1.0)
    arm.move_to({"joint1": 0.2}, 1.0)
    arm.move_to({"joint1": 0.3}, 1.0)

    assert arm._motion is not None
    assert arm._motion.target["joint1"] == pytest.approx(0.3)


def test_real_clock_controller_goto_retargets_continuously():
    clock = time.monotonic
    arm = SimArm(("joint1",), clock=clock, self_driven=True)
    arm.connect()
    latch = SafetyLatch(clock=clock)
    controller = Controller(
        arm=arm,
        shutter=SimShutter(),
        latch=latch,
        broadcaster=Broadcaster(),
        clock=clock,
        actions=InlineRunner([]),
    )
    controller.preflight_path = lambda samples: []  # type: ignore[method-assign]

    controller.goto(Pose(name="one", joints={"joint1": 0.1}))
    controller.goto(Pose(name="two", joints={"joint1": 0.2}))
    controller.goto(Pose(name="three", joints={"joint1": 0.3}))

    assert arm._motion is not None
    assert arm._motion.target["joint1"] == pytest.approx(0.3)


def test_self_driven_reference_progress_still_makes_candidate_stale():
    clock = Clock()
    arm = SimArm(("joint1",), clock=clock, self_driven=True)
    arm.move_to({"joint1": 0.5}, 2.0)
    candidate = arm.prepare_move({"joint1": 0.9}, 2.0)

    clock.now = 0.1
    arm.read_state()
    with pytest.raises(RuntimeError, match="stale"):
        arm.commit_move(candidate)


def test_long_self_driven_commit_delay_does_not_complete_new_motion():
    clock = Clock()
    arm = SimArm(("joint1",), clock=clock, self_driven=True)
    arm.hold({"joint1": 0.8})
    candidate = arm.prepare_move({"joint1": 0.2}, 2.0)

    clock.now = 10.0
    arm.commit_move(candidate)
    assert arm._motion_started_at == pytest.approx(10.0)
    assert arm.read_state().positions["joint1"] == pytest.approx(0.8, abs=1e-6)
    assert arm.read_state().positions["joint1"] != pytest.approx(0.2)
