"""Control loop behaviour, especially the emergency-stop path.

This is where the latch, the executor and the arm finally meet, so this is
where "engage the stop mid-playback" is tested end to end rather than as three
separate units that each behave correctly on their own.
"""

import pytest

from backend.actions import InlineRunner, ShutterProvider
from backend.arm import SimArm
from backend.core import Broadcaster, Controller, Phase
from backend.sequences import EventMarker, HoldBlock, Pose, Sequence, TransitionBlock
from backend.safety import LatchSource, SafetyLatch, Watchdog
from backend.shutter import SimShutter

JOINTS = ("joint1", "joint2")
DT = 0.01


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class Rig:
    def __init__(self, watchdog: bool = False) -> None:
        self.clock = FakeClock()
        self.arm = SimArm(JOINTS, clock=self.clock, tau=0.05)
        self.arm.connect()
        self.shutter = SimShutter()
        self.latch = SafetyLatch(clock=self.clock)
        self.broadcaster = Broadcaster()
        self.published: list = []
        self.broadcaster.publish = self.published.append  # type: ignore[method-assign]
        self.watchdog = Watchdog(self.latch, clock=self.clock) if watchdog else None
        self.controller = Controller(
            arm=self.arm,
            shutter=self.shutter,
            latch=self.latch,
            broadcaster=self.broadcaster,
            clock=self.clock,
            watchdog=self.watchdog,
            expected_period_s=DT,
            # Inline, so a fake clock and real worker threads never race. The
            # loop-stays-free property is the subject of test_action_runner.py.
            actions=InlineRunner([ShutterProvider(self.shutter)]),
        )

    def step(self, n: int = 1) -> None:
        for _ in range(n):
            self.clock.now += DT
            self.arm.step(DT)
            self.controller.tick()

    def run_until_done(self, max_steps: int = 5000) -> None:
        for _ in range(max_steps):
            if not self.controller.is_playing:
                return
            self.step()
        raise AssertionError("playback never finished")


def seq(*angles: float, name: str = "x", shutter: bool = False) -> tuple[Sequence, dict]:
    """A hold per angle, transitions between, optionally a shutter marker each."""
    poses: dict[str, Pose] = {}
    blocks = []
    for i, q in enumerate(angles):
        p = Pose(name=f"p{i + 1}", joints={"joint1": q, "joint2": 0.0})
        poses[p.id] = p
        if i:
            blocks.append(TransitionBlock(duration_s=1.0))
        markers = [
            EventMarker(
                kind="shutter",
                params={"count": 1, "interval_s": 0.0, "focus_first": True},
                at=0.1,
            )
        ] if shutter else []
        blocks.append(HoldBlock(pose_id=p.id, duration_s=0.3, markers=markers))
    return Sequence(name=name, blocks=blocks), poses


@pytest.fixture
def rig() -> Rig:
    return Rig()


# ── modes ────────────────────────────────────────────────────────────────────


def test_idle_by_default(rig: Rig):
    rig.step()
    assert rig.controller.mode == "idle"


def test_playback_runs_a_sequence_to_completion(rig: Rig):
    sequence, poses = seq(0.2, 0.5, shutter=True)
    rig.controller.play(sequence, poses)
    assert rig.controller.mode == "playback"

    rig.run_until_done()

    assert rig.controller.executor.phase is Phase.DONE
    assert rig.shutter.shots == 2
    assert rig.controller.mode == "idle"


def test_cannot_start_two_sequences(rig: Rig):
    a, aposes = seq(0.9, name="a")
    b, bposes = seq(0.1, name="b")
    rig.controller.play(a, aposes)
    with pytest.raises(RuntimeError, match="already executing"):
        rig.controller.play(b, bposes)


