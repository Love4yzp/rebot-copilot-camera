"""A simulated arm.

Not a testing afterthought. SimArm is the substrate for the whole no-hardware
development loop -- teach capture, executor timing, playback, and the "engage
the stop mid-playback" integration test all run on it. The previous generation
of this service had the same idea as a convenience; here it is load-bearing.

The model is deliberately crude: a first-order lag toward the commanded target,
plus an injectable "someone is pulling on it" term. That is enough to exercise
every piece of logic that sits above the arm, and pretending to more fidelity
than that would only invite trusting it.

Time is injected. Nothing here sleeps, and no test that uses it should either.
"""

from __future__ import annotations

import math
import threading
from typing import Callable, Mapping, Sequence

from .base import ArmState

#: Time constant of the first-order lag, in seconds. Roughly "how long until it
#: has covered 63% of the distance to target".
DEFAULT_TAU = 0.15


class SimArm:
    """In-memory arm implementing :class:`~backend.arm.base.ArmDriver`."""

    def __init__(
        self,
        joint_names: Sequence[str],
        clock: Callable[[], float],
        initial: Mapping[str, float] | None = None,
        tau: float = DEFAULT_TAU,
    ) -> None:
        if tau <= 0:
            raise ValueError("tau must be positive")

        self._joint_names = tuple(joint_names)
        self._clock = clock
        self._tau = tau
        self._lock = threading.RLock()

        start = dict.fromkeys(self._joint_names, 0.0)
        if initial:
            unknown = set(initial) - set(self._joint_names)
            if unknown:
                raise KeyError(f"unknown joints: {sorted(unknown)}")
            start.update(initial)

        self._q: dict[str, float] = start
        self._q_target: dict[str, float] = dict(start)
        self._floating = False
        self._connected = False

        # Previous sample, for finite-differencing velocity. `None` until the
        # first step, which is why ArmState.velocities can be empty.
        self._prev_q: dict[str, float] | None = None
        self._prev_t: float | None = None
        self._velocities: dict[str, float] = {}
        self._t = clock()

    # ── ArmDriver ────────────────────────────────────────────────────────────

    @property
    def joint_names(self) -> Sequence[str]:
        return self._joint_names

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return self._connected

    def connect(self) -> None:
        with self._lock:
            self._connected = True

    def disconnect(self) -> None:
        with self._lock:
            self._connected = False

    def read_state(self) -> ArmState:
        with self._lock:
            return ArmState(
                positions=dict(self._q),
                velocities=dict(self._velocities),
                t=self._t,
            )

    def hold(self, q_target: Mapping[str, float]) -> None:
        with self._lock:
            unknown = set(q_target) - set(self._joint_names)
            if unknown:
                raise KeyError(f"unknown joints: {sorted(unknown)}")
            self._q_target.update(q_target)

    def move_to(self, q_target: Mapping[str, float], duration_s: float) -> None:
        """Approximate a timed move with the first-order lag.

        The simulator does not plan a trajectory; it just retargets, and the
        lag gets it there. That is enough to exercise arrival detection, settle
        timing and action sequencing, which is what the executor tests need.
        Real timing fidelity belongs to the hardware arm's trajectory planner.
        """
        if duration_s <= 0:
            raise ValueError("duration_s must be positive")
        self.hold(q_target)

    def set_float(self, enabled: bool) -> None:
        """Enter or leave float. Leaving re-targets wherever the arm now is.

        That re-target is the "let go and it stays put" behaviour, and getting
        it wrong on hardware means the arm snaps back to a stale target the
        moment the operator releases it.
        """
        with self._lock:
            self._floating = enabled
            if not enabled:
                self._q_target = dict(self._q)

    # ── simulation control (not part of ArmDriver) ───────────────────────────

    @property
    def is_floating(self) -> bool:
        with self._lock:
            return self._floating

    def drag(self, delta: Mapping[str, float]) -> None:
        """Simulate a human pushing the arm by ``delta`` radians per joint.

        Only has an effect while floating: a held arm resists. That asymmetry
        is the whole point of the teach workflow, so the simulator enforces it
        rather than letting tests drag a locked arm and believe the result.
        """
        with self._lock:
            if not self._floating:
                return
            unknown = set(delta) - set(self._joint_names)
            if unknown:
                raise KeyError(f"unknown joints: {sorted(unknown)}")
            for name, d in delta.items():
                self._q[name] += d
            # A dragged arm's target follows it, so releasing holds it here.
            self._q_target = dict(self._q)

    def step(self, dt: float) -> None:
        """Advance the simulation by ``dt`` seconds.

        Driven by the caller rather than by wall time, so tests stay
        deterministic and never sleep.
        """
        if dt < 0:
            raise ValueError("dt must not be negative")

        with self._lock:
            prev_q = dict(self._q)
            prev_t = self._t

            if not self._floating and dt > 0:
                # Exponential approach: exact solution of q' = (target - q)/tau,
                # so the result does not depend on how finely dt is chopped.
                alpha = 1.0 - math.exp(-dt / self._tau)
                for name in self._joint_names:
                    self._q[name] += (self._q_target[name] - self._q[name]) * alpha

            self._t = prev_t + dt
            self._velocities = (
                {n: (self._q[n] - prev_q[n]) / dt for n in self._joint_names} if dt > 0 else {}
            )
            self._prev_q, self._prev_t = prev_q, prev_t
