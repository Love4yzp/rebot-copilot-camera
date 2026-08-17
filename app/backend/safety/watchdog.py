"""Automatic emergency stop.

The third way the latch gets engaged, after the UI button and the API. This one
fires when the loop notices the machine is no longer behaving, which is the
case a human is slowest to catch.

Three conditions, each chosen because it means the arm is no longer under
control rather than merely inconvenient:

* **The loop fell behind.** If ticks stop arriving on time, commands stop
  arriving on time. Transient jitter is normal, so it takes a sustained run of
  late ticks, not one.
* **Reads keep failing.** A few dropped CAN frames happen. A run of them means
  the loop is commanding an arm whose actual position it no longer knows.
* **The arm drifted while being held.** Only checked while *holding* -- during
  a move a large error is the whole point. Drift under a hold means torque was
  lost or something is pushing, and a 48 V arm carrying a camera should not be
  discovering that on its own.

Pure logic: injected clock, and it only touches the latch. No hardware, no
sleeping, so all three conditions are unit-testable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Mapping

from .latch import LatchSource, SafetyLatch

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class WatchdogConfig:
    #: A tick is "late" past this multiple of the expected period.
    late_tick_factor: float = 3.0
    #: How long ticks may keep arriving late before the stop engages.
    late_tick_grace_s: float = 0.5
    #: Consecutive failed reads tolerated before the stop engages.
    max_read_failures: int = 5
    #: Per-joint drift under a hold that counts as losing the arm, in radians.
    tracking_error_rad: float = 0.20
    #: How long that drift must persist. Long enough to ride out the settle
    #: after a move, short enough to catch a joint that has gone slack.
    tracking_grace_s: float = 0.5


class Watchdog:
    """Fed once per control tick; engages the latch when something is wrong."""

    def __init__(
        self,
        latch: SafetyLatch,
        clock: Callable[[], float],
        config: WatchdogConfig | None = None,
    ) -> None:
        self._latch = latch
        self._clock = clock
        self.config = config or WatchdogConfig()

        self._last_tick_at: float | None = None
        self._late_since: float | None = None
        self._read_failures = 0
        self._drift_since: float | None = None

    def reset(self) -> None:
        """Forget accumulated suspicion. Called when the stop is cleared."""
        self._last_tick_at = None
        self._late_since = None
        self._read_failures = 0
        self._drift_since = None

    # ── observations ─────────────────────────────────────────────────────────

    def observe_tick(self, expected_period_s: float) -> None:
        now = self._clock()
        previous, self._last_tick_at = self._last_tick_at, now
        if previous is None:
            return

        limit = expected_period_s * self.config.late_tick_factor
        if now - previous <= limit:
            self._late_since = None
            return

        if self._late_since is None:
            self._late_since = now
        elif now - self._late_since >= self.config.late_tick_grace_s:
            self._engage(
                f"control loop running late for {now - self._late_since:.2f}s "
                f"(last gap {now - previous:.3f}s, limit {limit:.3f}s)"
            )

    def observe_read(self, ok: bool) -> None:
        if ok:
            self._read_failures = 0
            return

        self._read_failures += 1
        if self._read_failures >= self.config.max_read_failures:
            self._engage(f"{self._read_failures} consecutive arm read failures")

    def observe_hold(
        self,
        positions: Mapping[str, float] | None,
        target: Mapping[str, float] | None,
    ) -> None:
        """Check drift under a hold. Pass ``None`` for either when not holding."""
        if positions is None or target is None:
            self._drift_since = None
            return

        worst_joint, worst_error = None, 0.0
        for name, want in target.items():
            error = abs(positions.get(name, want) - want)
            if error > worst_error:
                worst_joint, worst_error = name, error

        if worst_error <= self.config.tracking_error_rad:
            self._drift_since = None
            return

        now = self._clock()
        if self._drift_since is None:
            self._drift_since = now
        elif now - self._drift_since >= self.config.tracking_grace_s:
            self._engage(
                f"{worst_joint} drifted {worst_error:.3f} rad from its hold "
                f"for {now - self._drift_since:.2f}s"
            )

    def _engage(self, reason: str) -> None:
        if self._latch.engage(reason, LatchSource.WATCHDOG):
            log.error("watchdog engaged emergency stop: %s", reason)
