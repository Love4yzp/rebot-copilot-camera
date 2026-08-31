"""Client liveness: silence during play/teach becomes a fault.

Idle and rest are not judged — an unattended holding arm is the default.
The controller maps expiry to SafeLock; this module only tracks time.
"""

from __future__ import annotations

from collections.abc import Callable


class ClientWatchdog:
    def __init__(self, clock: Callable[[], float], timeout_s: float = 2.0) -> None:
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        self._clock = clock
        self.timeout_s = timeout_s
        self._last = clock()

    def feed(self) -> None:
        self._last = self._clock()

    def expired(self) -> bool:
        return self._clock() - self._last >= self.timeout_s
