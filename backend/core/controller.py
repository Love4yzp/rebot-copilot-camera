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
from collections.abc import Callable, Mapping

from ..actions.runner import ActionRunner, ThreadedRunner
from ..actions.shutter import ShutterProvider
from ..arm.base import ArmDriver, ArmState
from ..safety import LatchSource, SafetyLatch, Watchdog
from ..safety.kinematics import ARM_JOINTS
from ..sequences.models import Pose, Sequence, TransitionBlock
from ..shutter.base import ShutterDriver
from ..tuning import PayloadProfile, TuningConfig, TuningRejected
from . import events
from .broadcaster import Broadcaster
from .executor import DEFAULT_APPROACH_S, SequenceExecutor
from .floatlock import FloatLock, FloatLockConfig

log = logging.getLogger(__name__)


def _floatlock_config(tuning: TuningConfig) -> FloatLockConfig:
    fl = tuning.floatlock
    return FloatLockConfig(
        linear_threshold=fl.linear_threshold,
        angular_threshold=fl.angular_threshold,
        release_factor=fl.release_factor,
        lock_factor=fl.lock_factor,
        min_still_s=fl.min_still_s,
    )


class Controller:
    """Owns the arm, the latch and at most one running sequence."""

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
        tuning: TuningConfig | None = None,
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
        # A finished executor is kept, not dropped: /ws goes on broadcasting
        # its final progress, and the UI relies on that lingering "done" to
        # keep saying 到位 while the arm holds. Dropped only by an explicit
        # stop — after POST /api/execute/stop the playback field is null.
        self._executor: SequenceExecutor | None = None
        self._teaching = False
        self._last_state = ArmState(positions={}, velocities={})
        self._hold_target: dict[str, float] | None = None
        self._was_latched = False
        #: Who asked for the sequence that is running (or last ran).
        self._playback_source = "ui"
        #: Operator-calibrated tuning, hot-swappable via :meth:`apply_tuning`.
        #: The whole object is replaced, never mutated in place, so a tick in
        #: flight sees either the old config or the new one — never half of one.
        self._tuning = tuning or TuningConfig()
        self._floatlock = FloatLock(floatlock or _floatlock_config(self._tuning))
        self._float_engaged = False
        self._push_tuning_to_arm()

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._tick_times: list[float] = []
        self.rate_hz = 0.0

    def set_shutter(self, driver: ShutterDriver) -> None:
        """Swap the shutter driver, provider included.

        Both move together on purpose. ``main()`` re-chooses the hardware after
        import time, and a swap that updated ``self.shutter`` alone would leave
        the runner holding a provider wrapped around the old driver: the
        self-test would talk to the real board while every routine kept firing
        into the simulator. Nothing would raise.
        """
        with self._lock:
            self.shutter = driver
            self.actions.register(ShutterProvider(driver))

    # ── tuning ─────────────────────────────────────────────────────────────────

    @property
    def tuning(self) -> TuningConfig:
        with self._lock:
            return self._tuning

    def bind_arm(self, arm: ArmDriver) -> None:
        """Swap the arm driver and re-push the current tuning onto it.

        ``main()`` re-chooses the hardware after import time; without this the
        real arm would run the code defaults while the panel shows the saved
        tuning — the same trap ``set_shutter`` exists to close."""
        with self._lock:
            self.arm = arm
            self._push_tuning_to_arm()

    def apply_tuning(self, config: TuningConfig) -> None:
        """Hot-apply a new tuning config. Rejects what the arm state forbids.

        The gates, by risk class:

        - everything is refused while a sequence executes: the run's executor
          captured its settle/approach values at construction, and retuning a
          moving arm helps no one;
        - a payload change moves the gravity feedforward by newton-metres at
          once, so it is refused while the arm floats (a floating arm has no
          position loop to catch the jump with);
        - float gains are safe mid-float: the follow target is the arm's own
          position, so the position error — and the torque jump — is zero.
        """
        with self._lock:
            if self.is_playing:
                raise TuningRejected("a sequence is executing — stop it before retuning")
            from .. import assets  # local: keep the gate readable in one place

            if assets.has_gripper() and config.payload.profile is not PayloadProfile.GRIPPER:
                # The motor is wired: the gripper's mass is physically on the
                # arm whatever the profile claims, and this is the only legal
                # answer. The options list steers the UI; this is the server
                # refusing the illegal one outright.
                raise TuningRejected(
                    "the gripper motor is on the bus — profile must be 'gripper'"
                )
            payload_changed = config.payload != self._tuning.payload
            if payload_changed and self.arm.is_floating:
                raise TuningRejected(
                    "the arm is floating — let it lock before changing the payload; "
                    "the gravity feedforward jumps when the payload does"
                )
            self._tuning = config
            self._floatlock.config = _floatlock_config(config)
            self._push_tuning_to_arm(rebuild_dynamics=payload_changed)

    def _push_tuning_to_arm(self, rebuild_dynamics: bool = True) -> None:
        self.arm.set_float_gains(self._tuning.float_.kp, self._tuning.float_.kd)
        if rebuild_dynamics:
            self.arm.reload_dynamics(self._tuning.payload)

    def emit_event(self, name: str, data: dict) -> None:
        """Publish a semantic event. Never blocks, never raises.

        Public because the API layer emits a couple of things the control loop
        cannot see — a pose captured by hand arrives over HTTP, not over CAN.
        """
        self.broadcaster.publish(
            {"type": events.TOPIC, "data": events.envelope(name, data, self._clock())}
        )

    # ── playback ─────────────────────────────────────────────────────────────

    @property
    def playback_source(self) -> str:
        """Who asked for the current (or most recent) run."""
        with self._lock:
            return self._playback_source

    @property
    def executor(self) -> SequenceExecutor | None:
        with self._lock:
            return self._executor

    @property
    def playback_sequence_id(self) -> str | None:
        """The id the current (or most recent) run belongs to. For a goto this
        is the pose's id — the mock reports the same — so it never collides
        with a real sequence in the library lockout."""
        with self._lock:
            return self._executor.sequence_id if self._executor is not None else None

    @property
    def is_playing(self) -> bool:
        with self._lock:
            return self._executor is not None and not self._executor.is_finished

    def play(
        self,
        sequence: Sequence,
        poses: Mapping[str, Pose],
        source: str = "ui",
    ) -> SequenceExecutor:
        """Start a sequence. Refuses while stopped, teaching, or already playing.

        Poses arrive resolved — the API layer read them out of the PoseStore,
        so the executor never touches a store and a pose deleted mid-run cannot
        change a run already in flight.

        ``source`` is who asked — the transport bar, an agent, a foot switch, a
        shot-list script. It changes nothing about the motion and is recorded
        only so that "why did the arm just move" has an answer. On a machine
        several things can trigger, that question gets asked first and is
        otherwise unanswerable after the fact.
        """
        with self._lock:
            if self.latch.is_latched:
                raise RuntimeError("emergency stop is engaged")
            if self.is_playing:
                raise RuntimeError("a sequence is already executing")
            if self._teaching:
                raise RuntimeError("cannot execute while teaching")

            executor = SequenceExecutor(
                sequence,
                poses,
                arm=self.arm,
                actions=self.actions,
                clock=self._clock,
                settle_s=self._tuning.settle.min_s,
                settle_drift=self._tuning.settle.drift_rad,
                first_approach_max_speed=self._tuning.approach.first_max_speed,
                on_progress=lambda p: self.broadcaster.publish(
                    {"type": "playback", "data": _progress_payload(p)}
                ),
                on_event=self.emit_event,
            )
            self._playback_source = source
            log.info("playing %r (%d blocks) at the request of %r",
                     sequence.name, len(sequence.blocks), source)
            executor.start()
            self._executor = executor
            return executor

    def goto(self, pose: Pose, source: str = "ui") -> SequenceExecutor:
        """Move to one library pose and stay there.

        The use-layer atomic operation: tap a pose card, the arm goes and
        holds. Implemented as an ephemeral one-block sequence — a lone
        transition with the pose as its target — so arrival checking, the
        first-approach speed limit (the arm can be anywhere when a card is
        tapped) and stop-latch abort all come from the executor unchanged. The
        ephemeral sequence is never stored.
        """
        ephemeral = Sequence(
            id=pose.id,
            name=f"位姿 · {pose.name}",
            blocks=[TransitionBlock(duration_s=DEFAULT_APPROACH_S)],
        )
        with self._lock:
            if self.latch.is_latched:
                raise RuntimeError("emergency stop is engaged")
            if self.is_playing:
                raise RuntimeError("a sequence is already executing")
            if self._teaching:
                raise RuntimeError("cannot move while teaching")

            executor = SequenceExecutor(
                ephemeral,
                {pose.id: pose},
                goto=pose,
                arm=self.arm,
                actions=self.actions,
                clock=self._clock,
                settle_s=self._tuning.settle.min_s,
                settle_drift=self._tuning.settle.drift_rad,
                first_approach_max_speed=self._tuning.approach.first_max_speed,
                on_progress=lambda p: self.broadcaster.publish(
                    {"type": "playback", "data": _progress_payload(p)}
                ),
                on_event=self.emit_event,
            )
            self._playback_source = source
            log.info("goto pose %r at the request of %r", pose.name, source)
            executor.start()
            self._executor = executor
            return executor

    def resume(self) -> bool:
        """Continue past a wait marker. False when nothing is suspended."""
        with self._lock:
            if self._executor is None:
                return False
            return self._executor.resume()

    def stop_playback(self, reason: str = "stopped by operator") -> bool:
        """Abort any running sequence. Returns whether one was running.

        The executor is dropped afterwards, so the playback field goes null —
        an explicit stop clears the progress, unlike a finished run, whose
        final "done" keeps broadcasting (see __init__).
        """
        with self._lock:
            if self._executor is None:
                return False
            running = not self._executor.is_finished
            if running:
                self._executor.abort(reason)
            # Dropped even when the run had already finished: an explicit stop
            # clears the progress (the docstring below, and the mock's
            # semantics). Only a run that finished *on its own* keeps
            # broadcasting its final "done".
            self._executor = None
            return running

    # ── shutdown ─────────────────────────────────────────────────────────────

    def park_home(self) -> SequenceExecutor | None:
        """Slow move to the all-zero rest pose, for process shutdown.

        Returns the executor driving the move, or ``None`` when no move was
        started. ``None`` means the stop latch is engaged: an engaged stop
        means something went wrong, and planning a new motion is exactly what
        it exists to prevent — the arm holds its frozen pose through exit
        instead, and the motors keep holding it after the process is gone.

        The move goes through :meth:`goto`, so the first-approach speed limit,
        arrival detection and stuck-abort all apply unchanged. Only the six
        arm joints are parked; the gripper is left where it is (there is no
        calibrated mapping from motor angle to finger travel). A stop engaged
        *during* the park is handled by the normal latched-tick path: the
        executor is aborted and the arm freezes where it is.
        """
        if self.latch.is_latched:
            log.warning("stop latch engaged — holding the frozen pose, not parking")
            return None
        if self.is_teaching:
            self.set_teaching(False)
        self.stop_playback("process shutdown — parking at zero")
        pose = Pose(
            name="回零 · park",
            joints={n: 0.0 for n in ARM_JOINTS if n in self.arm.joint_names},
        )
        try:
            return self.goto(pose, source="shutdown")
        except RuntimeError:
            # The latch engaged between the check above and the goto.
            log.warning("stop latch engaged mid-park — holding the frozen pose")
            return None

    # ── teaching ─────────────────────────────────────────────────────────────

    @property
    def is_teaching(self) -> bool:
        with self._lock:
            return self._teaching

    def set_teaching(self, enabled: bool) -> None:
        """Enter or leave zero-force drag teaching.

        Refused while a sequence plays: a floating arm and a moving target in
        the same loop is the mock's 409, and the operator can stop first.
        """
        with self._lock:
            if enabled and self.latch.is_latched:
                raise RuntimeError("emergency stop is engaged")
            if enabled and self.is_playing:
                raise RuntimeError("cannot teach while a sequence is executing")
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
            if latched != self._was_latched:
                # Emitted from here rather than from SafetyLatch: the latch is
                # pure logic that touches nothing but itself, and giving it a
                # broadcaster would be the first thread through that wall.
                snapshot = self.latch.snapshot()
                self.emit_event(
                    events.ESTOP_ENGAGED if latched else events.ESTOP_CLEARED,
                    {
                        "reason": snapshot.reason,
                        "source": snapshot.source.value if snapshot.source else None,
                    },
                )
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
            # Free: the arm compensates gravity and does not resist. Float is
            # a command stream, not silence — MIT executes the last command it
            # received, so a loop that goes quiet here leaves the motors
            # holding the stale lock target and the arm pulls back toward it.
            if not self._float_engaged:
                self.arm.set_float(True)
                self._float_engaged = True
            self._hold_target = None
            self.arm.follow()
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
                        "engaged_at": latch.engaged_at,
                        "freeze_pose": dict(latch.freeze_pose) if latch.freeze_pose else None,
                    },
                    "playback": _progress_payload(executor.progress()) if executor else None,
                    "source": self._playback_source if executor else None,
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

    @property
    def is_running(self) -> bool:
        """Whether the control-loop thread is up. The shutdown park's move is
        driven by that loop, so a shutdown before startup finished has nothing
        to drive it with."""
        return self._thread is not None

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
    """The SeqPlayback wire shape — field for field what the frontend reads."""
    return {
        "sequence_id": progress.sequence_id,
        "sequence_name": progress.sequence_name,
        "block_index": progress.block_index,
        "block_total": progress.block_total,
        "phase": progress.phase.value,
        "t_in_block": progress.t_in_block,
        "error": progress.error,
        "finished": progress.is_finished,
        "approaching": progress.approaching,
    }
