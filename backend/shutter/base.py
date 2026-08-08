"""The shutter interface.

A shutter is an ESP32 pretending to be a Canon wireless remote over BLE, driven
from the host over USB CDC. From the executor's point of view it is three
operations that either work or raise.

Failures raise rather than returning a status. The executor has to distinguish
"carry on" from "stop the shoot", and a boolean return is exactly the shape
that gets dropped on the floor at a call site. The distinction between a dead
link and a camera that declined also matters: the first means every remaining
frame will fail too.

**There are two links, and they fail separately.** The host reaches the board
over USB; the board reaches the camera over BLE. ``is_connected`` and ``ping``
answer only for the first — deliberately, because the host has to be able to
tell a missing board from a sleeping camera. So the BLE half has its own two
operations: :meth:`ShutterDriver.pair`, which is how a camera gets attached at
all, and :meth:`ShutterDriver.camera_connected`, which is the only thing that
answers "will a frame actually be taken". Without them a self-test can report
a healthy chain while nothing is paired, and the first anyone hears of it is a
routine failing at the first anchor.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

#: How long the board scans for a camera in pairing mode. Long because a person
#: is standing there holding the camera and working through its menu.
PAIR_TIMEOUT_S = 30.0

#: Smart-mode pairing needs extra time for the user to confirm on the camera's
#: screen (up to 60 s for the confirmation dialog, plus scan time).
PAIR_SMART_TIMEOUT_S = 75.0

#: Camera status strings, matching the firmware's ``STATUS`` response.
#: Keeping them literals here rather than on the driver so the constants are
#: importable by the self-test endpoint without depending on the protocol layer.
CAMERA_STATUS_UNPAIRED = "unpaired"
CAMERA_STATUS_DISCONNECTED = "disconnected"
CAMERA_STATUS_CONNECTED = "connected"


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

    def pair(self, timeout_s: float = PAIR_TIMEOUT_S) -> None:
        """Put the board into BLE pairing mode and wait for a camera.

        Slow by nature — the camera has to be put into its own pairing mode by
        hand — and the one operation on this interface that needs a person in
        front of the machine.
        """
        ...

    def pair_smart(self, timeout_s: float = PAIR_SMART_TIMEOUT_S) -> None:
        """Put the board into smartphone-mode pairing.

        The camera must be in "connect to smartphone" mode (not "remote" mode).
        The user must confirm on the camera's screen within 60 s.
        """
        ...

    def camera_connected(self) -> bool:
        """Whether the board currently has the camera on BLE.

        Separate from :attr:`is_connected`, which is the USB link. Reported
        rather than raised: "no camera is paired" is a normal state on a machine
        being set up, not a fault to abort on.
        """
        ...

    def camera_status(self) -> str:
        """Three-state answer: connected / disconnected / unpaired.

        The self-test needs this to distinguish a camera that was never paired
        (needs a human with the menu) from one that is just sleeping (resolves
        itself on the next frame). Returns one of the ``CAMERA_STATUS_*``
        constants.
        """
        ...
