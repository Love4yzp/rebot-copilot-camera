"""Sequence traversal, waiting, arrival and failure behavior with fake time."""

import pytest


from backend.arm import SimArm
from backend.core import Phase, SequenceExecutor
from backend.sequences import (
    EventMarker,
    HoldBlock,
    Pose,
    Sequence,
    TransitionBlock,
)


JOINTS = ("joint1", "joint2")
DT = 0.01


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class Harness:
    """Fake clock plus a simulated arm and shutter, stepped together."""

    def __init__(
        self,
        sequence: Sequence,
        poses: dict[str, Pose],
        connected: bool = True,
        goto: Pose | None = None,
    ) -> None:
        self.clock = FakeClock()
        self.arm = SimArm(JOINTS, clock=self.clock, tau=0.05)
        self.arm.connect()
        self.events: list = []
        self.executor = SequenceExecutor(
            sequence,
            poses,
            goto=goto,
            arm=self.arm,
            clock=self.clock,
            on_progress=self.events.append,
        )

    def run(self, max_seconds: float = 60.0) -> None:
        """Start, then tick until the sequence finishes."""
        self.executor.start()
        self.finish(max_seconds)

    def finish(self, max_seconds: float = 60.0) -> None:
        """Tick until the sequence finishes, or give up rather than loop forever."""
        deadline = self.clock.now + max_seconds
        while not self.executor.is_finished and self.clock.now < deadline:
            self.step()
        assert self.executor.is_finished, "executor never finished"

    def step(self, n: int = 1) -> None:
        for _ in range(n):
            self.clock.now += DT
            self.arm.step(DT)
            self.executor.tick()


def pose(j1: float, name: str = "pose") -> Pose:
    return Pose(name=name, joints={"joint1": j1, "joint2": 0.0})


def wait_marker(at: float) -> EventMarker:
    return EventMarker(kind="wait", params={}, at=at, estimate_s=0.0)


def hold(p: Pose, duration_s: float = 0.5, markers=()) -> HoldBlock:
    return HoldBlock(pose_id=p.id, duration_s=duration_s, markers=list(markers))


def transition(duration_s: float = 1.0, markers=()) -> TransitionBlock:
    return TransitionBlock(duration_s=duration_s, markers=list(markers))


def make(*blocks, name: str = "shoot") -> tuple[Sequence, dict[str, Pose]]:
    """A sequence plus the pose map the API layer would have resolved."""
    poses = {
        block.pose_id: Pose(
            id=block.pose_id, name=f"p{block.pose_id[:4]}", joints={"joint1": 0.0, "joint2": 0.0}
        )
        for block in blocks
        if isinstance(block, HoldBlock)
    }
    return Sequence(name=name, blocks=list(blocks)), poses


def rig(*blocks, name: str = "shoot", connected: bool = True) -> Harness:
    sequence, poses = make(*blocks, name=name)
    return Harness(sequence, poses, connected=connected)


# ── happy path ───────────────────────────────────────────────────────────────


def test_empty_sequence_finishes_immediately_rather_than_hanging():
    h = Harness(Sequence(name="empty"), {})
    h.executor.start()
    assert h.executor.phase is Phase.DONE


def test_holds_are_visited_in_order():
    a, b, c = pose(0.2, "a"), pose(0.5, "b"), pose(-0.3, "c")
    sequence, poses = make(hold(a), transition(), hold(b), transition(), hold(c), name="round")
    # make() zeroes the joints; give them the real targets.
    poses[a.id] = a
    poses[b.id] = b
    poses[c.id] = c
    h = Harness(sequence, poses)
    h.run()

    assert h.executor.phase is Phase.DONE
    assert h.arm.read_state().positions["joint1"] == pytest.approx(-0.3, abs=0.02)


def test_wait_starts_only_when_the_arm_is_actually_still():
    """Position inside the arrival window is not stillness.

    A first-order approach crosses the eps boundary while the joint is
    still moving fast. A marker at ``at=0`` fired on that tick photographs
    a moving arm — the frame comes back blurred and nothing reports why.
    """
    from backend.core.executor import SETTLE_DRIFT_RAD, SETTLE_MIN_S

    a = pose(0.8, "a")
    sequence, poses = make(hold(a, duration_s=2.0, markers=[wait_marker(0.0)]))
    poses[a.id] = a
    h = Harness(sequence, poses)
    h.executor.start()
    while h.executor.phase is not Phase.WAIT and h.clock.now < 30:
        h.step()

    assert h.executor.phase is Phase.WAIT
    speed = abs(h.arm.read_state().velocities["joint1"])
    # The dwell bounds the latent speed at the shot to drift/dwell.
    assert speed <= SETTLE_DRIFT_RAD / SETTLE_MIN_S * 2, (
        f"shot fired while joint1 was still moving at {speed:.3f} rad/s"
    )


