"""The control loop: the one place that talks to the arm every tick.

:meth:`Controller.tick` is the whole thing, and it is deliberately readable top
to bottom, because the emergency-stop path runs through it.

Order matters. The latch is checked **before** anything else gets to command
the arm, so no mode can slip a command past an engaged stop. That is why the
stop is a latch and not a mode: a mode would have to be reached by a transition
that some other mode might not take.

What the stop does here is **hold**, not release:

    ``arm.hold(frozen_pose)`` keeps torque on and pins the arm where it was.
    Upstream offers ``RebotArm.estop()``, which is one line forwarding to
    ``disable_all()``; MotorBridge documents ``disable_all()`` as "Emergency
    stop all motors". Both cut torque, and a 48 V arm holding a camera drops
    when torque goes. Neither is called here or anywhere else in this project.
    See docs/HARDWARE_NOTES.md.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from ..actions.runner import ActionRunner, ThreadedRunner
from ..actions.shutter import ShutterProvider
from ..arm.base import ArmDriver
from ..routines.models import Routine
from ..arm.base import ArmState
from ..safety import LatchSource, SafetyLatch, Watchdog
from ..shutter.base import ShutterDriver
from .broadcaster import Broadcaster
from .executor import Phase, RoutineExecutor
from .floatlock import FloatLock, FloatLockConfig

log = logging.getLogger(__name__)


class Controller:
    """Owns the arm, the latch and at most one running routine."""

    def __init__(
        self,
        arm: ArmDriver,
        shutter: ShutterDriver,
        latch: SafetyLatch,
        broadcaster: Broadcaster,
        clock: Callable[[], float] | None = None,
        watchdog: Watchdog | None = None,
        expected_period_s: float = 0.01,
        floatlock: FloatLockConfig | None = None,
        actions: ActionRunner | None = None,
    ) -> None:
        self.arm = arm
        # The driver stays reachable: /api/shutter/test checks the link from a
        # request thread, which is a different question from running an action.
        self.shutter = shutter
        # Actions run off this loop. Defaulting to a threaded runner here means
        # nothing that constructs a Controller can accidentally get a runner
        # that blocks the loop -- the one shape this whole layer exists to stop.
        #
        # The loop's clock is deliberately *not* passed down. An action's
        # deadline measures how long a provider has really been working, and a
        # provider works in wall time whatever the loop thinks the time is.
        self.actions = actions or ThreadedRunner([ShutterProvider(shutter)])
        self.latch = latch
        self.broadcaster = broadcaster
        self._clock = clock or time.monotonic
        self.watchdog = watchdog
        self._expected_period_s = expected_period_s

        self._lock = threading.RLock()
        self._executor: RoutineExecutor | None = None
        self._teaching = False
        self._last_state = ArmState(positions={}, velocities={})
        self._hold_target: dict[str, float] | None = None
        self._was_latched = False
        self._floatlock = FloatLock(floatlock)
        self._float_engaged = False

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._tick_times: list[float] = []
        self.rate_hz = 0.0

    # ── playback ─────────────────────────────────────────────────────────────

    @property
    def executor(self) -> RoutineExecutor | None:
        with self._lock:
            return self._executor

    @property
    def is_playing(self) -> bool:
        with self._lock:
            return self._executor is not None and not self._executor.is_finished

    def play(self, routine: Routine) -> RoutineExecutor:
        """Start a routine. Refuses while stopped, teaching, or already playing."""
        with self._lock:
            if self.latch.is_latched:
                raise RuntimeError("emergency stop is engaged")
            if self.is_playing:
                raise RuntimeError("a routine is already playing")
            if self._teaching:
                raise RuntimeError("cannot play while teaching")

            executor = RoutineExecutor(
                routine,
                arm=self.arm,
                actions=self.actions,
                clock=self._clock,
                on_progress=lambda p: self.broadcaster.publish(
                    {"type": "playback", "data": _progress_payload(p)}
                ),
            )
            executor.start()
            self._executor = executor
            return executor

    def stop_playback(self, reason: str = "stopped by operator") -> bool:
        """Abort any running routine. Returns whether one was running."""
        with self._lock:
            if self._executor is None or self._executor.is_finished:
                return False
            self._executor.abort(reason)
            return True

    # ── teaching ─────────────────────────────────────────────────────────────

    @property
    def is_teaching(self) -> bool:
        with self._lock:
            return self._teaching

    def set_teaching(self, enabled: bool) -> None:
        """Enter or leave zero-force drag teaching.

        Entering while a routine plays would put a floating arm and a moving
        target in the same loop, so playback is stopped first.
        """
        with self._lock:
            if enabled and self.latch.is_latched:
                raise RuntimeError("emergency stop is engaged")
            if enabled and self.is_playing:
                self.stop_playback("teaching started")
            self._teaching = enabled
            self._floatlock.reset()
            # Starts locked, so an arm nobody is holding yet does not sag.
            self._float_engaged = False
            self._hold_target = dict(self.arm.read_state().positions) if enabled else None
            if not enabled:
                self.arm.set_float(False)

    # ── the loop ─────────────────────────────────────────────────────────────

    def tick(self) -> None:
        """One iteration. Called by the loop thread, or directly by tests."""
        if self.watchdog is not None:
            self.watchdog.observe_tick(self._expected_period_s)

        try:
            state = self.arm.read_state()
        except Exception:
            # A failed read is not fatal on its own -- CAN drops frames. The
            # watchdog decides when a run of them means we have lost the arm.
            log.exception("arm read failed")
            if self.watchdog is not None:
                self.watchdog.observe_read(ok=False)
            state = self._last_state
        else:
            self._last_state = state
            if self.watchdog is not None:
                self.watchdog.observe_read(ok=True)

        self._record_rate()

        with self._lock:
            latched = self.latch.is_latched
            if latched:
                self._tick_latched(state)
            else:
                if self._was_latched and self.watchdog is not None:
                    # Clearing a stop starts a fresh assessment; suspicion
                    # accumulated before the stop is about the old situation.
                    self.watchdog.reset()
                self._tick_running()
            self._was_latched = latched

            if self.watchdog is not None:
                self.watchdog.observe_hold(
                    state.positions if self._hold_target else None, self._hold_target
                )

            self._publish(state)

    def _tick_latched(self, state) -> None:
        """Hold position and refuse everything else. Never disables the motors."""
        # First tick after engaging decides where "here" is.
        self.latch.record_freeze_pose(state.positions)
        frozen = self.latch.snapshot().freeze_pose or state.positions

        if self._teaching:
            # A floating arm under an engaged stop is a dropped arm.
            self._teaching = False
            self._floatlock.reset()
            self._float_engaged = False
            self.arm.set_float(False)

        if self._executor is not None and not self._executor.is_finished:
            self._executor.abort("emergency stop engaged")

        self.arm.hold(frozen)
        self._hold_target = dict(frozen)

    def _tick_running(self) -> None:
        if self._teaching:
            self._tick_teaching()
            return
        if self._executor is not None and not self._executor.is_finished:
            self._executor.tick()
            # Mid-move a large error is the point, so drift is only judged
            # against a hold, never against a moving target.
            self._hold_target = None
            return
        self._hold_target = None

    def _tick_teaching(self) -> None:
        """Zero-force drag: follow the hand while it moves, hold when it stops.

        The decision is velocity-based (see :mod:`backend.core.floatlock`), and
        the velocity comes from the arm's finite-differenced joint speeds rather
        than from the motor's velocity register, which is not rad/s on this
        firmware.

        End-effector speed is approximated by the largest joint speed rather
        than computed through the Jacobian. That is deliberate: the threshold is
        a "has the hand stopped" test, not a measurement, it has to be retuned
        once a camera is mounted anyway, and a per-tick Jacobian at 500 Hz buys
        precision this decision does not use.
        """
        state = self._last_state
        speed = max((abs(v) for v in state.velocities.values()), default=0.0)

        following = self._floatlock.update(speed, 0.0, self._clock())

        if following:
            # Free: the arm compensates gravity and does not resist.
            if not self._float_engaged:
                self.arm.set_float(True)
                self._float_engaged = True
            self._hold_target = None
            return

        # Locked: pin it where the operator let go. Re-asserted only on the
        # transition, so the target cannot creep tick by tick.
        if self._float_engaged:
            self.arm.set_float(False)
            self._float_engaged = False
            self._hold_target = dict(state.positions)
            log.debug("teach: locked at %s", self._hold_target)

        if self._hold_target:
            self.arm.hold(self._hold_target)

    def _record_rate(self) -> None:
        now = self._clock()
        self._tick_times.append(now)
        cutoff = now - 1.0
        while self._tick_times and self._tick_times[0] < cutoff:
            self._tick_times.pop(0)
        self.rate_hz = float(len(self._tick_times))

    def _publish(self, state) -> None:
        latch = self.latch.snapshot()
        executor = self._executor
        self.broadcaster.publish(
            {
                "type": "state",
                "data": {
                    "t": state.t,
                    "positions": dict(state.positions),
                    "velocities": dict(state.velocities),
                    "rate_hz": self.rate_hz,
                    "mode": self.mode,
                    "estop": {
                        "latched": latch.latched,
                        "reason": latch.reason,
                        "source": latch.source.value if latch.source else None,
                    },
                    "playback": _progress_payload(executor.progress()) if executor else None,
                },
            }
        )

    @property
    def mode(self) -> str:
        """Coarse state for the UI. The latch outranks everything."""
        if self.latch.is_latched:
            return "estop"
        if self._teaching:
            return "teach"
        if self.is_playing:
            return "playback"
        return "idle"

    # ── thread driver ────────────────────────────────────────────────────────
    #
    # On real hardware the loop is upstream's start_control_loop(control_fn,
    # rate), which already owns CAN timing. This thread exists so the simulated
    # arm can run the identical tick() without one.

    def start(self, rate_hz: float = 100.0) -> None:
        if self._thread is not None:
            raise RuntimeError("already started")
        self._stop_event.clear()
        self._expected_period_s = 1.0 / rate_hz
        self._thread = threading.Thread(
            target=self._run, args=(rate_hz,), name="control-loop", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _run(self, rate_hz: float) -> None:
        period = 1.0 / rate_hz
        next_tick = time.monotonic()
        while not self._stop_event.is_set():
            try:
                self.tick()
            except Exception:
                # A raised exception must not kill the loop: the loop is what
                # keeps the arm held. Engage the stop and keep ticking.
                log.exception("control loop tick failed")
                self.latch.engage("control loop tick raised", LatchSource.WATCHDOG)

            next_tick += period
            sleep = next_tick - time.monotonic()
            if sleep > 0:
                time.sleep(sleep)
            else:
                next_tick = time.monotonic()  # fell behind; do not pile up


def _progress_payload(progress) -> dict:
    return {
        "phase": progress.phase.value,
        "waypoint_index": progress.waypoint_index,
        "waypoint_total": progress.waypoint_total,
        "action_index": progress.action_index,
        "action_total": progress.action_total,
        "routine_id": progress.routine_id,
        "routine_name": progress.routine_name,
        "error": progress.error,
        "finished": progress.phase in (Phase.DONE, Phase.ABORTED),
    }