def test_teaching_is_refused_while_a_sequence_is_playing(rig: Rig):
    """A floating arm and a moving target in the same loop is the mock's 409 —
    the operator stops the run first, deliberately."""
    sequence, poses = seq(0.9)
    rig.controller.play(sequence, poses)
    rig.step(3)

    with pytest.raises(RuntimeError, match="cannot teach"):
        rig.controller.set_teaching(True)

    assert rig.controller.mode == "playback"
    assert not rig.controller.executor.is_finished, "the run was not sacrificed"


def test_teaching_starts_locked_and_floats_once_pushed(rig: Rig):
    """The arm must not go slack the moment teaching is enabled — nobody is
    holding it yet. It floats when the loop sees it actually moving."""
    rig.controller.set_teaching(True)
    rig.step(3)
    assert rig.arm.is_floating is False, "went slack before anyone touched it"

    rig.arm.drag({"joint1": 0.05})
    rig.step(2)
    assert rig.arm.is_floating is True, "did not release when pushed"


def test_letting_go_locks_the_arm_where_it_was_left(rig: Rig):
    rig.controller.set_teaching(True)
    for _ in range(20):
        rig.arm.drag({"joint1": 0.02})
        rig.step()
    assert rig.arm.is_floating is True

    # Hand off: no more drag, so velocity decays and the decider locks.
    rig.step(200)
    assert rig.arm.is_floating is False

    settled = rig.arm.read_state().positions["joint1"]
    rig.step(500)
    assert rig.arm.read_state().positions["joint1"] == pytest.approx(settled, abs=1e-3)
    assert settled > 0.3, "should have stayed where it was dragged"


def test_a_floating_arm_is_followed_every_tick(rig: Rig):
    """Float is a command stream, not silence. MIT mode executes the last
    command it received, so a loop that goes quiet while "floating" leaves
    the motors holding the stale lock setpoint — the arm pulls back toward
    where it locked, which an operator reads as "it keeps lifting"."""
    calls = 0
    real_follow = rig.arm.follow

    def spy() -> None:
        nonlocal calls
        calls += 1
        real_follow()

    rig.arm.follow = spy  # type: ignore[method-assign]

    rig.controller.set_teaching(True)
    rig.arm.drag({"joint1": 0.4})
    rig.step(2)
    assert rig.arm.is_floating is True

    before = calls
    rig.step(3)
    assert calls - before == 3, "every floating tick must stream a follow command"

    # ...and the observable behaviour is unchanged: the arm stays where put.
    settled = rig.arm.read_state().positions["joint1"]
    rig.step(3)
    assert rig.arm.read_state().positions["joint1"] == pytest.approx(settled)


# ── emergency stop ───────────────────────────────────────────────────────────


def test_stop_freezes_the_arm_where_it_was(rig: Rig):
    sequence, poses = seq(1.0)
    # A long first approach so the arm is mid-move when the stop lands.
    sequence.blocks[0].duration_s = 5.0
    rig.controller.play(sequence, poses)
    rig.step(20)

    rig.latch.engage("operator pressed stop", LatchSource.UI)
    rig.step()
    frozen = rig.arm.read_state().positions["joint1"]

    rig.step(500)

    assert rig.arm.read_state().positions["joint1"] == pytest.approx(frozen, abs=1e-6)
    assert 0 < frozen < 1.0, "should have stopped part-way, not at either end"


def test_the_freeze_pose_is_recorded_once_at_the_moment_the_loop_notices(rig: Rig):
    sequence, poses = seq(1.0)
    rig.controller.play(sequence, poses)
    rig.step(20)

    rig.latch.engage("stop", LatchSource.API)
    rig.step()
    recorded = rig.latch.snapshot().freeze_pose

    rig.step(50)
    assert rig.latch.snapshot().freeze_pose == recorded


