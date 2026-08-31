"""Contact: residual torque past a dwell window.

Measured torque minus model torque. A single noisy sample does not lock;
a sustained window does. The table maps the fault to SafeLock — this module
does not command the arm.

Default-off: uncalibrated gravity would otherwise lock on every unfolded pose.
Teach and idle are not sampled by the controller; only playing is.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence


class ContactObserver:
    def __init__(
        self,
        threshold_nm: float = 8.0,
        window_s: float = 0.05,
        *,
        enabled: bool = False,
        clock: Callable[[], float],
        model_torque: Callable[[Sequence[float], Sequence[float]], Sequence[float]] | None = None,
    ) -> None:
        self.threshold_nm = threshold_nm
        self.window_s = window_s
        self.enabled = enabled
        self._clock = clock
        self._model = model_torque
        self._over_since: float | None = None
        self.triggered = False

    def update(
        self,
        q: Sequence[float],
        v: Sequence[float],
        tau_measured: Sequence[float],
    ) -> bool:
        """One sample. True only on the rising edge of a trigger."""
        if not self.enabled or self.triggered:
            return False
        model = (
            list(self._model(q, v))
            if self._model is not None
            else [0.0] * len(tau_measured)
        )
        over = any(
            abs(tm - tm_model) > self.threshold_nm
            for tm, tm_model in zip(tau_measured, model, strict=False)
        )
        now = self._clock()
        if not over:
            self._over_since = None
            return False
        if self._over_since is None:
            self._over_since = now
            return False
        if now - self._over_since >= self.window_s:
            self.triggered = True
            return True
        return False

    def reset(self) -> None:
        self._over_since = None
        self.triggered = False
