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

from .base import PAIR_TIMEOUT_S, ShutterError, ShutterNotConnected, ShutterTimeout

log = logging.getLogger(__name__)


class SimShutter:
    """In-memory shutter implementing :class:`~backend.shutter.base.ShutterDriver`."""

    def __init__(self, connected: bool = True, camera: bool = True) -> None:
        self._lock = threading.RLock()
        self._connected = connected
        #: The BLE half. Starts attached so the ordinary development loop is
        #: one step, and can be turned off to walk the setup flow: an operator
        #: meeting this machine for the first time has no camera paired, and
        #: that path needs to be reachable without a board and a Canon body.
        self._camera = camera
        self._pair_fails = False
        #: Scripted outcomes, consumed one per shoot(). None means success.
        self._scripted: deque[ShutterError | None] = deque()
        self.shots = 0
        self.focuses = 0
        self.pings = 0
        self.pairs = 0

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
            self._require_camera()
            self.focuses += 1
            log.debug("sim shutter: focus (%d)", self.focuses)

    def shoot(self) -> None:
        with self._lock:
            self._require_camera()
            outcome = self._scripted.popleft() if self._scripted else None
            if outcome is not None:
                log.debug("sim shutter: scripted failure %r", outcome)
                raise outcome
            self.shots += 1
            log.debug("sim shutter: shot (%d)", self.shots)

    def pair(self, timeout_s: float = PAIR_TIMEOUT_S) -> None:
        """Attach the imaginary camera. Returns at once — the thirty seconds a
        real one spends scanning are a person working a camera menu, and making
        a test wait them out would teach nothing."""
        with self._lock:
            self.pairs += 1
            self._require_link()
            if self._pair_fails:
                raise ShutterTimeout("no camera found in pairing mode")
            self._camera = True
            log.debug("sim shutter: paired (%d)", self.pairs)

    def camera_connected(self) -> bool:
        with self._lock:
            return self._connected and self._camera

    # ── simulation control ───────────────────────────────────────────────────

    def set_connected(self, connected: bool) -> None:
        with self._lock:
            self._connected = connected

    def set_camera_connected(self, camera: bool, pair_fails: bool = False) -> None:
        """Detach or attach the camera, and optionally make pairing fail.

        Two knobs rather than one because they are two situations: a camera that
        went to sleep still pairs again, while a camera that is not in pairing
        mode does not.
        """
        with self._lock:
            self._camera = camera
            self._pair_fails = pair_fails

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

    def _require_camera(self) -> None:
        """Both links, in the order the real chain fails in.

        The firmware answers ``ERR camera not connected`` rather than dropping
        the frame, so the simulator raises here for the same reason: an unpaired
        camera has to be a loud failure, never a silent no-op that leaves a
        routine reporting frames it never took.
        """
        self._require_link()
        if not self._camera:
            raise ShutterError("camera not connected")