def test_stop_mid_playback_aborts_and_never_resumes(rig: Rig):
    """The one that matters.

    Engage mid-run: the sequence stops, the arm holds, and clearing the stop
    leaves the system idle rather than picking up where it left off. By the
    time an operator clears a stop the scene has usually changed.
    """
    sequence, poses = seq(0.3, 0.9, name="multi-angle", shutter=True)
    rig.controller.play(sequence, poses)
    rig.step(10)

    rig.latch.engage("operator pressed stop", LatchSource.UI)
    rig.step()

    assert rig.controller.executor.phase is Phase.ABORTED
    assert rig.controller.executor.error == "emergency stop engaged"
    shots_at_stop = rig.shutter.shots
    frozen = rig.arm.read_state().positions["joint1"]

    rig.step(1000)
    assert rig.shutter.shots == shots_at_stop
    assert rig.arm.read_state().positions["joint1"] == pytest.approx(frozen, abs=1e-6)

    rig.latch.clear()
    rig.step(1000)

    assert rig.controller.mode == "idle", "must not resume on clear"
    assert rig.controller.is_playing is False
    assert rig.shutter.shots == shots_at_stop
    assert rig.arm.read_state().positions["joint1"] == pytest.approx(frozen, abs=1e-6)


def test_stop_during_teaching_clamps_the_arm(rig: Rig):
    """A floating arm under an engaged stop is a dropped arm."""
    rig.controller.set_teaching(True)
    rig.arm.drag({"joint1": 0.6})
    rig.step(2)
    assert rig.arm.is_floating is True

    rig.latch.engage("stop", LatchSource.WATCHDOG)
    rig.step()

    assert rig.arm.is_floating is False
    assert rig.controller.is_teaching is False
    assert rig.controller.mode == "estop"

    frozen = rig.arm.read_state().positions["joint1"]
    rig.arm.drag({"joint1": 5.0})  # pushed, but held: it must come back
    rig.step(500)
    assert rig.arm.read_state().positions["joint1"] == pytest.approx(frozen, abs=1e-3)


def test_play_is_refused_while_stopped(rig: Rig):
    sequence, poses = seq(0.1)
    rig.latch.engage("stop", LatchSource.UI)
    with pytest.raises(RuntimeError, match="emergency stop"):
        rig.controller.play(sequence, poses)


def test_teaching_is_refused_while_stopped(rig: Rig):
    rig.latch.engage("stop", LatchSource.UI)
    with pytest.raises(RuntimeError, match="emergency stop"):
        rig.controller.set_teaching(True)


def test_mode_reports_estop_above_everything_else(rig: Rig):
    sequence, poses = seq(0.9)
    rig.controller.play(sequence, poses)
    rig.latch.engage("stop", LatchSource.UI)
    rig.step()
    assert rig.controller.mode == "estop"


