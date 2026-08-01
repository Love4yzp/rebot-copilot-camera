"""RoutineExecutor timing and failure handling.

Driven entirely by a fake clock. No test here sleeps, and none needs hardware.
"""

import pytest

from backend.arm import SimArm
from backend.core import Phase, RoutineExecutor
from backend.routines import Routine, ShutterAction, SleepAction, Waypoint
from backend.shutter import ShutterNotConnected, ShutterTimeout, SimShutter

JOINTS = ("joint1", "joint2")
DT = 0.01


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class Harness:
    """Fake clock plus a simulated arm and shutter, stepped together."""

    def __init__(self, routine: Routine, connected: bool = True) -> None:
        self.clock = FakeClock()
        self.arm = SimArm(JOINTS, clock=self.clock, tau=0.05)
        self.arm.connect()
        self.shutter = SimShutter(connected=connected)
        self.events: list = []
        self.executor = RoutineExecutor(
            routine,
            arm=self.arm,
            shutter=self.shutter,
            clock=self.clock,
            on_progress=self.events.append,
        )

    def run(self, max_seconds: float = 30.0) -> None:
        """Tick until the routine finishes, or give up rather than loop forever."""
        deadline = self.clock.now + max_seconds
        self.executor.start()
        while not self.executor.is_finished and self.clock.now < deadline:
            self.step()
        assert self.executor.is_finished, "executor never finished"

    def step(self, n: int = 1) -> None:
        for _ in range(n):
            self.clock.now += DT
            self.arm.step(DT)
            self.executor.tick()


def wp(j1: float, **kwargs) -> Waypoint:
    return Waypoint(joints={"joint1": j1, "joint2": 0.0}, **kwargs)


def routine(*waypoints: Waypoint, name: str = "shoot") -> Routine:
    return Routine(name=name, waypoints=list(waypoints))


# ── happy path ───────────────────────────────────────────────────────────────


def test_empty_routine_finishes_immediately_rather_than_hanging():
    h = Harness(routine())
    h.executor.start()
    assert h.executor.phase is Phase.DONE


def test_waypoints_are_visited_in_order():
    h = Harness(routine(wp(0.2), wp(0.5), wp(-0.3)))
    h.run()

    assert h.executor.phase is Phase.DONE
    assert h.arm.read_state().positions["joint1"] == pytest.approx(-0.3, abs=0.02)


def test_every_shutter_action_fires():
    h = Harness(routine(wp(0.1, actions=[ShutterAction()]), wp(0.2, actions=[ShutterAction()])))
    h.run()

    assert h.shutter.shots == 2
    assert h.shutter.focuses == 2, "focus_first defaults on"


def test_focus_is_skipped_when_disabled():
    h = Harness(routine(wp(0.1, actions=[ShutterAction(focus_first=False)])))
    h.run()

    assert h.shutter.shots == 1
    assert h.shutter.focuses == 0


def test_actions_run_in_order_within_a_waypoint():
    h = Harness(routine(wp(0.1, actions=[SleepAction(duration_s=0.5), ShutterAction()])))
    h.executor.start()

    while h.executor.phase is not Phase.ACTING:
        h.step()
    assert h.shutter.shots == 0, "sleep must complete before the shutter fires"

    while not h.executor.is_finished:
        h.step()
    assert h.shutter.shots == 1


# ── timing ───────────────────────────────────────────────────────────────────


def test_settle_actually_waits_before_acting():
    """The arm reaching its target and being steady enough for a sharp frame
    are a few hundred milliseconds apart."""
    h = Harness(routine(wp(0.1, settle_ms=500, actions=[ShutterAction()])))
    h.executor.start()

    while h.executor.phase is Phase.MOVING:
        h.step()
    settle_started = h.clock.now

    while h.executor.phase is Phase.SETTLING:
        assert h.shutter.shots == 0, "fired mid-settle — the frame would be blurred"
        h.step()

    assert h.clock.now - settle_started >= 0.5


def test_zero_settle_does_not_cost_a_tick():
    h = Harness(routine(wp(0.1, settle_ms=0, actions=[ShutterAction()])))
    h.executor.start()
    while h.executor.phase is Phase.MOVING:
        h.step()

    assert h.executor.phase is not Phase.SETTLING


def test_sleep_action_waits_its_full_duration():
    h = Harness(routine(wp(0.1, settle_ms=0, actions=[SleepAction(duration_s=1.0)])))
    h.executor.start()
    while h.executor.phase is not Phase.ACTING:
        h.step()
    started = h.clock.now

    while not h.executor.is_finished:
        h.step()

    assert h.clock.now - started >= 1.0


def test_a_stuck_arm_faults_instead_of_waiting_forever():
    h = Harness(routine(wp(1.0, duration_s=1.0)))
    h.executor.start()

    # Arm never moves: step the clock and the executor but not the simulation.
    for _ in range(2000):
        h.clock.now += DT
        h.executor.tick()
        if h.executor.is_finished:
            break

    assert h.executor.phase is Phase.ABORTED
    assert "not reached" in h.executor.error


# ── action failure policies ──────────────────────────────────────────────────


def test_shutter_failure_aborts_the_routine_by_default():
    """A missed frame is not noticed until the whole set is reviewed."""
    h = Harness(routine(wp(0.1, actions=[ShutterAction()]), wp(0.5, actions=[ShutterAction()])))
    h.shutter.script([ShutterTimeout("camera asleep")])
    h.run()

    assert h.executor.phase is Phase.ABORTED
    assert "camera asleep" in h.executor.error
    assert h.shutter.shots == 0
    assert h.executor.progress().waypoint_index == 0, "did not move on"


