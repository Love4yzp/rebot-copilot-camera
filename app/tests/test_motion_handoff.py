"""Persistent tests for prepare/commit generation and controller transactions."""

import pytest

from backend.actions import InlineRunner, ShutterProvider
from backend.arm import SimArm
from backend.core import Broadcaster, Controller, Phase, SequenceExecutor
from backend.sequences import EventMarker, HoldBlock, Pose, Sequence, TransitionBlock
from backend.safety import SafetyLatch, Watchdog, WatchdogConfig
from backend.shutter import SimShutter


class Clock:
    now = 0.0
    def __call__(self): return self.now


def sim():
    c = Clock()
    arm = SimArm(("joint1",), clock=c)
    arm.connect()
    return c, arm


def test_prepared_candidate_is_stale_after_reference_progress_and_old_target_remains():
    c, arm = sim()
    arm.move_to({"joint1": 0.5}, 2.0)
    candidate = arm.prepare_move({"joint1": 0.9}, 2.0)
    c.now += 0.1
    arm.step(0.1)
    with pytest.raises(RuntimeError, match="stale"):
        arm.commit_move(candidate)
    assert arm._motion is not None
    assert arm._motion.target["joint1"] == pytest.approx(0.5)


def test_clock_only_delay_keeps_last_reference_for_handoff():
    c, arm = sim()
    arm.move_to({"joint1": 0.5}, 2.0)
    old = arm._reference["joint1"]
    candidate = arm.prepare_move({"joint1": 0.9}, 2.0)
    c.now += 10.0
    arm.commit_move(candidate)
    assert arm._reference["joint1"] == pytest.approx(old)


def test_session_handoff_reuses_last_mit_reference_after_clock_only_delay():
    from test_playback_regressions import _Clock, _session

    clock = _Clock()
    session, transport = _session(clock)
    accepted = session.move_to({"joint1": 0.5}, 2.0)
    clock.now = accepted / 2
    session.move_to({"joint1": 0.5}, 2.0)
    old = transport.groups["arm"].mit[-1]
    candidate = session.prepare_move({"joint1": 0.9}, 2.0)
    clock.now += 10.0
    session.commit_move(candidate)
    session.move_to({"joint1": 0.9}, 2.0)
    current = transport.groups["arm"].mit[-1]
    assert current["pos"] == pytest.approx(old["pos"])
    assert current["vel"] == pytest.approx(old["vel"])


def test_both_drivers_reject_candidate_after_float_reference_follow():
    from test_playback_regressions import _Clock, _session

    # SimArm's drag and ArmSession's measured transport state exercise the same
    # generation invalidation at the two hardware seams.
    c, arm = sim()
    arm.set_float(True)
    candidate = arm.prepare_move({"joint1": 0.5}, 2.0)
    arm.drag({"joint1": 0.2})
    arm.follow()
    with pytest.raises(RuntimeError, match="stale"):
        arm.commit_move(candidate)

    session_clock = _Clock()
    session, transport = _session(session_clock)
    session.set_float(True)
    candidate = session.prepare_move({"joint1": 0.5}, 2.0)
    transport.q[0] = 0.2
    session.follow()
    with pytest.raises(RuntimeError, match="stale"):
        session.commit_move(candidate)


def controller():
    c = Clock()
    arm = SimArm(("joint1",), clock=c)
    arm.connect()
    latch = SafetyLatch(clock=c)
    watchdog = Watchdog(latch, clock=c, config=WatchdogConfig(excessive_gap_s=0.1))
    return c, arm, Controller(
        arm=arm, shutter=SimShutter(), latch=latch, broadcaster=Broadcaster(),
        clock=c, watchdog=watchdog, actions=InlineRunner([ShutterProvider(SimShutter())]),
    )


def test_controller_preflight_rejection_preserves_old_executor_and_rest():
    c, arm, ctl = controller()
    old = ctl.goto(Pose(name="old", joints={"joint1": 0.2}))
    ctl.preflight_path = lambda samples: ["collision"]  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="preflight"):
        ctl.goto(Pose(name="new", joints={"joint1": 0.3}))
    assert ctl.executor is old
    assert not old.is_finished


