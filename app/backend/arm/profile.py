"""Small analytical quintic motion profiles used by both arm drivers."""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

import numpy as np


@dataclass(frozen=True)
class MotionLimits:
    velocity: float = 0.25
    acceleration: float = 0.5
    jerk: float = 2.0

    def __post_init__(self) -> None:
        if not all(np.isfinite(x) and x > 0 for x in (self.velocity, self.acceleration, self.jerk)):
            raise ValueError("motion limits must be finite and positive")


DEFAULT_LIMITS = MotionLimits()


class MotionRejected(RuntimeError):
    """A candidate motion cannot satisfy the configured analytic limits."""


@dataclass(frozen=True)
class Quintic:
    """A boundary-value polynomial in normalized time ``u=t/duration``."""

    q0: float
    v0: float
    a0: float
    q1: float
    duration: float
    coefficients: tuple[float, ...]

    @classmethod
    def create(cls, q0: float, v0: float, a0: float, q1: float, duration: float) -> "Quintic":
        if not all(np.isfinite(x) for x in (q0, v0, a0, q1)):
            raise MotionRejected("motion boundary values must be finite")
        if duration <= 0 or not np.isfinite(duration):
            raise MotionRejected("duration must be finite and positive")
        d = float(duration)
        # Coefficients are in u, therefore derivatives below are converted by d.
        c0, c1, c2 = q0, v0 * d, 0.5 * a0 * d * d
        r0 = q1 - (c0 + c1 + c2)
        r1 = -c1 - 2 * c2
        r2 = -2 * c2
        c3 = 10 * r0 - 4 * r1 + 0.5 * r2
        c4 = -15 * r0 + 7 * r1 - r2
        c5 = 6 * r0 - 3 * r1 + 0.5 * r2
        return cls(q0, v0, a0, q1, d, (c0, c1, c2, c3, c4, c5))

    def eval(self, elapsed: float) -> tuple[float, float, float, float]:
        u = min(max(float(elapsed) / self.duration, 0.0), 1.0)
        c = np.asarray(self.coefficients)
        q = float(np.polynomial.polynomial.polyval(u, c))
        d1 = float(np.polynomial.polynomial.polyval(u, np.arange(1, 6) * c[1:])) / self.duration
        d2 = float(np.polynomial.polynomial.polyval(u, np.arange(2, 6) * np.arange(1, 5) * c[2:])) / self.duration**2
        d3 = float(np.polynomial.polynomial.polyval(u, np.array([6, 24, 60]) * c[3:])) / self.duration**3
        return q, d1, d2, d3

    def extrema(self) -> tuple[float, float, float, float]:
        c = np.asarray(self.coefficients, dtype=float)
        points = [0.0, 1.0]
        for order in (1, 2, 3, 4):
            d = np.polynomial.polynomial.polyder(c, order)
            for root in np.roots(d[::-1]):
                if abs(root.imag) < 1e-8 and 0.0 < root.real < 1.0:
                    points.append(float(root.real))
        values = [self.eval(u * self.duration) for u in points]
        return (max(abs(v[1]) for v in values), max(abs(v[2]) for v in values), max(abs(v[3]) for v in values), max(v[0] for v in values) - min(v[0] for v in values))

    def position_extrema(self) -> tuple[float, float]:
        c = np.asarray(self.coefficients, dtype=float)
        points = [0.0, 1.0]
        for root in np.roots(np.polynomial.polynomial.polyder(c)[::-1]):
            if abs(root.imag) < 1e-8 and 0.0 < root.real < 1.0:
                points.append(float(root.real))
        values = [self.eval(u * self.duration)[0] for u in points]
        return min(values), max(values)


@dataclass(frozen=True)
class PreparedMotion:
    requested: Mapping[str, float]
    target: Mapping[str, float]
    duration: float
    generation: int
    profiles: Mapping[str, Quintic]
    samples: tuple[Mapping[str, float], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "requested", MappingProxyType(dict(self.requested)))
        object.__setattr__(self, "target", MappingProxyType(dict(self.target)))
        object.__setattr__(self, "profiles", MappingProxyType(dict(self.profiles)))


def minimum_duration(displacements: Mapping[str, float], limits: MotionLimits = DEFAULT_LIMITS) -> float:
    result = 0.0
    for delta in displacements.values():
        distance = abs(float(delta))
        result = max(result, 1.875 * distance / limits.velocity)
        result = max(result, np.sqrt((10.0 / np.sqrt(3.0)) * distance / limits.acceleration))
        result = max(result, np.cbrt(60.0 * distance / limits.jerk))
    return float(result)


def prepare_profiles(
    starts: Mapping[str, tuple[float, float, float]],
    target: Mapping[str, float],
    requested_duration: float,
    generation: int,
    limits: MotionLimits = DEFAULT_LIMITS,
    joint_bounds: Mapping[str, tuple[float, float]] | None = None,
) -> PreparedMotion:
    if requested_duration <= 0 or not np.isfinite(requested_duration):
        raise MotionRejected("duration must be finite and positive")
    if any(not np.isfinite(v) for start in starts.values() for v in start) or any(not np.isfinite(v) for v in target.values()):
        raise MotionRejected("motion values must be finite")
    if joint_bounds is not None and any(lo > hi or not np.isfinite(lo) or not np.isfinite(hi) for lo, hi in joint_bounds.values()):
        raise MotionRejected("joint bounds must be finite and ordered")
    duration = max(float(requested_duration), minimum_duration({n: target[n] - starts[n][0] for n in target}, limits))
    for _ in range(32):
        profiles = {n: Quintic.create(*starts[n], target[n], duration) for n in target}
        within_derivatives = all((p.extrema()[0] <= limits.velocity + 1e-8 and p.extrema()[1] <= limits.acceleration + 1e-8 and p.extrema()[2] <= limits.jerk + 1e-8) for p in profiles.values())
        within_positions = joint_bounds is None or all(
            name not in joint_bounds or (joint_bounds[name][0] - 1e-9 <= p.position_extrema()[0] and p.position_extrema()[1] <= joint_bounds[name][1] + 1e-9)
            for name, p in profiles.items()
        )
        if within_derivatives and within_positions:
            samples = tuple(MappingProxyType({n: p.eval(t)[0] for n, p in profiles.items()}) for t in np.linspace(0.0, duration, 21))
            return PreparedMotion(dict(target), target, duration, generation, profiles, samples)
        duration *= 1.25
    raise MotionRejected("motion cannot satisfy velocity, acceleration and jerk limits")
