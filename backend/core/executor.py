"""Routine execution: move, wait for arrival, settle, run actions, next.

Pure logic. The clock, the arm and the shutter are all injected, nothing here
sleeps, and nothing imports FastAPI. The control loop calls :meth:`tick` once
per iteration and the executor advances at most one step; that keeps the whole
photography workflow testable at whatever speed a fake clock runs at.

The emergency stop is *not* wired in here. The executor exposes :meth:`abort`
and the control loop calls it when it sees the latch engaged. Keeping the latch
out of this module means the executor cannot accidentally decide to resume, and
resuming after a stop is precisely what must never happen: by then someone has
usually moved the arm or taken the subject away.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Mapping

from ..arm.base import ArmDriver
from ..routines.models import FailurePolicy, Routine, ShutterAction, SleepAction, Waypoint
from ..shutter.base import ShutterDriver, ShutterError

log = logging.getLogger(__name__)

#: Per-joint tolerance for "we have arrived", in radians.
DEFAULT_ARRIVAL_EPS = 0.01
#: Ceiling on joint speed for the approach to the *first* waypoint, rad/s.
#:
#: Every later waypoint is reached from the one before it, so its duration was
#: chosen against a known starting pose. The first has no such guarantee: the
#: arm is wherever teaching left it, which may be most of the workspace away.
#: Honouring the stored duration there turns a long move into a fast one.
FIRST_APPROACH_MAX_SPEED = 0.5
#: How much longer than a waypoint's own duration to wait before calling it
#: stuck. Generous, because a stall is reported as a fault and stops the shoot.
ARRIVAL_TIMEOUT_FACTOR = 3.0
ARRIVAL_TIMEOUT_FLOOR_S = 2.0


class Phase(str, Enum):
    IDLE = "idle"
    MOVING = "moving"
    SETTLING = "settling"
    ACTING = "acting"
    DONE = "done"
    #: Stopped early — by a fault, a failed action, or an external abort.
    ABORTED = "aborted"


@dataclass(frozen=True)
class Progress:
    phase: Phase
    waypoint_index: int
    waypoint_total: int
    action_index: int | None = None
    action_total: int = 0
    routine_id: str | None = None
    routine_name: str | None = None
    error: str | None = None

    @property
    def is_finished(self) -> bool:
        return self.phase in (Phase.DONE, Phase.ABORTED)


class RoutineExecutor:
    """Drives one routine to completion, one :meth:`tick` at a time."""

    def __init__(
        self,
        routine: Routine,
        arm: ArmDriver,
        shutter: ShutterDriver,
        clock: Callable[[], float],
        arrival_eps: float = DEFAULT_ARRIVAL_EPS,
        on_progress: Callable[[Progress], None] | None = None,
    ) -> None:
        self._routine = routine
        self._arm = arm
        self._shutter = shutter
        self._clock = clock
        self._arrival_eps = arrival_eps
        self._on_progress = on_progress

        self._phase = Phase.IDLE
        self._wp_index = 0
        self._action_index = 0
        self._error: str | None = None

        self._phase_started_at = 0.0
        self._arrival_deadline = 0.0
        self._sleep_until = 0.0
        self._attempts = 0
        #: Frames fired so far within the current shutter action (a burst).
        self._shots_fired = 0

    # ── state ────────────────────────────────────────────────────────────────

    @property
    def phase(self) -> Phase:
        return self._phase

    @property
    def is_finished(self) -> bool:
        return self._phase in (Phase.DONE, Phase.ABORTED)

    @property
    def error(self) -> str | None:
        return self._error

    def progress(self) -> Progress:
        waypoint = self._current_waypoint()
        return Progress(
            phase=self._phase,
            waypoint_index=self._wp_index,
            waypoint_total=len(self._routine.waypoints),
            action_index=self._action_index if self._phase is Phase.ACTING else None,
            action_total=len(waypoint.actions) if waypoint else 0,
            routine_id=self._routine.id,
            routine_name=self._routine.name,
            error=self._error,
        )

    def _current_waypoint(self) -> Waypoint | None:
        if 0 <= self._wp_index < len(self._routine.waypoints):
            return self._routine.waypoints[self._wp_index]
        return None

    def _emit(self) -> None:
        if self._on_progress is not None:
            self._on_progress(self.progress())

    # ── control ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Begin. An empty routine finishes immediately rather than hanging."""
        if self._phase is not Phase.IDLE:
            raise RuntimeError(f"already started (phase={self._phase.value})")

        if not self._routine.waypoints:
            self._phase = Phase.DONE
            self._emit()
            return

        self._wp_index = 0
        self._begin_move()

    def abort(self, reason: str) -> None:
        """Stop the routine. Idempotent, and never resumes.

        Called by the control loop when the emergency stop engages. The arm is
        deliberately left alone here -- whoever aborted is responsible for the
        arm, and for a stop that means holding it, not letting the executor
        issue one last command on its way out.
        """
        if self.is_finished:
            return
        self._phase = Phase.ABORTED
        self._error = reason
        log.warning("routine %s aborted: %s", self._routine.id, reason)
        self._emit()

    def tick(self) -> None:
        """Advance by at most one step. Safe to call after finishing."""
        if self._phase in (Phase.IDLE, Phase.DONE, Phase.ABORTED):
            return

        if self._phase is Phase.MOVING:
            self._tick_moving()
        elif self._phase is Phase.SETTLING:
            self._tick_settling()
        elif self._phase is Phase.ACTING:
            self._tick_acting()

    # ── phases ───────────────────────────────────────────────────────────────

    def _begin_move(self) -> None:
        waypoint = self._routine.waypoints[self._wp_index]
        now = self._clock()
        duration = waypoint.duration_s

        if self._wp_index == 0:
            duration = self._first_approach_duration(waypoint)

        self._phase = Phase.MOVING
        self._phase_started_at = now
        self._arrival_deadline = now + max(
            ARRIVAL_TIMEOUT_FLOOR_S, duration * ARRIVAL_TIMEOUT_FACTOR
        )
        self._arm.move_to(waypoint.joints, duration)
        self._emit()

    def _first_approach_duration(self, waypoint: Waypoint) -> float:
        """Stretch the first move so no joint exceeds a safe speed.

        Later waypoints start from the previous one, so their stored duration
        was chosen against a known starting pose. The first starts from
        wherever the arm happens to be — often across the workspace — and using
        the stored duration there would fling it.
        """
        positions = self._arm.read_state().positions
        largest_move = max(
            (abs(positions.get(name, target) - target) for name, target in waypoint.joints.items()),
            default=0.0,
        )
        needed = largest_move / FIRST_APPROACH_MAX_SPEED
        if needed > waypoint.duration_s:
            log.info(
                "stretching approach to first waypoint: %.1fs -> %.1fs (%.2f rad to cover)",
                waypoint.duration_s,
                needed,
                largest_move,
            )
            return needed
        return waypoint.duration_s

    def _tick_moving(self) -> None:
        waypoint = self._routine.waypoints[self._wp_index]
        if self._has_arrived(waypoint.joints):
            self._begin_settle()
            return

        if self._clock() >= self._arrival_deadline:
            self.abort(
                f"waypoint {self._wp_index} not reached within "
                f"{self._arrival_deadline - self._phase_started_at:.1f}s"
            )

    def _has_arrived(self, target: Mapping[str, float]) -> bool:
        positions = self._arm.read_state().positions
        return all(abs(positions.get(n, 0.0) - q) <= self._arrival_eps for n, q in target.items())

    def _begin_settle(self) -> None:
        waypoint = self._routine.waypoints[self._wp_index]
        self._phase = Phase.SETTLING
        self._phase_started_at = self._clock()
        self._sleep_until = self._phase_started_at + waypoint.settle_ms / 1000.0
        self._emit()
        # A zero settle should not cost a whole tick.
        self._tick_settling()

    def _tick_settling(self) -> None:
        if self._clock() >= self._sleep_until:
            self._begin_actions()

    def _begin_actions(self) -> None:
        self._phase = Phase.ACTING
        self._action_index = 0
        self._attempts = 0
        self._shots_fired = 0
        self._emit()
        self._tick_acting()

    def _tick_acting(self) -> None:
        waypoint = self._routine.waypoints[self._wp_index]

        if self._action_index >= len(waypoint.actions):
            self._advance_waypoint()
            return

        action = waypoint.actions[self._action_index]

        if isinstance(action, SleepAction):
            self._tick_sleep_action(action)
            return

        self._run_fallible_action(action)

    def _tick_sleep_action(self, action: SleepAction) -> None:
        if self._attempts == 0:
            self._attempts = 1
            self._sleep_until = self._clock() + action.duration_s
            self._emit()
        if self._clock() >= self._sleep_until:
            self._next_action()

    def _run_fallible_action(self, action) -> None:
        if isinstance(action, ShutterAction) and self._shots_fired > 0:
            # Mid-burst: wait out the inter-frame interval, one frame per tick.
            if self._shots_fired >= action.count:
                self._next_action()
                return
            if self._clock() < self._sleep_until:
                return
        try:
            if isinstance(action, ShutterAction):
                # Refocus every frame: between frames of a burst the subject
                # has usually moved — that is why there is a burst at all.
                if action.focus_first:
                    self._shutter.focus()
                self._shutter.shoot()
                self._shots_fired += 1
                if self._shots_fired < action.count:
                    self._sleep_until = self._clock() + action.interval_s
                    self._emit()
                    return
                self._next_action()
                return
            else:  # pragma: no cover — the union is closed
                raise ShutterError(f"no handler for action type {action.type!r}")
        except ShutterError as exc:
            self._handle_action_failure(action, exc)
            return

        self._next_action()

    def _handle_action_failure(self, action, exc: Exception) -> None:
        self._attempts += 1
        where = f"waypoint {self._wp_index}, action {self._action_index} ({action.type})"

        if action.on_failure is FailurePolicy.SKIP:
            log.warning("%s failed, skipping: %s", where, exc)
            self._next_action()
            return

        if action.on_failure is FailurePolicy.RETRY and self._attempts <= action.retries:
            log.warning("%s failed, retry %d/%d: %s", where, self._attempts, action.retries, exc)
            self._emit()
            return

        self.abort(f"{where} failed: {exc}")

    def _next_action(self) -> None:
        self._action_index += 1
        self._attempts = 0
        self._shots_fired = 0
        self._emit()
        if self._action_index >= len(self._routine.waypoints[self._wp_index].actions):
            self._advance_waypoint()

    def _advance_waypoint(self) -> None:
        self._wp_index += 1
        if self._wp_index >= len(self._routine.waypoints):
            self._phase = Phase.DONE
            log.info("routine %s complete", self._routine.id)
            self._emit()
            return
        self._begin_move()
