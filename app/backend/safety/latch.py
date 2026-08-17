"""Emergency stop, modelled as a cross-cutting latch.

Not a mode. The latch has to win from *any* mode, and making it a mode would
mean re-auditing every transition each time a mode is added. Instead the
control loop checks it first thing on every tick, and the API gates every
motion endpoint on it.

What "engaged" means here is **hold**, not **release**:

    The arm keeps its torque and stays pinned where it was. It does not go
    limp. Do not reach for ``RebotArm.estop()`` or ``disable_all()`` -- both
    are documented as "emergency stop" and both cut torque, which drops the
    arm. See docs/HARDWARE_NOTES.md.

This module is pure logic: it never touches hardware and never imports the arm
layer. The control loop supplies the frozen pose via :meth:`record_freeze_pose`
on the first tick after engaging, which keeps the latch testable without a
robot and keeps the freeze instant honest -- it is the moment the loop actually
noticed, not the moment the HTTP request landed.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Mapping


class LatchSource(str, Enum):
    """Who engaged the latch. Kept in the snapshot for the operator's benefit."""

    UI = "ui"
    API = "api"
    WATCHDOG = "watchdog"


@dataclass(frozen=True)
class LatchSnapshot:
    """Immutable view of the latch, safe to hand to the API and websocket."""

    latched: bool
    reason: str | None = None
    source: LatchSource | None = None
    engaged_at: float | None = None
    #: Joint angles at the instant the control loop froze, once it has ticked.
    #: ``None`` in the brief window between engaging and the next tick.
    freeze_pose: Mapping[str, float] | None = None


class SafetyLatch:
    """Thread-safe emergency-stop latch.

    Engaged from HTTP request threads and from the watchdog; read every tick by
    the control loop, so every mutation is under a lock.
    """

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.time
        self._lock = threading.RLock()
        self._latched = False
        self._reason: str | None = None
        self._source: LatchSource | None = None
        self._engaged_at: float | None = None
        self._freeze_pose: dict[str, float] | None = None

    @property
    def is_latched(self) -> bool:
        with self._lock:
            return self._latched

    def engage(self, reason: str, source: LatchSource | str) -> bool:
        """Engage the latch. Returns ``True`` only if this call was the one that engaged it.

        Re-engaging an already-latched stop keeps the *first* reason. The first
        cause is the diagnostic one; a watchdog firing a moment later because
        the arm stopped tracking is a symptom of it, and letting the symptom
        overwrite the cause is how an operator ends up debugging the wrong
        thing.

        ``source`` is coerced at this boundary. ``LatchSource`` is a ``str``
        enum, so a bare string would sail through unnoticed and then fail deep
        in whatever later reads ``.value`` -- a long way from the mistake.
        """
        if not reason:
            raise ValueError("engage() requires a reason — it is what the operator reads")
        source = LatchSource(source)

        with self._lock:
            if self._latched:
                return False
            self._latched = True
            self._reason = reason
            self._source = source
            self._engaged_at = self._clock()
            self._freeze_pose = None
            return True

    def record_freeze_pose(self, joints: Mapping[str, float]) -> None:
        """Record the pose the arm is being held at. First call after engaging wins.

        Called by the control loop, not by the API. Ignored when not latched, so
        a late tick racing a clear cannot resurrect stale state.
        """
        with self._lock:
            if not self._latched or self._freeze_pose is not None:
                return
            self._freeze_pose = dict(joints)

    def clear(self) -> bool:
        """Release the latch. Returns ``True`` only if it was actually latched.

        Clearing is idempotent so an operator mashing the button, or a retrying
        client, cannot produce an error. Clearing does **not** resume anything:
        by the time a stop is cleared the scene has usually changed -- someone
        has dragged the arm aside, or taken the subject away -- and auto-resuming
        into that is how you get a collision.
        """
        with self._lock:
            if not self._latched:
                return False
            self._latched = False
            self._reason = None
            self._source = None
            self._engaged_at = None
            self._freeze_pose = None
            return True

    def snapshot(self) -> LatchSnapshot:
        with self._lock:
            return LatchSnapshot(
                latched=self._latched,
                reason=self._reason,
                source=self._source,
                engaged_at=self._engaged_at,
                freeze_pose=dict(self._freeze_pose) if self._freeze_pose is not None else None,
            )
