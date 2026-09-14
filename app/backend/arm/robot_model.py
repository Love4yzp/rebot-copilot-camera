"""Project-owned port for robot geometry and kinematics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol

import numpy as np


@dataclass(frozen=True)
class LimitViolation:
    joint: str
    value: float
    lower: float
    upper: float

    def __str__(self) -> str:
        return (
            f"{self.joint}={self.value:.4f} rad is outside "
            f"[{self.lower:.4f}, {self.upper:.4f}]"
        )


@dataclass(frozen=True)
class CollisionHit:
    first: str
    second: str

    def __str__(self) -> str:
        return f"{self.first} collides with {self.second}"


@dataclass(frozen=True)
class RobotPose:
    """Vendor-neutral rigid pose returned by forward kinematics."""

    translation: np.ndarray
    rotation: np.ndarray
    homogeneous: np.ndarray


class RobotModel(Protocol):
    """The narrow geometry surface used by safety and control code."""

    def check_limits(
        self, joints: Mapping[str, float], tolerance: float
    ) -> list[LimitViolation]: ...

    def check_self_collision(self, joints: Mapping[str, float]) -> list[CollisionHit]: ...

    def check_path(
        self,
        start: Mapping[str, float],
        end: Mapping[str, float],
        samples: int,
    ) -> list[CollisionHit]: ...

    def forward_kinematics(self, joints: Mapping[str, float]) -> RobotPose: ...

    def limits(self) -> dict[str, tuple[float, float]]: ...

    def collision_pair_count(self) -> int: ...
