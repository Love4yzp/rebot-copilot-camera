"""Regression coverage for the controller/executor safety handoff."""

import pytest


from backend.arm import SimArm
from backend.core import Broadcaster, Controller, Phase, SequenceExecutor
from backend.sequences import EventMarker, HoldBlock, Pose, Sequence, TransitionBlock
from backend.safety import LatchSource, SafetyLatch


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def rig():
    clock = Clock()
    arm = SimArm(("joint1", "joint2"), clock=clock, tau=0.05)
    arm.connect()
    latch = SafetyLatch(clock=clock)
    controller = Controller(
        arm=arm,
        latch=latch,
        broadcaster=Broadcaster(),
        clock=clock,
    )
    return clock, arm, latch, controller


def wait_sequence(pose: Pose | None = None):
    pose = pose or Pose(name="wait", joints={"joint1": 0.0, "joint2": 0.0})
    block = HoldBlock(
        pose_id=pose.id,
        duration_s=1.0,
        markers=[EventMarker(kind="wait", params={}, at=0.1, estimate_s=0.0)],
    )
    return Sequence(name="wait", blocks=[block]), {pose.id: pose}


def test_goto_rechecks_latch_after_preflight_and_keeps_old_executor():
    clock, arm, latch, controller = rig()
    old = controller.goto(Pose(name="old", joints={"joint1": 0.4, "joint2": 0.0}))

    def engage_during_preflight(_samples):
        latch.engage("stop during planning", LatchSource.API)
        return []

    controller.preflight_path = engage_during_preflight  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="emergency stop"):
        controller.goto(Pose(name="new", joints={"joint1": 0.8, "joint2": 0.0}))

    assert old.phase is not Phase.ABORTED
    assert controller.executor is old
    assert controller.executor is not None


def test_sequence_started_callback_cannot_send_after_latch_engages():
    _clock, arm, latch, controller = rig()
    old = controller.goto(Pose(name="old", joints={"joint1": 0.4, "joint2": 0.0}))

    def stop_on_event(_name, _data):
        latch.engage("stop from sequence event", LatchSource.API)

    controller.emit_event = stop_on_event  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="motion rejected"):
        controller.goto(Pose(name="new", joints={"joint1": 0.8, "joint2": 0.0}))

    assert old.phase is not Phase.ABORTED
    assert controller.executor is old


def test_wait_holds_executor_target_even_when_feedback_moves():
    clock, arm, _latch, controller = rig()
    sequence, poses = wait_sequence()
    controller.play(sequence, poses)
    for _ in range(100):
        clock.now += 0.01
        arm.step(0.01)
        controller.tick()
        if controller.executor and controller.executor.is_waiting:
            break
    assert controller.executor is not None and controller.executor.is_waiting
    target = dict(controller.executor.wait_hold_target or {})
    arm.drag({"joint1": 0.2})
    for _ in range(30):
        clock.now += 0.01
        arm.step(0.01)
        controller.tick()
    assert controller.executor.wait_hold_target == target
    assert arm.read_state().positions["joint1"] == pytest.approx(target["joint1"], abs=0.002)


def test_latched_resume_is_rejected_without_motion():
    clock, arm, latch, controller = rig()
    sequence, poses = wait_sequence()
    controller.play(sequence, poses)
    for _ in range(100):
        clock.now += 0.01
        arm.step(0.01)
        controller.tick()
        if controller.executor and controller.executor.is_waiting:
            break
    assert controller.executor is not None and controller.executor.is_waiting
    target = controller.executor.wait_hold_target
    latch.engage("stop", LatchSource.API)
    before = arm.read_state().positions
    assert controller.resume() is False
    assert controller.executor.phase is Phase.WAIT
    assert arm.read_state().positions == before
    assert controller.executor.wait_hold_target == target


