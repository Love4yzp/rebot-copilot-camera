"""Operator-calibrated tuning: payload profile and control parameters.

Two kinds of value live here, because two kinds of risk live here:

- **decision-class** (floatlock thresholds, settle dwell, approach speed):
  they steer *when* logic fires and never touch motor torque.
- **torque-class** (float gains, payload profile): they change what the
  motors push with. Float gains are safe to change mid-float (the follow
  target *is* the current position, so the position error — and the torque
  jump — is zero). The payload profile is not: it moves the gravity
  feedforward by newton-metres at once, so switching it is gated on the arm
  not floating (see ``Controller.apply_tuning``).

Why a separate file from ``config/rebotarm_rs.yaml``: that file is a heavily
commented fork of the upstream hardware config, and a YAML round-trip drops
every comment. The hardware file describes the *bus*; this file describes
what the operator calibrated. The panel writes here only on an explicit
save — hot-applied values live in the Controller and die with the process.

Defaults below mirror the code constants they replace
(``backend/arm/session.py`` FLOAT_KP/KD, ``backend/core/floatlock.py``,
``backend/core/executor.py`` SETTLE_* / FIRST_APPROACH_MAX_SPEED).
``tests/test_tuning.py`` asserts that mirror, so a default cannot drift away
from the code it feeds.
"""

from __future__ import annotations

import logging
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import assets
from .safety.kinematics import ARM_JOINTS

log = logging.getLogger(__name__)

#: Section names as they appear on the wire (the PUT patch, the dirty list).
SECTIONS = ("payload", "float", "floatlock", "settle", "approach", "gravity")


class TuningRejected(RuntimeError):
    """A tuning change the current arm state forbids. The API maps it to 409."""


class PayloadProfile(str, Enum):
    """What *mass* hangs off the end flange — orthogonal to the hardware yaml's
    ``gripper`` switch, which answers a different question: whether the
    gripper *motor* is on the bus.

    ``gripper`` means the gripper assembly's mass hangs off the end, motor
    wired or not. With the motor off the bus it is dead weight: the dynamics
    model carries its mass (it is physically there) while the actuator is
    absent. With the motor on, this is the only legal profile — a wired motor
    cannot be hot-added.

    ``bare`` and ``camera`` are motor-less either way; they differ only in
    what the gravity model carries."""

    BARE = "bare"
    CAMERA = "camera"
    GRIPPER = "gripper"


class CameraPayload(BaseModel):
    """The camera + mount as an equivalent point mass on the end link.

    ``com`` is expressed in the ``gripper_end`` link frame — the camera
    mounts where the gripper did. Only mass and centre of mass feed the
    gravity model; the inertia tensor is left as the link's own (gravity
    feedforward does not read it), so this is honest about being a
    gravity-only calibration."""

    mass: float | None = Field(default=None, gt=0, le=5.0)
    com: tuple[float, float, float] = (0.0, 0.0, 0.0)


class PayloadTuning(BaseModel):
    profile: PayloadProfile = PayloadProfile.BARE
    camera: CameraPayload = Field(default_factory=CameraPayload)

    @model_validator(mode="after")
    def camera_profile_requires_a_weighed_mass(self) -> PayloadTuning:
        if self.profile is PayloadProfile.CAMERA and self.camera.mass is None:
            raise ValueError(
                "profile 'camera' requires camera.mass — weigh the body and "
                "mount first; a guessed mass is a phantom torque"
            )
        return self


class FloatTuning(BaseModel):
    """MIT gains while floating (kp near zero = your hand moves the arm)."""

    kp: float = Field(2.0, ge=0.0, le=10.0)
    kd: float = Field(1.0, ge=0.0, le=10.0)


class FloatLockTuning(BaseModel):
    linear_threshold: float = Field(0.04, gt=0, le=0.5)
    angular_threshold: float = Field(0.08, gt=0, le=1.0)
    release_factor: float = Field(1.0, gt=0, le=4.0)
    lock_factor: float = Field(0.6, gt=0, le=1.0)
    min_still_s: float = Field(0.25, gt=0, le=2.0)

    @model_validator(mode="after")
    def hysteresis_band_stays_positive(self) -> FloatLockTuning:
        if self.lock_factor > self.release_factor:
            raise ValueError(
                "lock_factor must not exceed release_factor — that inverts "
                "the hysteresis band and the arm chatters between free and locked"
            )
        return self