def test_controller_slow_preflight_preserves_old_executor():
    c, arm, ctl = controller()
    old = ctl.goto(Pose(name="old", joints={"joint1": 0.2}))
    def slow(samples):
        c.now += 0.2
        return []
    ctl.preflight_path = slow  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="control gap"):
        ctl.goto(Pose(name="new", joints={"joint1": 0.3}))
    assert ctl.executor is old
    assert not old.is_finished


def test_controller_commit_failure_preserves_old_executor():
    c, arm, ctl = controller()
    old = ctl.goto(Pose(name="old", joints={"joint1": 0.2}))
    real_commit = arm.commit_move
    def fail(prepared):
        raise RuntimeError("commit failed")
    arm.commit_move = fail  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="commit failed"):
        ctl.goto(Pose(name="new", joints={"joint1": 0.3}))
    arm.commit_move = real_commit  # type: ignore[method-assign]
    assert ctl.executor is old
    assert not old.is_finished


def test_controller_rejected_prepare_does_not_wake_rest():
    c, arm, ctl = controller()
    ctl.tick()
    ctl.set_resting(True)
    assert ctl.activity.value == "rest"
    ctl.preflight_path = lambda samples: ["collision"]  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="preflight"):
        ctl.goto(Pose(name="new", joints={"joint1": 0.3}))
    assert ctl.activity.value == "rest"


def test_controller_goto_applies_first_approach_speed_limit_to_profile():
    c, arm, ctl = controller()
    ctl._tuning.approach.first_max_speed = 0.05
    ctl.goto(Pose(name="slow", joints={"joint1": 1.0}))
    assert arm._motion is not None
    assert arm._motion.profiles["joint1"].extrema()[0] <= 0.05 + 1e-8


def test_transition_wait_markers_pause_five_seconds_and_resume_in_order():
    from backend.actions import InlineRunner

    c, arm = sim()
    arm.hold_calls = 0
    real_hold = arm.hold
    def recording_hold(q):
        arm.hold_calls += 1
        return real_hold(q)
    arm.hold = recording_hold  # type: ignore[method-assign]
    first = Pose(name="first", joints={"joint1": 0.0})
    second = Pose(name="second", joints={"joint1": 0.3})
    markers = [
        EventMarker(kind="wait", params={}, at=0.0, estimate_s=0.0),
        EventMarker(kind="wait", params={}, at=0.5, estimate_s=0.0),
        EventMarker(kind="wait", params={}, at=1.0, estimate_s=0.0),
    ]
    sequence = Sequence(name="waits", blocks=[
        HoldBlock(pose_id=first.id, duration_s=0.1),
        TransitionBlock(duration_s=1.0, markers=markers),
        HoldBlock(pose_id=second.id, duration_s=0.1),
    ])
    executor = SequenceExecutor(sequence, {first.id: first, second.id: second}, arm=arm, actions=InlineRunner([]), clock=c, settle_s=0.0)
    executor.start()
    seen = []
    for expected in (0.0, 0.5, 1.0):
        for _ in range(500):
            if executor.phase is Phase.WAIT:
                break
            c.now += 0.01
            arm.step(0.01)
            executor.tick()
        else:
            pytest.fail(f"wait marker {expected} was not reached")
        seen.append(executor.progress().t_in_block)
        before = arm.hold_calls
        for _ in range(5):
            c.now += 1.0
            arm.step(1.0)
            executor.tick()
        assert arm.hold_calls > before
        assert executor.resume() is True
    for _ in range(2000):
        if executor.is_finished:
            break
        c.now += 0.01
        arm.step(0.01)
        executor.tick()
    else:
        pytest.fail("transition did not finish after final wait")
    assert executor.phase is Phase.DONE
    assert seen[0] <= seen[1] <= seen[2]
    assert seen[0] == pytest.approx(0.0, abs=0.02)
    assert seen[1] == pytest.approx(0.5, abs=0.02)
    assert seen[2] == pytest.approx(1.0, abs=0.02)
