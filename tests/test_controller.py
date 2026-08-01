"""Control loop behaviour, especially the emergency-stop path.

This is where the latch, the executor and the arm finally meet, so this is
where "engage the stop mid-playback" is tested end to end rather than as three
separate units that each behave correctly on their own.
"""

import pytest

from backend.arm import SimArm
from backend.core import Broadcaster, Controller, Phase
from backend.routines import Routine, ShutterAction, Waypoint
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


def wp(j1: float, **kwargs) -> Waypoint:
    return Waypoint(joints={"joint1": j1, "joint2": 0.0}, **kwargs)


@pytest.fixture
def rig() -> Rig:
    return Rig()


# ── modes ────────────────────────────────────────────────────────────────────


def test_idle_by_default(rig: Rig):
    rig.step()
    assert rig.controller.mode == "idle"


def test_playback_runs_a_routine_to_completion(rig: Rig):
    rig.controller.play(Routine(name="x", waypoints=[wp(0.2, actions=[ShutterAction()]), wp(0.5)]))
    assert rig.controller.mode == "playback"

    rig.run_until_done()

    assert rig.controller.executor.phase is Phase.DONE
    assert rig.shutter.shots == 1
    assert rig.controller.mode == "idle"


def test_cannot_start_two_routines(rig: Rig):
    rig.controller.play(Routine(name="a", waypoints=[wp(0.9)]))
    with pytest.raises(RuntimeError, match="already playing"):
        rig.controller.play(Routine(name="b", waypoints=[wp(0.1)]))


def test_teaching_floats_the_arm_and_stops_playback(rig: Rig):
    rig.controller.play(Routine(name="x", waypoints=[wp(0.9)]))
    rig.step(3)

    rig.controller.set_teaching(True)

    assert rig.arm.is_floating is True
    assert rig.controller.mode == "teach"
    assert rig.controller.executor.phase is Phase.ABORTED


def test_a_floating_arm_is_not_commanded(rig: Rig):
    rig.controller.set_teaching(True)
    rig.arm.drag({"joint1": 0.4})
    rig.step(100)

    assert rig.arm.read_state().positions["joint1"] == pytest.approx(0.4)


# ── emergency stop ───────────────────────────────────────────────────────────


def test_stop_freezes_the_arm_where_it_was(rig: Rig):
    rig.controller.play(Routine(name="x", waypoints=[wp(1.0, duration_s=5)]))
    rig.step(20)

    rig.latch.engage("operator pressed stop", LatchSource.UI)
    rig.step()
    frozen = rig.arm.read_state().positions["joint1"]

    rig.step(500)

    assert rig.arm.read_state().positions["joint1"] == pytest.approx(frozen, abs=1e-6)
    assert 0 < frozen < 1.0, "should have stopped part-way, not at either end"


def test_the_freeze_pose_is_recorded_once_at_the_moment_the_loop_notices(rig: Rig):
    rig.controller.play(Routine(name="x", waypoints=[wp(1.0, duration_s=5)]))
    rig.step(20)

    rig.latch.engage("stop", LatchSource.API)
    rig.step()
    recorded = rig.latch.snapshot().freeze_pose

    rig.step(50)
    assert rig.latch.snapshot().freeze_pose == recorded


def test_stop_mid_playback_aborts_and_never_resumes(rig: Rig):
    """The one that matters.

    Engage mid-run: the routine stops, the arm holds, and clearing the stop
    leaves the system idle rather than picking up where it left off. By the
    time an operator clears a stop the scene has usually changed.
    """
    routine = Routine(
        name="multi-angle",
        waypoints=[wp(0.3, actions=[ShutterAction()]), wp(0.9, actions=[ShutterAction()])],
    )
    rig.controller.play(routine)
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
    rig.step()

    rig.latch.engage("stop", LatchSource.WATCHDOG)
    rig.step()

    assert rig.arm.is_floating is False
    assert rig.controller.is_teaching is False
    assert rig.controller.mode == "estop"

    rig.arm.drag({"joint1": 5.0})  # ignored: no longer floating
    rig.step(200)
    assert rig.arm.read_state().positions["joint1"] == pytest.approx(0.6, abs=1e-6)


def test_play_is_refused_while_stopped(rig: Rig):
    rig.latch.engage("stop", LatchSource.UI)
    with pytest.raises(RuntimeError, match="emergency stop"):
        rig.controller.play(Routine(name="x", waypoints=[wp(0.1)]))


def test_teaching_is_refused_while_stopped(rig: Rig):
    rig.latch.engage("stop", LatchSource.UI)
    with pytest.raises(RuntimeError, match="emergency stop"):
        rig.controller.set_teaching(True)


def test_mode_reports_estop_above_everything_else(rig: Rig):
    rig.controller.play(Routine(name="x", waypoints=[wp(0.9)]))
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
    rig.controller.play(Routine(name="x", waypoints=[wp(1.0, duration_s=5)]))
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
    assert estop == {"latched": True, "reason": "joint2 stalled", "source": "watchdog"}


def test_playback_progress_is_published(rig: Rig):
    rig.controller.play(Routine(name="x", waypoints=[wp(0.2)]))
    rig.run_until_done()

    phases = [m["data"]["phase"] for m in rig.published if m["type"] == "playback"]
    assert "moving" in phases
    assert phases[-1] == "done"
