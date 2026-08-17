"""The float/lock decision behind drag teaching.

Ported from upstream's ``example/10_gravity_compensation_lock.py`` and pulled
out as pure logic, because the thresholds will have to be retuned once a Canon
body hangs off the end effector and retuning is only safe with tests.

The rule upstream implements: while the end effector is moving faster than a
threshold, the position target follows the arm, so the operator can drag it
freely. Below the threshold the target freezes and the arm holds where it was
left.

Two things are added here, both because the naive version misbehaves in the
hand:

**Hysteresis.** With a single threshold, a hand resting on a stationary arm
produces speeds that hover right at the boundary, so the arm alternates between
free and locked several times a second and feels like it is fighting back.
Releasing requires clearly exceeding the threshold; locking requires clearly
falling below it.

**A minimum still time.** Hand motion is not smooth. Mid-drag the operator
passes through zero velocity at every direction change, and locking on those
would stop the arm dead halfway through a move.

Velocity is supplied by the caller, and it must be finite-differenced from
position: ``mechVel (0x701A)`` is not rad/s on this firmware, so reading it
would make this decision on numbers that mean nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Upstream's thresholds, on an unloaded arm.
DEFAULT_LINEAR_THRESHOLD = 0.04  # m/s
DEFAULT_ANGULAR_THRESHOLD = 0.08  # rad/s

#: Release needs this multiple of the threshold; locking needs this fraction.
#: The gap between them is the hysteresis band.
RELEASE_FACTOR = 1.0
LOCK_FACTOR = 0.6

#: How long the arm must stay slow before the target freezes.
DEFAULT_MIN_STILL_S = 0.25


@dataclass(frozen=True)
class FloatLockConfig:
    linear_threshold: float = DEFAULT_LINEAR_THRESHOLD
    angular_threshold: float = DEFAULT_ANGULAR_THRESHOLD
    release_factor: float = RELEASE_FACTOR
    lock_factor: float = LOCK_FACTOR
    min_still_s: float = DEFAULT_MIN_STILL_S


class FloatLock:
    """Decides whether the position target should follow the arm or freeze.

    Starts locked: an arm that goes free the instant teaching is enabled would
    sag before anyone has hold of it.
    """

    def __init__(self, config: FloatLockConfig | None = None) -> None:
        self.config = config or FloatLockConfig()
        self._following = False
        self._slow_since: float | None = None

    @property
    def is_following(self) -> bool:
        """True while the target tracks the arm — i.e. the operator is dragging."""
        return self._following

    def reset(self) -> None:
        self._following = False
        self._slow_since = None

    def update(self, linear_speed: float, angular_speed: float, now: float) -> bool:
        """Feed one end-effector velocity sample. Returns :attr:`is_following`."""
        config = self.config

        if not self._following:
            moving = (
                linear_speed > config.linear_threshold * config.release_factor
                or angular_speed > config.angular_threshold * config.release_factor
            )
            if moving:
                self._following = True
                self._slow_since = None
            return self._following

        still = (
            linear_speed < config.linear_threshold * config.lock_factor
            and angular_speed < config.angular_threshold * config.lock_factor
        )
        if not still:
            # Direction changes pass through zero; only a sustained stop counts.
            self._slow_since = None
            return True

        if self._slow_since is None:
            self._slow_since = now
        elif now - self._slow_since >= config.min_still_s:
            self._following = False
            self._slow_since = None

        return self._following
