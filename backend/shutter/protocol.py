"""The host-to-ESP32 line protocol.

ASCII, one line per message, request/response with an explicit id::

    host -> board:  #7 SHOOT
    board -> host:  #7 OK
                    #7 ERR camera not connected

The id is the whole point. Without it, a reply that arrived late -- after the
host had already timed out and moved on -- gets matched against the *next*
request and reported as its success. That failure mode looks like an
intermittently missing frame and is close to impossible to diagnose on site.

The board also announces itself unprompted on boot::

    board -> host:  READY 1.0.0

which is how the host learns it has been reset (and therefore lost its BLE
pairing) without polling for it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Commands the firmware understands.
PING = "PING"
FOCUS = "FOCUS"
SHOOT = "SHOOT"
PAIR = "PAIR"
STATUS = "STATUS"

#: What ``STATUS`` answers when the board holds the camera on BLE. The firmware
#: prints this word (firmware/esp32-shutter/src/main.cpp). Mirrored in
#: ``backend/shutter/base`` as ``CAMERA_STATUS_CONNECTED`` — the driver and self-
#: test pull from there, not from the protocol layer.
_CAMERA_CONNECTED = "connected"

_RESPONSE = re.compile(r"^#(?P<id>\d+)\s+(?P<status>OK|ERR)(?:\s+(?P<detail>.*))?$")
_READY = re.compile(r"^READY\s+(?P<version>\S+)\s*$")


@dataclass(frozen=True)
class Response:
    request_id: int
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class Ready:
    """Unsolicited boot banner. Means the board reset and lost its pairing."""

    version: str


def encode(request_id: int, command: str) -> bytes:
    if request_id < 0:
        raise ValueError("request id must not be negative")
    if not command or any(c in command for c in "\r\n"):
        raise ValueError(f"invalid command: {command!r}")
    return f"#{request_id} {command}\n".encode()


def decode(line: str) -> Response | Ready | None:
    """Parse one line. ``None`` for anything unrecognised.

    Unknown lines are dropped rather than raised on: the firmware is free to
    log to the same serial port, and a stray debug print must not take down the
    shutter link mid-shoot.
    """
    line = line.strip()
    if not line:
        return None

    ready = _READY.match(line)
    if ready:
        return Ready(version=ready.group("version"))

    match = _RESPONSE.match(line)
    if not match:
        return None

    return Response(
        request_id=int(match.group("id")),
        ok=match.group("status") == "OK",
        detail=(match.group("detail") or "").strip(),
    )


class LineReader:
    """Reassembles lines from arbitrary byte chunks.

    Serial reads split wherever they like: half a line, three lines at once, a
    line boundary in the middle of a chunk. Everything downstream assumes whole
    lines, so the splitting happens exactly here.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> list[str]:
        self._buffer.extend(chunk)
        lines = []
        while b"\n" in self._buffer:
            raw, _, rest = self._buffer.partition(b"\n")
            self._buffer = bytearray(rest)
            lines.append(raw.decode("utf-8", errors="replace").strip())
        return lines

    def reset(self) -> None:
        self._buffer.clear()

    @property
    def pending(self) -> bytes:
        return bytes(self._buffer)
