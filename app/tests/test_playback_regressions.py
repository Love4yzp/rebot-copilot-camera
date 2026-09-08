"""Regression contracts for playback discontinuities.

These tests intentionally describe the fixed behaviour and therefore fail on
the pre-fix baseline.  They use only injected clocks and local transport
doubles; no CAN device or physics runtime is involved.
"""

import numpy as np
import pytest

from backend import assets
from backend.actions import InlineRunner
from backend.arm import SimArm
from backend.arm.session import ArmSession
from backend.core import Phase, SequenceExecutor
from backend.sequences import HoldBlock, Pose, Sequence, TransitionBlock
from backend.safety import LatchSource, SafetyLatch, Watchdog


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class _Group:
    def __init__(self, names: list[str]) -> None:
        self.joint_names = names
        self.mit: list[dict] = []

    def send_mit(self, pos, vel=None, kp=None, kd=None, tau=None) -> None:
        self.mit.append({
            "pos": np.asarray(pos, dtype=float).copy(),
            "vel": np.asarray(vel, dtype=float).copy(),
            "kp": np.asarray(kp, dtype=float).copy(),
            "kd": np.asarray(kd, dtype=float).copy(),
            "tau": np.asarray(tau, dtype=float).copy(),
        })


class _Transport:
    def __init__(self, names: tuple[str, ...]) -> None:
        self._names = list(names)
        arm_names = [n for n in names if n != "gripper"]
        self.groups = {
            "arm": _Group(arm_names),
            "gripper": _Group(["gripper"]) if "gripper" in names else _Group([]),
        }
        self.q = np.zeros(len(names), dtype=float)

    @property
    def joint_names(self) -> list[str]:
        return self._names

    def get_state(self):
        return self.q.copy(), np.zeros_like(self.q), np.zeros_like(self.q)


def _session(clock: _Clock) -> tuple[ArmSession, _Transport]:
    transport = _Transport(tuple(assets.joint_names()))
    session = ArmSession(clock=clock, transport=transport)
    session._gravity_torque = lambda q: np.zeros_like(q)  # type: ignore[method-assign]
    return session, transport


def test_move_to_returns_the_accepted_physical_duration_and_streams_quintic_velocity():
    clock = _Clock()
    session, transport = _session(clock)
    target = {"joint1": 1.0}

    accepted = session.move_to(target, duration_s=2.0)
    assert accepted is not None
    assert accepted >= 7.5

    clock.now = accepted / 2.0
    repeated = session.move_to(target, duration_s=2.0)
    assert repeated == pytest.approx(accepted)
    sent = transport.groups["arm"].mit[-1]

    # The exact profile is an implementation detail.  Its observable contract
    # is rest-to-rest motion with a bounded, nonzero interior velocity.
    assert 0.0 < sent["pos"][0] < 1.0
    assert 0.0 < sent["vel"][0] <= 0.25 + 1e-9

    clock.now = accepted
    session.move_to(target, duration_s=2.0)
    arrived = transport.groups["arm"].mit[-1]
    assert arrived["pos"][0] == pytest.approx(1.0, abs=1e-8)
    assert arrived["vel"][0] == pytest.approx(0.0, abs=1e-8)


def test_partial_target_is_frozen_at_acceptance_and_does_not_restart_profile():
    clock = _Clock()
    session, transport = _session(clock)
    session.move_to({"joint1": 1.0}, duration_s=2.0)
    first = transport.groups["arm"].mit[-1]["pos"][0]

    # Feedback noise on an unspecified channel must not make the same command
    # a new target and reset the active reference.
    transport.q[1] = 0.7
    clock.now = 0.5
    session.move_to({"joint1": 1.0}, duration_s=2.0)
    second = transport.groups["arm"].mit[-1]["pos"][0]
    assert first == pytest.approx(0.0)
    assert second > 0.0


def test_watchdog_engages_on_one_excessive_control_gap():
    clock = _Clock()
    latch = SafetyLatch(clock=clock)
    watchdog = Watchdog(latch, clock=clock)

    watchdog.observe_tick(0.01)
    clock.now = 0.11
    watchdog.observe_tick(0.01)

    assert latch.is_latched is True
    assert latch.snapshot().source is LatchSource.WATCHDOG
    assert "gap" in latch.snapshot().reason


def test_every_transition_is_stretched_to_the_profile_limits():
    class _RecordingSimArm(SimArm):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.move_calls: list[float] = []
            self.accepted_calls: list[float | None] = []

        def move_to(self, q_target, duration_s):
            self.move_calls.append(float(duration_s))
            accepted = super().move_to(q_target, duration_s)
            self.accepted_calls.append(accepted)
            return accepted

    clock = _Clock()
    arm = _RecordingSimArm(("joint1",), clock=clock, tau=0.05)
    arm.connect()
    first = Pose(name="first", joints={"joint1": 0.0})
    second = Pose(name="second", joints={"joint1": 2.0})
    sequence = Sequence(
        name="limits",
        blocks=[
            HoldBlock(pose_id=first.id, duration_s=0.01),
            TransitionBlock(duration_s=1.0),
            HoldBlock(pose_id=second.id, duration_s=0.01),
        ],
    )
    executor = SequenceExecutor(
        sequence,
        {first.id: first, second.id: second},
        arm=arm,
        actions=InlineRunner([]),
        clock=clock,
        settle_s=0.0,
    )
    executor.start()

    # Advance until the later transition is entered, then run to completion.
    for _ in range(4000):
        if executor.is_finished:
            break
        clock.now += 0.01
        arm.step(0.01)
        executor.tick()
    assert executor.phase is Phase.DONE

    # Quintic peak speed is 1.875 * displacement / duration.  With the
    # conservative .25 rad/s software ceiling, 2 rad needs at least 15 s.
    accepted_durations = [accepted for accepted in arm.accepted_calls if accepted is not None]
    assert accepted_durations
    assert max(accepted_durations) >= 15.0 - 1e-9
    assert arm.read_state().positions["joint1"] == pytest.approx(2.0, abs=0.02)


def test_executor_streams_holds_during_settling_and_after_arrival():
    class RecordingArm(SimArm):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.hold_calls = 0

        def hold(self, q_target):
            self.hold_calls += 1
            return super().hold(q_target)

    clock = _Clock()
    arm = RecordingArm(("joint1",), clock=clock, tau=0.05)
    arm.connect()
    first = Pose(name="first", joints={"joint1": 0.0})
    second = Pose(name="second", joints={"joint1": 0.1})
    sequence = Sequence(
        name="hold-stream",
        blocks=[HoldBlock(pose_id=first.id, duration_s=0.2), TransitionBlock(duration_s=0.1), HoldBlock(pose_id=second.id, duration_s=0.2)],
    )
    executor = SequenceExecutor(sequence, {first.id: first, second.id: second}, arm=arm, actions=InlineRunner([]), clock=clock, settle_s=0.0)
    executor.start()
    initial = arm.hold_calls
    for _ in range(30):
        clock.now += 0.01
        arm.step(0.01)
        executor.tick()
    assert arm.hold_calls > initial
    while not executor.is_finished:
        clock.now += 0.01
        arm.step(0.01)
        executor.tick()
    assert arm.hold_calls > initial + 1
