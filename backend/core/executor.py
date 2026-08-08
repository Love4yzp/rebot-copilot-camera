"""Sequence execution: walk the block list, one tick at a time.

Pure logic. The clock, the arm and the action runner are all injected, nothing
here sleeps, and nothing imports FastAPI or a store — poses arrive already
resolved. The control loop calls :meth:`tick` once per iteration and the
executor advances at most one step; that keeps the whole photography workflow
testable at whatever speed a fake clock runs at.

The walk mirrors the mock's ``advancePlayback`` (frontend/mock/plugin.ts),
which is the authoritative execution semantics, with the differences a real
arm forces:

- a transition is commanded as ``move_to(pose, duration)`` and confirmed by
  arrival detection, where the mock lerps joints. **Easing only shapes the
  frontend preview** — the real arm walks whatever profile upstream's
  ``move_to`` picks. Close, not guaranteed identical; the UI says so.
- a hold's clock starts when the arm has *arrived* at the pose, so a marker
  can never fire mid-approach and photograph a moving scene. The mock starts
  the countdown at block entry because its arm is never late.
- a block can be stretched by reality: a marker still executing when the
  commanded duration runs out holds the block open until it finishes. That is
  TIMELINE rule 4 — the plan ruler is commanded time, execution is honest.

Markers are *submitted*, not called. Providers block — a shutter waits on a
camera waking over BLE — and this runs inside the control loop, which is what
holds the arm up. So :mod:`backend.actions.runner` takes the work off-thread
and the executor polls a job once per tick. Markers in a block run in order,
one at a time: two jobs on one provider is two things driving the same
hardware. Bursts are still paced here, so an emergency stop lands *between*
frames.

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

from pydantic import BaseModel

from ..actions.base import ActionContext, ActionError, ActionProvider
from ..actions.runner import ActionRunner, Job
from ..actions.shutter import SHUTTER_PROVIDER_ID, ShutterParams
from ..arm.base import ArmDriver
from ..sequences.models import (
    WAIT_KIND,
    Block,
    EventMarker,
    HoldBlock,
    Pose,
    Sequence,
)
from ..sequences.normalize import nearest_hold
from . import events

log = logging.getLogger(__name__)

#: Per-joint tolerance for "we have arrived", in radians.
DEFAULT_ARRIVAL_EPS = 0.01
#: Ceiling on joint speed for the approach to the *first* pose, rad/s.
#:
#: Every later pose is reached from the one before it, so its duration was
#: chosen against a known starting pose. The first has no such guarantee: the
#: arm is wherever teaching left it, which may be most of the workspace away.
FIRST_APPROACH_MAX_SPEED = 0.5
#: Base duration for the approach to the first block's pose (and for a goto).
DEFAULT_APPROACH_S = 2.0
#: How much longer than a move's own duration to wait before calling it stuck.
#: Generous, because a stall is reported as a fault and stops the shoot.
ARRIVAL_TIMEOUT_FACTOR = 3.0
ARRIVAL_TIMEOUT_FLOOR_S = 2.0
#: Fixed per-marker timeout. A provider that does not answer in time fails the
#: marker, and a failed marker aborts the run — the fixed policy (see module
#: docstring and TIMELINE: a silently missed frame is found at review time).
MARKER_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class _Dispatch:
    """One marker, resolved to something the runner can be handed.

    ``repeat``/``interval_s`` stay out here rather than inside a provider so an
    emergency stop lands *between* frames of a burst. A provider that looped
    internally would be uninterruptible, and the whole burst would be shot
    before anyone noticed the stop.
    """

    provider_id: str
    params: BaseModel
    repeat: int = 1
    interval_s: float = 0.0


class Phase(str, Enum):
    """The wire vocabulary of SeqPlayback — one value per block type, plus the
    three run terminals. There is no "moving/settling/acting": the plan ruler
    only knows holds and transitions."""

    HOLD = "hold"
    TRANSITION = "transition"
    #: Suspended on a wait marker, until :meth:`resume`.
    WAIT = "wait"
    DONE = "done"
    #: Stopped early — by a fault, a failed marker, or an external abort.
    ABORTED = "aborted"


@dataclass(frozen=True)
class Progress:
    """The SeqPlayback shape. ``block_index`` sits one past the last block once
    finished (the advance step increments before it notices it is done)."""

    phase: Phase
    block_index: int
    block_total: int
    t_in_block: float
    sequence_id: str
    sequence_name: str
    error: str | None = None

    @property
    def is_finished(self) -> bool:
        return self.phase in (Phase.DONE, Phase.ABORTED)


class SequenceExecutor:
    """Drives one sequence to completion, one :meth:`tick` at a time."""

    def __init__(
        self,
        sequence: Sequence,
        poses: Mapping[str, Pose],
        arm: ArmDriver,
        actions: ActionRunner,
        clock: Callable[[], float],
        arrival_eps: float = DEFAULT_ARRIVAL_EPS,
        #: Set for a single-pose goto: the sequence is one transition block and
        #: this is where it goes. The mock's goto is exactly this shape.
        goto: Pose | None = None,
        on_progress: Callable[[Progress], None] | None = None,
        on_event: Callable[[str, dict], None] | None = None,
    ) -> None:
        self._sequence = sequence
        self._poses = poses
        self._arm = arm
        self._actions = actions
        self._clock = clock
        self._arrival_eps = arrival_eps
        self._goto = goto
        self._on_progress = on_progress
        self._on_event = on_event

        #: None until start(); the wire never sees an "idle" phase.
        self._phase: Phase | None = None
        self._block_index = 0
        self._error: str | None = None

        #: When the current block's clock started. None while a hold is still
        #: approaching its pose — a hold's time does not run before arrival.
        self._timing_started_at: float | None = None
        self._arrival_deadline = 0.0
        #: Whether the current transition's target has been reached.
        self._arrived = False
        #: The pose the current block is about: the hold's own pose, or the
        #: transition's target (the next hold's pose, or the goto pose).
        self._block_pose: Pose | None = None

        #: Markers already fired in the current block — a wait must not
        #: re-suspend after resume. Cleared on every block entry.
        self._fired: set[str] = set()
        self._marker_cursor = 0
        #: Where a wait marker suspended the run, in block time, so resume can
        #: pick the clock up exactly there — suspension time is not block time.
        self._suspended_t = 0.0

        #: The marker currently executing, the job it is running on a worker
        #: thread, and the burst bookkeeping. A burst is paced here, one frame
        #: per submit, so an abort lands between frames.
        self._active_marker: EventMarker | None = None
        self._job: Job | None = None
        self._shots_fired = 0
        self._repeat = 1
        self._interval_s = 0.0
        self._next_frame_at = 0.0
        #: Which provider the in-flight job went to, for the event that reports
        #: how it turned out — by then the dispatch has been consumed.
        self._last_provider: str | None = None

    # ── state ────────────────────────────────────────────────────────────────

    @property
    def phase(self) -> Phase | None:
        return self._phase

    @property
    def sequence_id(self) -> str:
        return self._sequence.id

    @property
    def is_finished(self) -> bool:
        return self._phase in (Phase.DONE, Phase.ABORTED)

    @property
    def is_waiting(self) -> bool:
        return self._phase is Phase.WAIT

    @property
    def error(self) -> str | None:
        return self._error

    def progress(self) -> Progress:
        return Progress(
            phase=self._phase or Phase.DONE,
            block_index=self._block_index,
            block_total=len(self._sequence.blocks),
            t_in_block=self._t_in_block(),
            sequence_id=self._sequence.id,
            sequence_name=self._sequence.name,
            error=self._error,
        )

    def _t_in_block(self) -> float:
        if self._phase is Phase.WAIT:
            return self._suspended_t
        if self._timing_started_at is None:
            return 0.0
        return self._clock() - self._timing_started_at

    def _current_block(self) -> Block | None:
        if 0 <= self._block_index < len(self._sequence.blocks):
            return self._sequence.blocks[self._block_index]
        return None

    def _emit(self) -> None:
        if self._on_progress is not None:
            self._on_progress(self.progress())

    def _emit_event(self, name: str, data: dict) -> None:
        """Publish a semantic event. Never blocks, never raises: subscribers are
        watching a shoot, not taking part in one."""
        if self._on_event is None:
            return
        try:
            self._on_event(name, data)
        except Exception:  # pragma: no cover — a sink must not break a sequence
            log.exception("event sink raised on %s", name)

    def _context(self) -> ActionContext:
        """What a provider is told. Note the absence of the arm.

        The field names are the v1 ActionContext vocabulary — providers are
        third-party code compiled against them, so the sequence/block rename
        stops at this boundary.
        """
        pose = self._block_pose
        return ActionContext(
            routine_id=self._sequence.id,
            routine_name=self._sequence.name,
            waypoint_index=self._block_index,
            waypoint_note=pose.name if pose is not None else "",
            joints=dict(self._arm.read_state().positions),
            emit=self._emit_event,
        )

    # ── control ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Begin. An empty sequence finishes immediately rather than hanging —
        the API refuses empties with 400 before this can happen."""
        if self._phase is not None:
            raise RuntimeError(f"already started (phase={self._phase.value})")

        if not self._sequence.blocks:
            self._phase = Phase.DONE
            self._emit()
            return

        self._emit_event(
            events.SEQUENCE_STARTED,
            {
                "sequence_id": self._sequence.id,
                "sequence_name": self._sequence.name,
                "blocks": len(self._sequence.blocks),
            },
        )
        self._block_index = 0
        self._enter_block()

    def abort(self, reason: str) -> None:
        """Stop the sequence. Idempotent, and never resumes.

        Called by the control loop when the emergency stop engages. The arm is
        deliberately left alone here -- whoever aborted is responsible for the
        arm, and for a stop that means holding it, not letting the executor
        issue one last command on its way out.

        A marker already in flight is abandoned rather than cancelled. A serial
        write on its way to a camera cannot be recalled; what stops here is
        anything happening *because* of how it turns out.
        """
        if self._phase is None or self.is_finished:
            return
        if self._job is not None:
            self._job.abandon()
            self._job = None
        self._phase = Phase.ABORTED
        self._error = reason
        log.warning("sequence %s aborted: %s", self._sequence.id, reason)
        self._emit_event(
            events.SEQUENCE_ABORTED,
            {
                "sequence_id": self._sequence.id,
                "sequence_name": self._sequence.name,
                "reason": reason,
                "block_index": self._block_index,
            },
        )
        self._emit()

    def resume(self) -> bool:
        """Continue past the wait marker the run is suspended on.

        The clock picks up at the marker's own time: the suspension is not
        charged against the block, matching the mock, which clamps ``t`` to the
        marker and counts on from there.
        """
        if self._phase is not Phase.WAIT:
            return False
        block = self._current_block()
        if block is None:  # pragma: no cover — a waiting run always has one
            return False
        self._phase = Phase.HOLD if isinstance(block, HoldBlock) else Phase.TRANSITION
        self._timing_started_at = self._clock() - self._suspended_t
        self._emit()
        return True

    def tick(self) -> None:
        """Advance by at most one step. Safe to call after finishing."""
        if self._phase is None or self.is_finished or self._phase is Phase.WAIT:
            return

        self._poll_job()
        if self.is_finished:
            return
        self._pace_burst()
        self._update_motion()
        if self.is_finished:
            return
        self._fire_due_markers()
        if self.is_finished or self._phase is Phase.WAIT:
            return
        self._maybe_complete_block()

    # ── block walking ────────────────────────────────────────────────────────

    def _enter_block(self) -> None:
        block = self._sequence.blocks[self._block_index]
        self._fired = set()
        self._marker_cursor = 0
        self._active_marker = None
        self._job = None
        self._shots_fired = 0
        self._arrived = False
        now = self._clock()

        if isinstance(block, HoldBlock):
            self._phase = Phase.HOLD
            pose = self._poses.get(block.pose_id)
            if pose is None:
                # The API refuses unknown pose references at execute time, so
                # reaching here means the pose was deleted mid-run. Say so
                # rather than hang — the mock's "sequence disappeared mid-run".
                self.abort(f"pose {block.pose_id!r} is gone mid-run")
                return
            self._block_pose = pose
            if self._has_arrived(pose.joints):
                self._begin_hold_timing()
            else:
                # The approach to the first block's pose: the arm is wherever
                # teaching left it, so the move is speed-limited (v1 parity).
                self._timing_started_at = None
                duration = self._move_duration(DEFAULT_APPROACH_S, pose.joints)
                self._arrival_deadline = now + max(
                    ARRIVAL_TIMEOUT_FLOOR_S, duration * ARRIVAL_TIMEOUT_FACTOR
                )
                self._arm.move_to(pose.joints, duration)
                self._emit()
            return

        self._phase = Phase.TRANSITION
        target = self._transition_target()
        if target is None:  # pragma: no cover — normalization guarantees one
            self.abort("transition block has no target pose")
            return
        self._block_pose = target
        self._timing_started_at = now
        duration = self._move_duration(block.duration_s, target.joints)
        self._arrival_deadline = now + max(
            ARRIVAL_TIMEOUT_FLOOR_S, duration * ARRIVAL_TIMEOUT_FACTOR
        )
        self._arm.move_to(target.joints, duration)
        self._emit()

    def _transition_target(self) -> Pose | None:
        """Where a transition goes: the next hold's pose, or the goto pose for
        a single-block goto run."""
        hold = nearest_hold(self._sequence.blocks, self._block_index, +1)
        if hold is not None:
            return self._poses.get(hold.pose_id)
        return self._goto

    def _move_duration(self, base: float, target: Mapping[str, float]) -> float:
        """Stretch the run's first move so no joint exceeds a safe speed.

        Later moves start from the previous pose, so their stored duration was
        chosen against a known starting pose. The first starts from wherever
        the arm happens to be — often across the workspace — and using the
        stored duration there would fling it.
        """
        if self._block_index != 0:
            return base
        positions = self._arm.read_state().positions
        largest_move = max(
            (abs(positions.get(name, q) - q) for name, q in target.items()),
            default=0.0,
        )
        needed = largest_move / FIRST_APPROACH_MAX_SPEED
        if needed > base:
            log.info(
                "stretching approach to first pose: %.1fs -> %.1fs (%.2f rad to cover)",
                base,
                needed,
                largest_move,
            )
            return needed
        return base

    def _begin_hold_timing(self) -> None:
        """Arrival confirmed: the hold's clock starts here, so no marker can
        fire mid-approach. This is the moment an integration usually wants —
        the scene is now what the pose said it would be, and the arm holds it.
        """
        pose = self._block_pose
        self._timing_started_at = self._clock()
        self._arrived = True
        if pose is not None:
            self._emit_event(
                events.POSE_ARRIVED,
                {
                    "sequence_id": self._sequence.id,
                    "sequence_name": self._sequence.name,
                    "block_index": self._block_index,
                    "pose_id": pose.id,
                    "pose_name": pose.name,
                },
            )
        self._emit()

    def _advance(self) -> None:
        self._block_index += 1
        if self._block_index >= len(self._sequence.blocks):
            self._phase = Phase.DONE
            self._timing_started_at = None
            if self._goto is not None:
                # A goto has no hold block to report arrival from, but "the arm
                # is at the pose and holding" is exactly what happened.
                self._emit_event(
                    events.POSE_ARRIVED,
                    {
                        "sequence_id": self._sequence.id,
                        "sequence_name": self._sequence.name,
                        "block_index": len(self._sequence.blocks) - 1,
                        "pose_id": self._goto.id,
                        "pose_name": self._goto.name,
                    },
                )
            log.info("sequence %s complete", self._sequence.id)
            self._emit_event(
                events.SEQUENCE_DONE,
                {
                    "sequence_id": self._sequence.id,
                    "sequence_name": self._sequence.name,
                    "blocks": len(self._sequence.blocks),
                },
            )
            self._emit()
            return
        self._enter_block()

    # ── motion ───────────────────────────────────────────────────────────────

    def _update_motion(self) -> None:
        block = self._current_block()
        if block is None:
            return

        if isinstance(block, HoldBlock):
            if self._timing_started_at is not None:
                return  # already arrived; the countdown is running
            pose = self._block_pose
            if pose is not None and self._has_arrived(pose.joints):
                self._begin_hold_timing()
            elif self._clock() >= self._arrival_deadline:
                self.abort(
                    f"block {self._block_index}: pose "
                    f"{pose.name if pose else block.pose_id!r} not reached in time"
                )
            return

        # Transition: arrival is confirmed by position, not by the clock.
        if not self._arrived:
            target = self._block_pose
            if target is not None and self._has_arrived(target.joints):
                self._arrived = True
            elif self._clock() >= self._arrival_deadline:
                self.abort(
                    f"block {self._block_index}: pose "
                    f"{target.name if target else '?'!r} not reached in time"
                )

    def _has_arrived(self, target: Mapping[str, float]) -> bool:
        positions = self._arm.read_state().positions
        return all(abs(positions.get(n, 0.0) - q) <= self._arrival_eps for n, q in target.items())

    # ── markers ──────────────────────────────────────────────────────────────

    def _marker_time(self, block: Block, marker: EventMarker) -> float:
        """Where a marker sits inside its block, in seconds (proportion →
        seconds inside a transition)."""
        return marker.at if isinstance(block, HoldBlock) else marker.at * block.duration_s

    def _burst_pending(self) -> bool:
        return (
            self._active_marker is not None
            and self._job is None
            and 0 < self._shots_fired < self._repeat
        )

    def _poll_job(self) -> None:
        """Collect a finished job. Between submitting and resolving the
        executor does nothing at all, and that is the point: the control loop
        goes on ticking while a provider sits on a serial exchange."""
        if self._job is None or not self._job.done:
            return

        job, self._job = self._job, None
        marker = self._active_marker
        if job.error is not None:
            self._emit_event(
                events.ACTION_FAILED,
                {
                    "provider": self._last_provider,
                    "block_index": self._block_index,
                    "marker_id": marker.id if marker else None,
                    "error": str(job.error),
                    "kind": type(job.error).__name__,
                },
            )
            # The failure policy is fixed: abort. A silently missed frame is
            # not noticed until the whole set is reviewed. There is no retry to
            # downgrade — abort is exactly where v1's retryable=False downgrade
            # landed, so a provider that declares itself unrepeatable gets the
            # same treatment as everything else: it is never re-run.
            where = f"block {self._block_index}, marker {marker.kind if marker else '?'}"
            self.abort(f"{where} failed: {job.error}")
            return

        self._shots_fired += 1
        self._emit_event(
            events.ACTION_DONE,
            {
                "provider": self._last_provider,
                "block_index": self._block_index,
                "marker_id": marker.id if marker else None,
                "frame": self._shots_fired,
                "frames": self._repeat,
            },
        )
        if self._shots_fired < self._repeat:
            self._next_frame_at = self._clock() + self._interval_s
            self._emit()
            return
        self._active_marker = None
        self._emit()

    def _pace_burst(self) -> None:
        """Fire the next frame of a burst once the interval has elapsed."""
        if not self._burst_pending():
            return
        if self._clock() < self._next_frame_at:
            return
        self._submit_marker_job()

    def _fire_due_markers(self) -> None:
        block = self._current_block()
        if block is None:
            return
        if isinstance(block, HoldBlock) and self._timing_started_at is None:
            return  # still approaching: nothing fires before arrival

        t = self._t_in_block()
        markers = block.markers
        while self._marker_cursor < len(markers):
            # One marker at a time, in block order: two jobs on one provider is
            # two things driving the same hardware. A later marker whose time
            # passes while an earlier one runs fires when the earlier finishes —
            # the block stretches, it does not overlap.
            if self._job is not None or self._burst_pending():
                return
            marker = markers[self._marker_cursor]
            if t < self._marker_time(block, marker):
                return
            self._marker_cursor += 1
            self._fired.add(marker.id)
            if marker.kind == WAIT_KIND:
                self._suspended_t = self._marker_time(block, marker)
                self._phase = Phase.WAIT
                self._emit()
                return
            self._begin_marker(marker)
            if self.is_finished:
                return

    def _begin_marker(self, marker: EventMarker) -> None:
        try:
            dispatch = self._dispatch(marker)
        except Exception as exc:
            # Bad params on a stored marker. The API validates them on the way
            # in, so reaching here means the sequence predates the provider or
            # the provider changed its model under it.
            self.abort(
                f"block {self._block_index}, marker {marker.kind} could not start: {exc}"
            )
            return
        self._active_marker = marker
        self._repeat = dispatch.repeat
        self._interval_s = dispatch.interval_s
        self._shots_fired = 0
        self._submit_marker_job()

    def _submit_marker_job(self) -> None:
        marker = self._active_marker
        if marker is None:  # pragma: no cover — guarded by every caller
            return
        provider = self._actions.provider(marker.kind)
        if provider is None:  # pragma: no cover — dispatch already checked
            self.abort(f"no provider {marker.kind!r} is installed")
            return
        params = provider.params_model.model_validate(marker.params)
        self._emit_event(
            events.ACTION_STARTED,
            {
                "provider": marker.kind,
                "block_index": self._block_index,
                "marker_id": marker.id,
                "frame": self._shots_fired + 1,
                "frames": self._repeat,
            },
        )
        self._last_provider = marker.kind
        self._job = self._actions.submit(
            marker.kind, params, self._context(), MARKER_TIMEOUT_S
        )

    def _dispatch(self, marker: EventMarker) -> _Dispatch:
        """Turn a stored marker into a provider call. Raises on bad params.

        Validated here as well as at the API boundary: a sequence can outlive
        the plugin version it was written against.
        """
        provider: ActionProvider | None = self._actions.provider(marker.kind)
        if provider is None:
            raise ActionError(f"no provider {marker.kind!r} is installed")
        params = provider.params_model.model_validate(marker.params)
        repeat, interval_s = 1, 0.0
        if marker.kind == SHUTTER_PROVIDER_ID and isinstance(params, ShutterParams):
            # The shutter's burst pacing (count/interval_s) is host policy, not
            # provider behaviour — one frame per submit, so an abort lands
            # between frames.
            repeat, interval_s = params.count, params.interval_s
        return _Dispatch(marker.kind, params, repeat, interval_s)

    # ── block completion ─────────────────────────────────────────────────────

    def _maybe_complete_block(self) -> None:
        block = self._current_block()
        if block is None:
            return
        # A marker still executing holds the block open past its commanded
        # duration — the block is stretched by reality, not silently truncated.
        if self._job is not None or self._active_marker is not None:
            return
        if isinstance(block, HoldBlock):
            if self._timing_started_at is None:
                return
            if self._t_in_block() >= block.duration_s:
                self._advance()
            return
        if self._arrived and self._t_in_block() >= block.duration_s:
            self._advance()
