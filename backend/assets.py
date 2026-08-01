"""Single source of truth for on-disk assets: the RS hardware config and URDF.

Nothing else in this codebase may hardcode a ``vendor/`` path, and nothing may
rely on the upstream library's *default* asset resolution. That second rule is
not style — it is a correctness trap:

    ``reBotArm_control_py`` resolves its default URDF through
    ``config/rebotarm.yaml``, whose ``hardware_yaml`` key ships pointing at
    ``rebotarm_dm.yaml`` — the **B601-DM** arm, a different robot with a
    different URDF (``reBot-DevArm_fixend.urdf``) and a different end-effector
    frame (``end_link``).

    Calling e.g. ``load_robot_model()`` with no argument therefore returns a
    perfectly valid model *of the wrong arm*. The file exists, so nothing
    raises. Forward kinematics, gravity compensation and collision checking all
    silently produce answers for hardware we do not have — and gravity
    compensation is exactly what the zero-force drag teaching mode rides on.

So: always pass ``urdf_path=str(urdf_path())`` explicitly to upstream calls.
``assert_rs_model()`` exists to make an accidental default fail loudly.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Upstream arm library, pinned as a git submodule. The URDF and its 30 STL
#: meshes (63 MB) are read from here rather than copied into this repo, so a
#: submodule bump can never leave a stale duplicate behind.
VENDOR_ROOT = REPO_ROOT / "vendor" / "reBotArm_control_py"

#: Our fork of the RS hardware config — retuned for the camera payload.
HARDWARE_YAML = REPO_ROOT / "config" / "rebotarm_rs.yaml"

#: Substring every RS URDF path must contain. Guards against a DM-arm mix-up.
_RS_URDF_MARKER = "00-arm-rs_asm-v3"


@lru_cache(maxsize=1)
def hardware_config() -> dict[str, Any]:
    """Parsed RS hardware config."""
    if not HARDWARE_YAML.exists():
        raise FileNotFoundError(f"Hardware config missing: {HARDWARE_YAML}")
    return yaml.safe_load(HARDWARE_YAML.read_text(encoding="utf-8")) or {}


def urdf_path() -> Path:
    """Absolute path to the RS URDF.

    Relative ``urdf_path`` values resolve against the vendored submodule root,
    since that is where the meshes live.
    """
    raw = hardware_config().get("urdf_path", "")
    if not raw:
        raise ValueError(f"'urdf_path' is empty in {HARDWARE_YAML}")

    path = Path(raw)
    if not path.is_absolute():
        path = VENDOR_ROOT / path

    if not path.exists():
        raise FileNotFoundError(
            f"URDF not found: {path}\n"
            "The arm layer is a git submodule — run `git submodule update --init`."
        )
    return path.resolve()


def end_effector_frame() -> str:
    """Name of the end-effector frame in the RS URDF (``gripper_end``)."""
    frame = hardware_config().get("end_effector_frame", "")
    if not frame:
        raise ValueError(f"'end_effector_frame' is empty in {HARDWARE_YAML}")
    return frame


def joint_names() -> list[str]:
    """Joint names in hardware order: ``joint1``..``joint6`` then ``gripper``."""
    return [j["name"] for j in hardware_config().get("joints", [])]


def assert_rs_model() -> None:
    """Fail loudly if the resolved assets are not the RS arm's.

    Call this at startup. Without it, a DM-arm mix-up shows up as subtly wrong
    torques rather than as an error.
    """
    path = urdf_path()
    if _RS_URDF_MARKER not in str(path):
        raise RuntimeError(
            f"Resolved URDF does not look like the reBot-RS arm: {path}\n"
            f"Expected a path containing '{_RS_URDF_MARKER}'. A DM-arm URDF "
            "loads without error but produces wrong kinematics and torques."
        )

    expected_frame = "gripper_end"
    frame = end_effector_frame()
    if frame != expected_frame:
        raise RuntimeError(
            f"End-effector frame is {frame!r}, expected {expected_frame!r}. "
            "'end_link' means the DM arm's config leaked in."
        )
