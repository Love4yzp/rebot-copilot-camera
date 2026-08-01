"""The shutter interface.

A shutter is an ESP32 pretending to be a Canon wireless remote over BLE, driven
from the host over USB CDC. From the executor's point of view it is three
operations that either work or raise.

Failures raise rather than returning a status. The executor has to distinguish
"carry on" from "stop the shoot", and a boolean return is exactly the shape
that gets dropped on the floor at a call site. The distinction between a dead
link and a camera that declined also matters: the first means every remaining
frame will fail too.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class ShutterError(Exception):
    """The shutter did not do what was asked."""


class ShutterNotConnected(ShutterError):
    """No link to the ESP32. Every subsequent frame will fail the same way."""


class ShutterTimeout(ShutterError):
    """The ESP32 did not answer in time — link up, camera possibly asleep."""


@runtime_checkable
class ShutterDriver(Protocol):
    @property
    def is_connected(self) -> bool: ...

    def ping(self) -> None:
        """Check the host-to-ESP32 link. Says nothing about the camera."""
        ...

    def focus(self) -> None:
        """Half-press. Needed before a shot when the camera is on autofocus."""
        ...

    def shoot(self) -> None:
        """Full-press. Raises rather than silently missing a frame."""
        ...