def test_nothing_in_the_backend_ever_disables_the_motors():
    """Guard on the project's sharpest edge.

    Upstream's ``RebotArm.estop()`` is one line forwarding to ``disable_all()``,
    and MotorBridge documents ``disable_all()`` as "Emergency stop all motors".
    Both cut torque, and a 48 V arm holding a camera drops when torque goes.
    This project's stop holds instead.

    Checked over the AST rather than the text, so the explanatory comments that
    have to name these methods do not trip it, and so an attribute access is
    what is actually detected.
    """
    import ast
    from pathlib import Path

    banned = {"disable_all", "estop"}
    offenders = []

    backend = Path(__file__).resolve().parent.parent / "backend"
    for path in sorted(backend.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in banned:
                offenders.append(f"{path.relative_to(backend.parent)}:{node.lineno} .{node.attr}")

    assert not offenders, (
        "these cut motor torque and drop the arm; this project's emergency stop "
        f"holds position instead (see docs/HARDWARE_NOTES.md): {offenders}"
    )


# ── watchdog integration ─────────────────────────────────────────────────────


def test_a_failing_arm_read_does_not_kill_the_loop():
    """CAN drops frames. One bad read must not stop the loop holding the arm."""
    rig = Rig(watchdog=True)
    calls = {"n": 0}
    real_read = rig.arm.read_state

    def flaky():
        calls["n"] += 1
        if calls["n"] % 10 == 0:
            raise OSError("CAN read failed")
        return real_read()

    rig.arm.read_state = flaky  # type: ignore[method-assign]
    rig.step(100)

    assert rig.latch.is_latched is False
    assert rig.controller.mode == "idle"


def test_persistent_read_failure_engages_the_stop():
    """A run of them means the loop no longer knows where the arm is."""
    rig = Rig(watchdog=True)

    def always_fails():
        raise OSError("CAN bus down")

    rig.arm.read_state = always_fails  # type: ignore[method-assign]
    rig.step(20)

    assert rig.latch.is_latched is True
    assert rig.latch.snapshot().source is LatchSource.WATCHDOG
    assert "read failures" in rig.latch.snapshot().reason


def test_drift_is_not_judged_against_a_moving_target():
    """During playback a large error is the whole point of moving."""
    rig = Rig(watchdog=True)
    sequence, poses = seq(1.0)
    sequence.blocks[0].duration_s = 5.0
    rig.controller.play(sequence, poses)
    rig.step(300)

    assert rig.latch.is_latched is False


def test_clearing_a_stop_resets_accumulated_suspicion():
    rig = Rig(watchdog=True)
    rig.latch.engage("operator stop", LatchSource.UI)
    rig.step(5)
    rig.latch.clear()
    rig.step(5)

    assert rig.latch.is_latched is False


# ── broadcast ────────────────────────────────────────────────────────────────


def test_state_is_published_every_tick(rig: Rig):
    rig.step(3)
    states = [m for m in rig.published if m["type"] == "state"]
    assert len(states) == 3
    assert set(states[0]["data"]) >= {"positions", "velocities", "rate_hz", "mode", "estop"}


def test_published_state_carries_the_stop_reason(rig: Rig):
    rig.latch.engage("joint2 stalled", LatchSource.WATCHDOG)
    rig.step()

    estop = [m for m in rig.published if m["type"] == "state"][-1]["data"]["estop"]
    assert estop["latched"] is True
    assert estop["reason"] == "joint2 stalled"
    assert estop["source"] == "watchdog"


def test_playback_progress_is_published(rig: Rig):
    sequence, poses = seq(0.2, 0.5)
    rig.controller.play(sequence, poses)
    rig.run_until_done()

    phases = [m["data"]["phase"] for m in rig.published if m["type"] == "playback"]
    assert "hold" in phases
    assert "transition" in phases
    assert phases[-1] == "done"


def test_the_progress_payload_is_the_seq_playback_shape(rig: Rig):
    sequence, poses = seq(0.2, name="wire check")
    rig.controller.play(sequence, poses)
    rig.run_until_done()

    last = [m for m in rig.published if m["type"] == "playback"][-1]["data"]
    assert set(last) == {
        "sequence_id", "sequence_name", "block_index", "block_total",
        "phase", "t_in_block", "error", "finished", "approaching",
    }
    assert last["sequence_id"] == sequence.id
    assert last["sequence_name"] == "wire check"
    assert last["finished"] is True
    assert last["block_index"] == last["block_total"]


def test_a_finished_run_keeps_broadcasting_done_but_a_stop_goes_null(rig: Rig):
    """The UI relies on the lingering done to keep saying 到位; an explicit
    stop clears the progress instead."""
    sequence, poses = seq(0.2)
    rig.controller.play(sequence, poses)
    rig.run_until_done()
    rig.step()
    states = [m for m in rig.published if m["type"] == "state"]
    assert states[-1]["data"]["playback"]["phase"] == "done"

    other, oposes = seq(0.9)
    rig.controller.play(other, oposes)
    rig.step(3)
    rig.controller.stop_playback()
    rig.step()
    states = [m for m in rig.published if m["type"] == "state"]
    assert states[-1]["data"]["playback"] is None