class SettleTuning(BaseModel):
    """"Arrived" = inside the eps window *and* drifting less than this."""

    drift_rad: float = Field(0.003, gt=0, le=0.05)
    min_s: float = Field(0.15, gt=0, le=2.0)


class ApproachTuning(BaseModel):
    """Ceiling on joint speed for the first approach (and goto), rad/s."""

    first_max_speed: float = Field(0.25, gt=0, le=1.0)


class GravityTuning(BaseModel):
    """Per-joint correction of the gravity feedforward, mirroring upstream's
    ``auto_float_test`` ``--k/--c`` knobs:

        tau_sent = scale[joint] * g_model(q)[joint] + bias[joint]

    Missing joints are identity (1.0 / 0.0). This is the operator's lever for
    the vendor model's residual error — the j2 over-compensation that floats
    the arm up at extended poses is corrected by a scale below 1 on joint2.
    Only the six arm joints are legal keys; the gripper has no calibrated
    mapping and stays at zero feedforward.
    """

    scale: dict[str, float] = Field(default_factory=dict)
    bias: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def keys_are_arm_joints_and_ranges_sane(self) -> GravityTuning:
        for name in set(self.scale) | set(self.bias):
            if name not in ARM_JOINTS:
                raise ValueError(f"gravity correction key {name!r} is not an arm joint")
        for name, value in self.scale.items():
            if not 0.2 <= value <= 2.0:
                raise ValueError(f"gravity scale for {name} out of range: {value}")
        for name, value in self.bias.items():
            if not -5.0 <= value <= 5.0:
                raise ValueError(f"gravity bias for {name} out of range: {value}")
        return self


class TuningConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    payload: PayloadTuning = Field(default_factory=PayloadTuning)
    float_: FloatTuning = Field(default_factory=FloatTuning, alias="float")
    floatlock: FloatLockTuning = Field(default_factory=FloatLockTuning)
    settle: SettleTuning = Field(default_factory=SettleTuning)
    approach: ApproachTuning = Field(default_factory=ApproachTuning)
    gravity: GravityTuning = Field(default_factory=GravityTuning)

    def dump(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)

    def dirty_sections(self, saved: TuningConfig) -> list[str]:
        mine, theirs = self.dump(), saved.dump()
        return [name for name in SECTIONS if mine[name] != theirs[name]]


def merge_patch(current: TuningConfig, patch: dict[str, Any]) -> TuningConfig:
    """Apply a partial PUT body to the current config and revalidate the whole.

    Validating the *merged* config (not the patch alone) is what lets a
    camera mass and the camera profile arrive in either order across two
    requests — each intermediate state stays legal, the combination is what
    gets checked."""
    merged = current.dump()
    for key, value in patch.items():
        if key not in SECTIONS:
            raise TuningRejected(f"unknown tuning section: {key!r}")
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return TuningConfig.model_validate(merged)


class TuningStore:
    """The on-disk copy. One document, atomic write — same discipline as the
    sequence stores. A missing file is defaults, not an error: a fresh
    checkout behaves exactly like the code constants it mirrors."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> TuningConfig:
        if not self._path.exists():
            config = TuningConfig()
        else:
            data = yaml.safe_load(self._path.read_text(encoding="utf-8")) or {}
            config = TuningConfig.model_validate(data)
        # Hardware truth beats a stale file: with the gripper motor on the bus
        # the profile must be "gripper" — the mass hangs off the arm whether
        # the file says so or not. A rig that was saved as bare/camera and
        # then got the motor wired must not come up inconsistent.
        if assets.has_gripper() and config.payload.profile is not PayloadProfile.GRIPPER:
            log.warning(
                "gripper motor is on the bus — coercing payload profile %s -> gripper",
                config.payload.profile.value,
            )
            config.payload.profile = PayloadProfile.GRIPPER
        return config

    def save(self, config: TuningConfig) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(
            "# 调参面板保存的值；热改只进内存，显式保存才落到这里。\n"
            "# Saved by the tuning panel. Hot-applied values live in memory\n"
            "# until an explicit save writes them here.\n"
            + yaml.safe_dump(config.dump(), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        tmp.replace(self._path)