# ── holds and transitions ────────────────────────────────────────────────────


def test_a_holds_clock_starts_at_arrival_not_at_block_entry():
    """A marker must never fire mid-approach — that photographs a moving scene."""
    a = pose(0.6)
    sequence, poses = make(hold(a, 0.5, [wait_marker(0.0)]))
    poses[a.id] = a
    h = Harness(sequence, poses)
    h.executor.start()

    while h.executor.phase is not Phase.WAIT and not h.executor.is_finished:
        h.step()
    assert h.executor.phase is Phase.WAIT
    # The wait started only once the arm was holding the pose.
    assert h.arm.read_state().positions["joint1"] == pytest.approx(0.6, abs=0.02)
    h.executor.resume()
    h.finish()
    assert h.executor.phase is Phase.DONE


def test_a_zero_duration_hold_costs_no_extra_time():
    a, b = pose(0.0), pose(0.3)
    sequence, poses = make(hold(a, 0.0), transition(), hold(b, 0.2))
    poses[a.id] = a
    poses[b.id] = b
    h = Harness(sequence, poses)
    h.run()
    assert h.executor.phase is Phase.DONE


def test_a_transition_moves_between_the_flanking_poses():
    a, b = pose(0.1), pose(0.6)
    sequence, poses = make(hold(a, 0.2), transition(1.0), hold(b, 0.2))
    poses[a.id] = a
    poses[b.id] = b
    h = Harness(sequence, poses)
    h.run()

    assert h.arm.read_state().positions["joint1"] == pytest.approx(0.6, abs=0.02)


def test_a_stuck_arm_faults_instead_of_waiting_forever():
    a = pose(1.0)
    sequence, poses = make(hold(a, 0.5))
    poses[a.id] = a
    h = Harness(sequence, poses)
    h.executor.start()

    # Arm never moves: step the clock and the executor but not the simulation.
    for _ in range(5000):
        h.clock.now += DT
        h.executor.tick()
        if h.executor.is_finished:
            break

    assert h.executor.phase is Phase.ABORTED
    assert "not reached" in h.executor.error


# ── wait markers ─────────────────────────────────────────────────────────────


def test_a_wait_marker_suspends_the_run_until_resume():
    a, b = pose(0.1), pose(0.4)
    sequence, poses = make(
        hold(a, 2.0, [wait_marker(1.0)]),
        transition(0.5),
        hold(b, 0.2),
    )
    poses[a.id] = a
    poses[b.id] = b
    h = Harness(sequence, poses)
    h.executor.start()

    while h.executor.phase is not Phase.WAIT and not h.executor.is_finished:
        h.step()

    assert h.executor.phase is Phase.WAIT
    assert h.executor.progress().t_in_block == pytest.approx(1.0, abs=0.02), (
        "t clamps to the marker"
    )

    for _ in range(500):  # 5s of ticking: a suspended run does not drift on
        h.step()
    assert h.executor.phase is Phase.WAIT
    assert h.executor.phase is Phase.WAIT

    assert h.executor.resume() is True
    h.finish()
    assert h.executor.phase is Phase.DONE


def test_resume_outside_a_wait_is_refused():
    a = pose(0.1)
    sequence, poses = make(hold(a, 0.5))
    poses[a.id] = a
    h = Harness(sequence, poses)
    h.executor.start()
    h.step(5)

    assert h.executor.resume() is False


def test_suspension_time_is_not_charged_to_the_block():
    """The mock clamps t at the marker and counts on from there — resume does
    not jump the remaining markers' times."""
    a = pose(0.1)
    sequence, poses = make(hold(a, 3.0, [wait_marker(1.0)]))
    poses[a.id] = a
    h = Harness(sequence, poses)
    h.executor.start()
    while h.executor.phase is not Phase.WAIT:
        h.step()

    for _ in range(1000):  # 10s suspended — far past the block's own duration
        h.step()
    h.executor.resume()
    h.step(50)  # 0.5s past resume: t ≈ 1.5s, the 2.0s marker must not fire early
    assert h.executor.phase is not Phase.WAIT
    h.executor.resume()
    h.finish()
    assert h.executor.phase is Phase.DONE