def test_resume_prepare_rejection_keeps_wait_and_can_retry():
    clock = Clock()
    arm = SimArm(("joint1", "joint2"), clock=clock, tau=0.05)
    arm.connect()
    pose = Pose(name="target", joints={"joint1": 0.4, "joint2": 0.0})
    block = TransitionBlock(
        duration_s=1.0,
        markers=[EventMarker(kind="wait", params={}, at=0.5, estimate_s=0.0)],
    )
    executor = SequenceExecutor(
        Sequence(name="transition", blocks=[block]),
        {pose.id: pose},
        goto=pose,
        arm=arm,
        clock=clock,
    )
    executor.start()
    # The first tick reaches the wait marker after the transition clock.
    for _ in range(200):
        clock.now += 0.01
        arm.step(0.01)
        executor.tick()
        if executor.phase is Phase.WAIT:
            break
    assert executor.phase is Phase.WAIT
    progress = executor.progress().t_in_block
    assert executor.resume(before_motion=lambda: False) is False
    assert executor.phase is Phase.WAIT
    assert executor.progress().t_in_block == pytest.approx(progress)
    assert executor.resume(before_motion=lambda: True) is True
    assert executor.phase is Phase.TRANSITION


def _delay_prepare(arm, clock, delayed_call: int):
    real_prepare = arm.prepare_move
    calls = 0

    def prepare(*args, **kwargs):
        nonlocal calls
        calls += 1
        candidate = real_prepare(*args, **kwargs)
        if calls == delayed_call:
            clock.now += 20.0
        return candidate

    arm.prepare_move = prepare  # type: ignore[method-assign]


@pytest.mark.parametrize("kind", ["hold", "transition"])
def test_slow_prepare_starts_first_motion_clock_after_commit(kind):
    clock = Clock()
    arm = SimArm(("joint1", "joint2"), clock=clock, tau=0.05)
    arm.connect()
    target = Pose(name="target", joints={"joint1": 0.4, "joint2": 0.0})
    if kind == "hold":
        sequence = Sequence(
            name="first hold", blocks=[HoldBlock(pose_id=target.id, duration_s=1.0)]
        )
    else:
        sequence = Sequence(name="first transition", blocks=[TransitionBlock(duration_s=1.0)])
    _delay_prepare(arm, clock, delayed_call=1)
    executor = SequenceExecutor(
        sequence,
        {target.id: target},
        goto=target if kind == "transition" else None,
        arm=arm,
        clock=clock,
    )

    executor.start()
    assert clock.now == pytest.approx(20.0)
    assert executor.phase is (Phase.HOLD if kind == "hold" else Phase.TRANSITION)
    executor.tick()
    assert executor.phase is not Phase.ABORTED
    assert executor.progress().t_in_block == pytest.approx(0.0)


def test_slow_prepare_on_later_transition_does_not_abort_on_next_tick():
    clock = Clock()
    arm = SimArm(("joint1", "joint2"), clock=clock, tau=0.05)
    arm.connect()
    first = Pose(name="first", joints={"joint1": 0.15, "joint2": 0.0})
    second = Pose(name="second", joints={"joint1": 0.3, "joint2": 0.0})
    sequence = Sequence(
        name="later transition",
        blocks=[
            TransitionBlock(duration_s=0.4),
            HoldBlock(pose_id=first.id, duration_s=0.05),
            TransitionBlock(duration_s=0.4),
            HoldBlock(pose_id=second.id, duration_s=0.05),
        ],
    )
    # prepare calls: first transition, then the later transition.  The hold
    # begins at the already reached first pose and needs no new profile.
    _delay_prepare(arm, clock, delayed_call=2)
    executor = SequenceExecutor(
        sequence,
        {first.id: first, second.id: second},
        arm=arm,
        clock=clock,
        settle_s=0.0,
    )
    executor.start()

    for _ in range(1000):
        if executor.progress().block_index == 2:
            break
        clock.now += 0.01
        arm.step(0.01)
        executor.tick()
    assert executor.progress().block_index == 2
    assert executor.phase is Phase.TRANSITION
    assert clock.now >= 20.0
    executor.tick()
    assert executor.phase is Phase.TRANSITION
    assert executor.progress().t_in_block == pytest.approx(0.0)
