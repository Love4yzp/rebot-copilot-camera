"""A simulated shutter.

Like SimArm, this is development infrastructure rather than a test fixture: the
whole photography workflow can be exercised with no ESP32 and no camera.

It counts what it was asked to do and can be scripted to fail, which is the
only way to reach the executor's abort/skip/retry branches without unplugging
real hardware mid-test.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Iterable

from .base import ShutterError, ShutterNotConnected

log = logging.getLogger(__name__)


class SimShutter:
    """In-memory shutter implementing :class:`~backend.shutter.base.ShutterDriver`."""

    def __init__(self, connected: bool = True) -> None:
        self._lock = threading.RLock()
        self._connected = connected
        #: Scripted outcomes, consumed one per shoot(). None means success.
        self._scripted: deque[ShutterError | None] = deque()
        self.shots = 0
        self.focuses = 0
        self.pings = 0

    # ── ShutterDriver ────────────────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return self._connected

    def ping(self) -> None:
        with self._lock:
            self.pings += 1
            self._require_link()

    def focus(self) -> None:
        with self._lock:
            self._require_link()
            self.focuses += 1
            log.debug("sim shutter: focus (%d)", self.focuses)

    def shoot(self) -> None:
        with self._lock:
            self._require_link()
            outcome = self._scripted.popleft() if self._scripted else None
            if outcome is not None:
                log.debug("sim shutter: scripted failure %r", outcome)
                raise outcome
            self.shots += 1
            log.debug("sim shutter: shot (%d)", self.shots)

    # ── simulation control ───────────────────────────────────────────────────

    def set_connected(self, connected: bool) -> None:
        with self._lock:
            self._connected = connected

    def script(self, outcomes: Iterable[ShutterError | None]) -> None:
        """Queue outcomes for the next ``shoot()`` calls, oldest first.

        ``None`` means success, so a retry test reads as
        ``script([ShutterTimeout(...), None])`` -- fails once, then works.
        Calls past the end of the script succeed.
        """
        with self._lock:
            self._scripted.extend(outcomes)

    def _require_link(self) -> None:
        if not self._connected:
            raise ShutterNotConnected("no link to the ESP32 shutter")
