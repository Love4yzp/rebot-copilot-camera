"""Choosing between the real shutter board and the simulator.

The counterpart of :mod:`backend.arm.factory`, with one deliberate difference:
**this one never falls back.**

Falling back to a simulated arm is right — the whole service needs an arm, and
every workflow above it can be exercised without one. Falling back to a
simulated shutter is not. A SimShutter reports every frame as fired, so an
operator would walk a whole set believing they had the shots while nothing
reached the card. That is the most expensive failure this workflow has, and it
is the one the driver's exceptions exist to make loud.

So a shutter is simulated only when someone asked for that (``--sim``).
Otherwise the real driver is returned unopened: :class:`Esp32Shutter` connects
lazily and raises ``ShutterNotConnected`` on the first command it cannot send,
which surfaces as a failed action and a red self-test rather than as silence.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Callable

from .base import ShutterDriver
from .esp32 import DEFAULT_BAUD, Esp32Shutter, Transport
from .sim import SimShutter

log = logging.getLogger(__name__)

#: udev gives the XIAO board this stable name (deploy/99-rebot-usb.rules).
#: Both it and the USB2CAN bridge enumerate as generic CDC devices, so raw
#: /dev/ttyACM* numbering swaps with plug order — pointing the shutter driver
#: at the CAN bridge is a failure that looks like a dead camera.
DEFAULT_PORT = "/dev/rebot-shutter"


class SerialTransport:
    """pyserial behind the slice of a port :class:`Esp32Shutter` needs.

    Opened non-blocking: the driver polls and owns its own deadline, so a read
    that waited here would move the timeout somewhere the driver cannot see.
    """

    def __init__(self, port: str, baud: int = DEFAULT_BAUD) -> None:
        import serial  # imported here so --sim needs no serial stack at all

        self._serial = serial.Serial(port=port, baudrate=baud, timeout=0, write_timeout=2)

    @property
    def is_open(self) -> bool:
        return bool(self._serial.is_open)

    def write(self, data: bytes) -> None:
        self._serial.write(data)
        self._serial.flush()

    def read(self, size: int = 1024) -> bytes:
        waiting = self._serial.in_waiting
        return self._serial.read(min(size, waiting)) if waiting else b""

    def close(self) -> None:
        self._serial.close()


def create_shutter(
    force_sim: bool = False,
    port: str | None = None,
    clock: Callable[[], float] | None = None,
) -> tuple[ShutterDriver, bool]:
    """Return ``(shutter, is_simulated)``.

    ``force_sim`` is the only way to get a simulator; see the module docstring.
    """
    if force_sim:
        log.info("sim mode requested: using SimShutter")
        return SimShutter(), True

    port = port or os.environ.get("REBOT_SHUTTER_PORT", DEFAULT_PORT)
    log.info("shutter: %s (opened on first command)", port)
    return (
        Esp32Shutter(
            open_transport=lambda: SerialTransport(port),
            clock=clock or time.monotonic,
        ),
        False,
    )


__all__ = ["DEFAULT_PORT", "SerialTransport", "Transport", "create_shutter"]