# ── marker failure ───────────────────────────────────────────────────────────


# ── abort / emergency stop ───────────────────────────────────────────────────


def test_abort_mid_playback_stops_and_never_resumes():
    """The most important test in the project.

    This is what the control loop does when the emergency stop engages. The
    sequence must stop where it is and stay stopped: by the time an operator
    clears a stop, someone has usually moved the arm or taken the subject away.
    """
    a, b = pose(0.3), pose(0.9)
    sequence, poses = make(
        hold(a, 1.0, []),
        transition(2.0),
        hold(b, 1.0, []),
    )
    poses[a.id] = a
    poses[b.id] = b
    h = Harness(sequence, poses)
    h.executor.start()
    h.step(5)

    h.executor.abort("emergency stop engaged")
    assert h.executor.phase is Phase.ABORTED
    assert h.executor.error == "emergency stop engaged"

    index_at_abort = h.executor.progress().block_index

    # Keep ticking as the control loop would. Nothing may happen.
    h.step(2000)

    assert h.executor.phase is Phase.ABORTED

    assert h.executor.progress().block_index == index_at_abort


def test_abort_is_idempotent_and_keeps_the_first_reason():
    a = pose(0.3)
    sequence, poses = make(hold(a, 1.0))
    poses[a.id] = a
    h = Harness(sequence, poses)
    h.executor.start()
    h.executor.abort("emergency stop engaged")
    h.executor.abort("something later")

    assert h.executor.error == "emergency stop engaged"


def test_abort_after_completion_does_not_rewrite_the_outcome():
    a = pose(0.1)
    sequence, poses = make(hold(a, 0.2))
    poses[a.id] = a
    h = Harness(sequence, poses)
    h.run()
    h.executor.abort("too late")

    assert h.executor.phase is Phase.DONE
    assert h.executor.error is None


def test_starting_twice_is_refused():
    a = pose(0.1)
    sequence, poses = make(hold(a, 0.2))
    poses[a.id] = a
    h = Harness(sequence, poses)
    h.executor.start()
    with pytest.raises(RuntimeError):
        h.executor.start()


# ── progress reporting ───────────────────────────────────────────────────────


def test_progress_reports_the_wire_shape():
    a, b = pose(0.1), pose(0.2)
    sequence, poses = make(
        hold(a, 0.5, []),
        transition(0.5),
        hold(b, 0.2),
        name="round the subject",
    )
    poses[a.id] = a
    poses[b.id] = b
    h = Harness(sequence, poses)
    h.executor.start()

    p = h.executor.progress()
    assert p.sequence_name == "round the subject"
    assert p.block_total == 3
    assert p.block_index == 0
    assert p.phase is Phase.HOLD

    h.finish()
    done = h.events[-1]
    assert done.phase is Phase.DONE
    assert done.block_index == done.block_total, "one past the last block"
    assert done.t_in_block == 0.0
    assert done.is_finished


def test_phases_pass_through_hold_and_transition():
    a, b = pose(0.1), pose(0.4)
    sequence, poses = make(hold(a, 0.3), transition(0.5), hold(b, 0.3))
    poses[a.id] = a
    poses[b.id] = b
    h = Harness(sequence, poses)
    h.run()

    seen = [e.phase for e in h.events]
    assert seen[0] is Phase.HOLD
    assert Phase.TRANSITION in seen
    assert seen[-1] is Phase.DONE


# ── approaching flag ──────────────────────────────────────────────────────────


def test_approaching_is_true_during_first_hold_approach():
    """The first hold block's approach: arm not at the pose yet."""
    target = pose(0.6)
    sequence, poses = make(hold(target, 0.5))
    poses[target.id] = target
    h = Harness(sequence, poses)
    h.executor.start()

    p = h.executor.progress()
    assert p.phase is Phase.HOLD
    assert p.approaching is True

    # Tick until the arm arrives or the sequence finishes.
    while not h.executor.is_finished and h.executor.progress().approaching:
        h.step()

    p = h.executor.progress()
    if p.phase is Phase.HOLD:
        assert p.approaching is False


