"""Joint position bounds loaded from the selected RS URDF."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Mapping
from xml.etree import ElementTree

from .. import assets

LIMIT_TOLERANCE_RAD = 0.02

@lru_cache(maxsize=1)
def urdf_joint_bounds(path: str | None = None) -> Mapping[str, tuple[float, float]]:
    urdf = Path(path) if path is not None else assets.urdf_path()
    root = ElementTree.parse(urdf).getroot()
    bounds: dict[str, tuple[float, float]] = {}
    for joint in root.findall("joint"):
        name = joint.get("name")
        limit = joint.find("limit")
        if not name or limit is None or limit.get("lower") is None or limit.get("upper") is None:
            continue
        bounds[name] = (float(limit.get("lower")), float(limit.get("upper")))
    return bounds


def expanded_joint_bounds(
    path: str | None = None, tolerance: float = LIMIT_TOLERANCE_RAD
) -> Mapping[str, tuple[float, float]]:
    """Return URDF bounds with the shared encoder noise tolerance applied."""
    if tolerance < 0:
        raise ValueError("joint bound tolerance must be non-negative")
    return {
        name: (lower - tolerance, upper + tolerance)
        for name, (lower, upper) in urdf_joint_bounds(path).items()
    }
