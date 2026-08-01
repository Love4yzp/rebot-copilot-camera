"""Shutter driver talking to a XIAO ESP32-S3 over USB CDC.

One request in flight at a time. The board is a shutter button, not a server;
pipelining would buy nothing and would make the late-reply problem worse.

Transport is injected so the protocol can be tested against an in-memory pipe.
Everything about serial ports that is worth getting wrong -- partial lines,
coalesced lines, late replies, the port vanishing when someone knocks the USB
cable -- is reachable without a board.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Protocol

from .base import ShutterError, ShutterNotConnected, ShutterTimeout
from .protocol import FOCUS, PAIR, PING, SHOOT, STATUS, LineReader, Ready, Response, decode, encode

log = logging.getLogger(__name__)

DEFAULT_BAUD = 115_200
DEFAULT_TIMEOUT_S = 3.0
#: Shooting can wait on the camera waking up over BLE, so it gets longer.
SHOOT_TIMEOUT_S = 6.0


class Transport(Protocol):
    """The slice of a serial port this driver needs."""

    @property
    def is_open(self) -> bool: ...

    def write(self, data: bytes) -> None: ...

    def read(self, size: int = 1024) -> bytes:
        """Return whatever is available, possibly empty. Must not block long."""
        ...

    def close(self) -> None: ...


class Esp32Shutter:
    """Line-protocol client for the shutter board."""

    def __init__(
        self,
        open_transport: Callable[[], Transport],
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self._open_transport = open_transport
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._timeout_s = timeout_s

        self._lock = threading.RLock()
        self._transport: Transport | None = None
        self._reader = LineReader()
        self._next_id = 1

        self.firmware_version: str | None = None
        #: Replies that arrived after their request had already timed out.
        #: Non-zero here means the timeouts are too tight, not that the board
        #: is broken -- worth surfacing rather than silently discarding.
        self.stale_replies = 0

    # ── connection ───────────────────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return self._transport is not None and self._transport.is_open

    def connect(self) -> None:
        with self._lock:
            if self.is_connected:
                return
            self._transport = self._open_transport()
            self._reader.reset()
            log.info("shutter transport opened")

    def close(self) -> None:
        with self._lock:
            if self._transport is not None:
                self._transport.close()
                self._transport = None
            self._reader.reset()

    def _ensure_connected(self) -> Transport:
        if not self.is_connected:
            # A knocked USB cable should cost one command, not the shoot.
            try:
                self.connect()
            except Exception as exc:
                raise ShutterNotConnected(f"cannot open shutter transport: {exc}") from exc
        assert self._transport is not None
        return self._transport

    # ── ShutterDriver ────────────────────────────────────────────────────────

    def ping(self) -> None:
        self._command(PING)

    def focus(self) -> None:
        self._command(FOCUS)

    def shoot(self) -> None:
        self._command(SHOOT, timeout_s=SHOOT_TIMEOUT_S)

    # ── extras ───────────────────────────────────────────────────────────────

    def pair(self, timeout_s: float = 30.0) -> None:
        """Put the board into BLE pairing mode. Slow by nature — a human is
        holding a camera and pressing buttons on it."""
        self._command(PAIR, timeout_s=timeout_s)

    def status(self) -> str:
        return self._command(STATUS).detail

    # ── the exchange ─────────────────────────────────────────────────────────

    def _command(self, command: str, timeout_s: float | None = None) -> Response:
        deadline_s = timeout_s if timeout_s is not None else self._timeout_s

        with self._lock:
            transport = self._ensure_connected()
            request_id = self._next_id
            self._next_id += 1

            try:
                transport.write(encode(request_id, command))
            except Exception as exc:
                self.close()
                raise ShutterNotConnected(f"write failed: {exc}") from exc

            return self._await_reply(transport, request_id, command, deadline_s)

    def _await_reply(
        self, transport: Transport, request_id: int, command: str, timeout_s: float
    ) -> Response:
        deadline = self._clock() + timeout_s

        while self._clock() < deadline:
            try:
                chunk = transport.read()
            except Exception as exc:
                self.close()
                raise ShutterNotConnected(f"read failed: {exc}") from exc

            if not chunk:
                self._sleep(0.005)
                continue

            for line in self._reader.feed(chunk):
                message = decode(line)

                if isinstance(message, Ready):
                    # The board reset mid-exchange; its BLE pairing is gone, so
                    # the in-flight command is not merely late, it is void.
                    self.firmware_version = message.version
                    raise ShutterError(f"board reset during {command} (firmware {message.version})")

                if message is None:
                    log.debug("shutter: ignoring unrecognised line %r", line)
                    continue

                if message.request_id != request_id:
                    # A reply to something we already gave up on. Counting it
                    # rather than matching it is the whole reason ids exist.
                    self.stale_replies += 1
                    log.warning(
                        "shutter: discarding stale reply #%d while awaiting #%d",
                        message.request_id,
                        request_id,
                    )
                    continue

                if message.ok:
                    return message
                raise ShutterError(f"{command} failed: {message.detail or 'no reason given'}")

        raise ShutterTimeout(f"{command} timed out after {timeout_s:.1f}s")