def test_skip_policy_carries_on_to_the_next_waypoint():
    h = Harness(
        routine(
            wp(0.1, actions=[ShutterAction(on_failure="skip")]),
            wp(0.5, actions=[ShutterAction()]),
        )
    )
    h.shutter.script([ShutterTimeout("one bad frame")])
    h.run()

    assert h.executor.phase is Phase.DONE
    assert h.shutter.shots == 1, "second waypoint still fired"


def test_retry_policy_succeeds_on_the_second_attempt():
    h = Harness(routine(wp(0.1, actions=[ShutterAction(on_failure="retry", retries=2)])))
    h.shutter.script([ShutterTimeout("flaky"), None])
    h.run()

    assert h.executor.phase is Phase.DONE
    assert h.shutter.shots == 1


def test_retry_policy_gives_up_after_the_configured_attempts():
    h = Harness(routine(wp(0.1, actions=[ShutterAction(on_failure="retry", retries=2)])))
    h.shutter.script([ShutterTimeout("1"), ShutterTimeout("2"), ShutterTimeout("3")])
    h.run()

    assert h.executor.phase is Phase.ABORTED
    assert h.shutter.shots == 0


def test_a_dead_link_aborts_rather_than_shooting_blanks():
    """BLE down while the arm walks the whole set is the most expensive failure
    in this workflow: a full run with nothing on the card."""
    h = Harness(routine(wp(0.1, actions=[ShutterAction()]), wp(0.5, actions=[ShutterAction()])))
    h.shutter.set_connected(False)
    h.run()

    assert h.executor.phase is Phase.ABORTED
    assert isinstance(ShutterNotConnected(), Exception)
    assert h.shutter.shots == 0


# ── abort / emergency stop ───────────────────────────────────────────────────


def test_abort_mid_playback_stops_and_never_resumes():
    """The most important test in the project.

    This is what the control loop does when the emergency stop engages. The
    routine must stop where it is and stay stopped: by the time an operator
    clears a stop, someone has usually moved the arm or taken the subject away.
    """
    h = Harness(routine(wp(0.3, actions=[ShutterAction()]), wp(0.9, actions=[ShutterAction()])))
    h.executor.start()
    h.step(5)

    h.executor.abort("emergency stop engaged")
    assert h.executor.phase is Phase.ABORTED
    assert h.executor.error == "emergency stop engaged"

    shots_at_abort = h.shutter.shots
    index_at_abort = h.executor.progress().waypoint_index

    # Keep ticking as the control loop would. Nothing may happen.
    h.step(2000)

    assert h.executor.phase is Phase.ABORTED
    assert h.shutter.shots == shots_at_abort
    assert h.executor.progress().waypoint_index == index_at_abort


def test_abort_is_idempotent_and_keeps_the_first_reason():
    h = Harness(routine(wp(0.3)))
    h.executor.start()
    h.executor.abort("emergency stop engaged")
    h.executor.abort("something later")

    assert h.executor.error == "emergency stop engaged"


def test_abort_after_completion_does_not_rewrite_the_outcome():
    h = Harness(routine(wp(0.1)))
    h.run()
    h.executor.abort("too late")

    assert h.executor.phase is Phase.DONE
    assert h.executor.error is None


def test_starting_twice_is_refused():
    h = Harness(routine(wp(0.1)))
    h.executor.start()
    with pytest.raises(RuntimeError):
        h.executor.start()


# ── progress reporting ───────────────────────────────────────────────────────


def test_progress_reports_phases_in_order():
    h = Harness(routine(wp(0.1, settle_ms=100, actions=[ShutterAction()])))
    h.run()

    seen = [e.phase for e in h.events]
    assert seen[0] is Phase.MOVING
    assert Phase.SETTLING in seen
    assert Phase.ACTING in seen
    assert seen[-1] is Phase.DONE


def test_progress_carries_routine_identity_and_totals():
    h = Harness(routine(wp(0.1), wp(0.2), wp(0.3), name="round the subject"))
    h.executor.start()

    p = h.executor.progress()
    assert p.routine_name == "round the subject"
    assert p.waypoint_total == 3
    assert p.waypoint_index == 0


# ── first-waypoint approach ──────────────────────────────────────────────────


def test_the_approach_to_the_first_waypoint_is_speed_limited():
    """Later waypoints start from the previous one, so their stored duration
    was chosen against a known pose. The first starts from wherever teaching
    left the arm, and honouring a short duration there would fling it."""
    from backend.core.executor import FIRST_APPROACH_MAX_SPEED

    far = 2.0
    h = Harness(routine(wp(far, duration_s=0.1)))
    h.executor.start()

    commanded = h.executor._arrival_deadline - h.clock.now
    assert commanded > 0.1 * 3, "the stored duration was used unchanged"
    assert commanded >= (far / FIRST_APPROACH_MAX_SPEED) * 3 * 0.99


def test_a_short_first_hop_keeps_its_own_duration():
    h = Harness(routine(wp(0.05, duration_s=2.0)))
    h.executor.start()

    assert h.executor._arrival_deadline - h.clock.now == pytest.approx(2.0 * 3)


def test_later_waypoints_are_not_stretched():
    h = Harness(routine(wp(0.0, duration_s=1.0), wp(2.0, duration_s=1.0)))
    h.executor.start()
    while h.executor.progress().waypoint_index == 0 and not h.executor.is_finished:
        h.step()

    assert h.executor.progress().waypoint_index == 1
    assert h.executor._arrival_deadline - h.clock.now == pytest.approx(1.0 * 3, abs=0.05)