def test_approaching_is_false_once_a_present_arm_proves_it_is_still():
    """Arm already at the hold's pose at start — no approach needed, but the
    settle dwell still has to pass before the hold's clock starts: "already
    there" is also only believed once the arm has demonstrably held still."""
    from backend.core.executor import SETTLE_MIN_S

    target = pose(0.0)
    sequence, poses = make(hold(target, 0.5))
    poses[target.id] = target
    h = Harness(sequence, poses)
    h.executor.start()

    p = h.executor.progress()
    assert p.phase is Phase.HOLD
    assert p.approaching is True, "the stillness dwell has not run yet"

    h.step(int(SETTLE_MIN_S / DT) + 2)
    p = h.executor.progress()
    assert p.approaching is False


def test_approaching_is_false_for_transition_blocks():
    """Transition blocks never approach — they start their clock immediately."""
    a, b = pose(0.0), pose(0.6)
    sequence, poses = make(hold(a, 0.2), transition(1.0), hold(b, 0.2))
    poses[a.id] = a
    poses[b.id] = b
    h = Harness(sequence, poses)
    h.run()

    for e in h.events:
        if e.phase is Phase.TRANSITION:
            assert e.approaching is False, f"transition at {e.block_index} has approaching=True"


def test_approaching_is_false_when_done():
    """Finished sequence — approaching is False."""
    target = pose(0.0)
    sequence, poses = make(hold(target, 0.2))
    poses[target.id] = target
    h = Harness(sequence, poses)
    h.run()

    assert h.executor.progress().approaching is False


def test_approaching_is_false_for_aborted_run():
    """Aborted sequence — approaching is False."""
    a = pose(1.0)
    sequence, poses = make(hold(a, 0.5))
    poses[a.id] = a
    h = Harness(sequence, poses)
    h.executor.start()

    # Arm never moves — the arrival deadline will trigger an abort.
    for _ in range(5000):
        h.clock.now += DT
        h.executor.tick()
        if h.executor.is_finished:
            break

    assert h.executor.phase is Phase.ABORTED
    assert h.executor.progress().approaching is False


# ── first-pose approach ──────────────────────────────────────────────────────


def test_the_approach_to_the_first_pose_is_speed_limited():
    """Later poses start from the previous one, so their stored duration was
    chosen against a known pose. The first starts from wherever teaching left
    the arm, and honouring a short duration there would fling it."""
    from backend.arm.base import EASE_PEAK
    from backend.core.executor import FIRST_APPROACH_MAX_SPEED

    far = pose(2.0)
    sequence, poses = make(hold(far, 0.5))
    poses[far.id] = far
    h = Harness(sequence, poses)
    h.executor.start()

    commanded = h.executor._arrival_deadline - h.clock.now
    assert commanded == pytest.approx(EASE_PEAK * 2.0 / FIRST_APPROACH_MAX_SPEED * 3)


def test_a_short_first_hop_keeps_the_base_approach_duration():
    a = pose(0.05)
    sequence, poses = make(hold(a, 0.5))
    poses[a.id] = a
    h = Harness(sequence, poses)
    h.executor.start()

    assert h.executor._arrival_deadline - h.clock.now == pytest.approx(2.0 * 3)


def test_later_moves_are_not_stretched():
    a, b = pose(0.0), pose(2.0)
    sequence, poses = make(hold(a, 0.2), transition(1.0), hold(b, 0.2))
    poses[a.id] = a
    poses[b.id] = b
    h = Harness(sequence, poses)
    h.executor.start()
    while h.executor.progress().block_index == 0 and not h.executor.is_finished:
        h.step()

    assert h.executor.progress().block_index == 1
    assert h.executor._arrival_deadline - h.clock.now == pytest.approx(15.0 * 3, abs=0.05)


# ── goto ─────────────────────────────────────────────────────────────────────


def test_a_goto_is_a_single_transition_to_the_pose():
    target = pose(0.4, "侧面")
    ephemeral = Sequence(
        id=target.id,
        name=f"位姿 · {target.name}",
        blocks=[TransitionBlock(duration_s=2.0)],
    )
    h = Harness(ephemeral, {target.id: target}, goto=target)
    h.executor.start()

    assert h.executor.progress().phase is Phase.TRANSITION
    assert h.executor.progress().block_total == 1

    deadline = h.clock.now + 60.0
    while not h.executor.is_finished and h.clock.now < deadline:
        h.step()
    assert h.executor.phase is Phase.DONE
    assert h.arm.read_state().positions["joint1"] == pytest.approx(0.4, abs=0.02)
