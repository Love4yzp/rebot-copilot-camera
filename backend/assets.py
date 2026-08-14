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
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from .tuning import PayloadTuning

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Upstream arm library, pinned as a git submodule. The URDF and its 30 STL
#: meshes (63 MB) are read from here rather than copied into this repo, so a
#: submodule bump can never leave a stale duplicate behind.
VENDOR_ROOT = REPO_ROOT / "vendor" / "reBotArm_control_py"

#: Our fork of the RS hardware config — retuned for the camera payload.
HARDWARE_YAML = REPO_ROOT / "config" / "rebotarm_rs.yaml"

#: Built frontend, produced by `npm run build` in frontend/.
STATIC_DIR = REPO_ROOT / "backend" / "static"

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
    """Joint names in hardware order, as listed in the hardware yaml.

    Gripper joints drop out when the ``gripper`` switch is off — see
    :func:`has_gripper`.
    """
    names = [j["name"] for j in hardware_config().get("joints", [])]
    if has_gripper():
        return names
    gripper = set(_gripper_joint_names())
    return [n for n in names if n not in gripper]


def has_gripper() -> bool:
    """Whether a motor gripper is attached (the ``gripper`` switch in the yaml).

    The gripper is a swappable accessory, so this is a flag rather than an edit
    to the group/joint definitions. Missing key defaults to ``True`` — a config
    without the switch behaves exactly as before the switch existed.
    """
    return bool(hardware_config().get("gripper", True))


def _gripper_joint_names() -> list[str]:
    """Joint names belonging to the gripper group (usually just ``gripper``)."""
    groups = hardware_config().get("groups", {})
    return list(groups.get("gripper", {}).get("joints", []))


def effective_hardware_yaml() -> Path:
    """The yaml to hand upstream's ``RebotArm``.

    With the gripper switched on this is simply :data:`HARDWARE_YAML`. With it
    off, the gripper group and its joint entries are stripped into a throwaway
    copy: otherwise upstream would register a motor that is not on the bus and
    ``connect()`` would fail. Upstream substitutes a ``NoOpGroup`` for the
    missing gripper group, so every gripper call above stays a harmless no-op.
    Only ``name``/``channel``/``rate``/``groups``/``joints`` are read from the
    file, so the copy's location does not matter.
    """
    if has_gripper():
        return HARDWARE_YAML

    import tempfile

    cfg = hardware_config()
    gripper = set(_gripper_joint_names())
    stripped = {
        **cfg,
        "groups": {k: v for k, v in cfg.get("groups", {}).items() if k != "gripper"},
        "joints": [j for j in cfg.get("joints", []) if j["name"] not in gripper],
    }
    with tempfile.NamedTemporaryFile(
        "w", prefix="rebotarm_rs_nogrip_", suffix=".yaml", delete=False, encoding="utf-8"
    ) as f:
        yaml.safe_dump(stripped, f)
        return Path(f.name)


def effective_urdf_path(payload: PayloadTuning | None = None) -> Path:
    """The URDF to hand dynamics loaders (gravity feedforward).

    With the gripper attached this is simply :func:`urdf_path`. Without it,
    a throwaway copy whose end links match the payload profile:

    - ``bare`` (also the default when ``payload`` is ``None``): the gripper
      links' inertial masses are zeroed. The ``gripper`` switch takes the
      motor off the bus (see :func:`effective_hardware_yaml`), but the
      vendored URDF still carries ~0.8 kg of gripper links, and the gravity
      model keeps feeding forward torque for a payload that is not there.
      At q=0 that phantom torque is +3.3 N·m on joint3 against ~0.2–0.5 N·m
      of friction — with float gains near zero nothing resists it, and the
      "floating" arm pushes itself upward.
    - ``camera``: the finger links are zeroed and ``gripper_end`` becomes the
      camera — its mass and centre of mass come from the tuning file (the
      camera mounts where the gripper did). Only mass and com are replaced;
      the inertia tensor stays the link's own, because gravity feedforward
      does not read it and inventing a tensor would be a made-up number.
    - ``gripper``: the assembly's mass stays — a mounted-but-unwired gripper
      is dead weight the feedforward must carry. Same file as the motor-on
      case, because the mass facts are identical; only the bus differs.

    Geometry caveat: the copy edits only ``<inertial>`` values and lives in
    a temp dir, so its relative ``meshes/...`` paths no longer resolve. Use
    it for dynamics only (``pin.buildModelFromUrdf`` alone reads no meshes);
    kinematics/collision keep the vendored URDF, where the phantom gripper
    geometry errs on the conservative side.
    """
    if has_gripper():
        return urdf_path()

    from .tuning import PayloadProfile  # local: assets must not hard-depend on tuning

    profile = payload.profile if payload is not None else PayloadProfile.BARE
    if profile is PayloadProfile.GRIPPER:
        # Dead weight: the gripper assembly is bolted on but its motor is not
        # wired, so it has no group on the bus (effective_hardware_yaml strips
        # it) — yet its 0.8 kg hangs off the arm for real, and the gravity
        # model must carry it. The profile answers "what mass hangs off the
        # end"; the yaml switch answers "is the motor on the bus". Two
        # orthogonal facts, and this profile is the mass answer.
        return urdf_path()

    import tempfile
    import xml.etree.ElementTree as ET

    tree = ET.parse(urdf_path())
    for link in tree.getroot().iter("link"):
        name = link.get("name", "")
        if not name.startswith("gripper"):
            continue
        mass = link.find("inertial/mass")
        if mass is None:
            continue
        if profile is PayloadProfile.CAMERA and name == "gripper_end":
            camera = payload.camera
            mass.set("value", str(camera.mass))
            origin = link.find("inertial/origin")
            if origin is not None:
                origin.set("xyz", " ".join(str(c) for c in camera.com))
        else:
            mass.set("value", "0.0")

    with tempfile.NamedTemporaryFile(
        "w", prefix=f"rebotarm_rs_{profile.value}_", suffix=".urdf", delete=False, encoding="utf-8"
    ) as f:
        f.write(ET.tostring(tree.getroot(), encoding="unicode"))
        return Path(f.name)


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
