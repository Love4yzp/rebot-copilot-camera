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

from .base import (
    CAMERA_STATUS_CONNECTED,
    CAMERA_STATUS_DISCONNECTED,
    CAMERA_STATUS_UNPAIRED,
    PAIR_SMART_TIMEOUT_S,
    PAIR_TIMEOUT_S,
    ShutterError,
    ShutterNotConnected,
    ShutterTimeout,
)

log = logging.getLogger(__name__)


class SimShutter:
    """In-memory shutter implementing :class:`~backend.shutter.base.ShutterDriver`.

    Three knobs separate ``paired`` (has a camera ever been stored) from
    ``connected`` (the BLE link is up right now) from ``connected`` (the USB
    link is up). The firmware's STATUS returns three states, and the self-test
    needs to distinguish them: "unpaired" means a human with the camera's menu,
    "disconnected" resolves itself on the next frame.
    """

    def __init__(self, connected: bool = True, paired: bool = True, camera: bool = True) -> None:
        self._lock = threading.RLock()
        self._connected = connected
        #: Has a camera ever been paired? Persists across reboots on the real
        #: board (NVS); on the sim it is set by :meth:`pair`.
        self._paired = paired
        #: The BLE link is currently up. Stays False on a freshly booted board
        #: even with a camera paired, because nothing connects until the first
        #: ``FOCUS`` or ``SHOOT`` does it lazily.
        self._camera = camera
        self._pair_fails = False
        self._unreachable = False
        #: The banner the self-test endpoint reports. Esp32Shutter fills this
        #: from the firmware's VERSION line; the sim has one from the start so
        #: the endpoint's response has the same shape on both.
        self.firmware_version: str | None = "sim-1.0.0"
        #: Scripted outcomes, consumed one per shoot(). None means success.
        self._scripted: deque[ShutterError | None] = deque()
        self.shots = 0
        self.focuses = 0
        self.pings = 0
        self.pairs = 0
        self.smart_pairs = 0

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
            self._require_paired()
            if self._unreachable:
                raise ShutterError("camera unreachable")
            if not self._camera:
                self._camera = True  # lazy BLE connect
            self.focuses += 1
            log.debug("sim shutter: focus (%d)", self.focuses)

    def shoot(self) -> None:
        with self._lock:
            self._require_link()
            self._require_paired()
            if self._unreachable:
                raise ShutterError("camera unreachable")
            if not self._camera:
                self._camera = True  # lazy BLE connect
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
            self._paired = True
            self._camera = True
            self._unreachable = False
            log.debug("sim shutter: paired (%d)", self.pairs)

    def pair_smart(self, timeout_s: float = PAIR_SMART_TIMEOUT_S) -> None:
        """Smartphone-mode pairing. Same as pair() for simulation purposes."""
        with self._lock:
            self.smart_pairs += 1
            self._require_link()
            if self._pair_fails:
                raise ShutterTimeout("no camera with smart service found")
            self._paired = True
            self._camera = True
            self._unreachable = False
            log.debug("sim shutter: smart paired (%d)", self.smart_pairs)

    def camera_connected(self) -> bool:
        with self._lock:
            return self._connected and self._paired and self._camera

    def camera_status(self) -> str:
        with self._lock:
            if not self._paired:
                return CAMERA_STATUS_UNPAIRED
            if not self._camera:
                return CAMERA_STATUS_DISCONNECTED
            return CAMERA_STATUS_CONNECTED

    # ── simulation control ───────────────────────────────────────────────────

    def set_connected(self, connected: bool) -> None:
        with self._lock:
            self._connected = connected

    def set_camera_connected(self, camera: bool, pair_fails: bool = False, unreachable: bool = False) -> None:
        """Detach or attach the camera BLE link, and set failure modes.

        Three knobs rather than one because they model three situations: a camera
        that went to sleep still pairs again (``camera=False``), a camera not in
        pairing mode does not (``pair_fails=True``), and a camera that is paired
        but cannot be reached (e.g. powered off) is a third (``unreachable=True``).
        """
        with self._lock:
            self._camera = camera
            self._pair_fails = pair_fails
            self._unreachable = unreachable

    def set_paired(self, paired: bool) -> None:
        """Set whether a camera has ever been paired.

        ``False`` means the board has no stored address, and a human must go
        through the camera's Bluetooth menu. On the real board this is what the
        ``READY`` banner conveys: the board reset and its NVS pairing is gone.
        """
        with self._lock:
            self._paired = paired

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

    def _require_paired(self) -> None:
        if not self._paired:
            raise ShutterError("no camera paired")
