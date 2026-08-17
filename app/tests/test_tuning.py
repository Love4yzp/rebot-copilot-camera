"""Tuning model, merge and store behaviour.

The defaults mirror is the load-bearing one: TuningConfig() must equal the
code constants it replaces, or a fresh checkout (no tuning.yaml) silently
behaves differently from the code the constants came from.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.arm import session as arm_session
from backend.core import executor
from backend.core.floatlock import (
    DEFAULT_ANGULAR_THRESHOLD,
    DEFAULT_LINEAR_THRESHOLD,
    DEFAULT_MIN_STILL_S,
    LOCK_FACTOR,
    RELEASE_FACTOR,
)
from backend.tuning import (
    TuningConfig,
    TuningRejected,
    TuningStore,
    merge_patch,
)


def test_gravity_correction_defaults_and_validation():
    """The correction is identity by default, and only the six arm joints
    with sane ranges are legal keys — a typo'd key or a wild value must fail
    at the wire, not show up as a phantom torque on the arm."""
    t = TuningConfig()
    assert t.gravity.scale == {}
    assert t.gravity.bias == {}

    ok = TuningConfig.model_validate(
        {"gravity": {"scale": {"joint2": 0.6}, "bias": {"joint2": -0.5}}}
    )
    assert ok.gravity.scale["joint2"] == 0.6
    assert ok.gravity.bias["joint2"] == -0.5

    with pytest.raises(ValidationError):
        TuningConfig.model_validate({"gravity": {"scale": {"gripper": 0.5}}})
    with pytest.raises(ValidationError):
        TuningConfig.model_validate({"gravity": {"scale": {"joint2": 0.1}}})
    with pytest.raises(ValidationError):
        TuningConfig.model_validate({"gravity": {"bias": {"joint3": 6.0}}})


def test_defaults_mirror_the_code_constants():
    t = TuningConfig()
    assert t.float_.kp == arm_session.FLOAT_KP
    assert t.float_.kd == arm_session.FLOAT_KD
    assert t.floatlock.linear_threshold == DEFAULT_LINEAR_THRESHOLD
    assert t.floatlock.angular_threshold == DEFAULT_ANGULAR_THRESHOLD
    assert t.floatlock.release_factor == RELEASE_FACTOR
    assert t.floatlock.lock_factor == LOCK_FACTOR
    assert t.floatlock.min_still_s == DEFAULT_MIN_STILL_S
    assert t.settle.drift_rad == executor.SETTLE_DRIFT_RAD
    assert t.settle.min_s == executor.SETTLE_MIN_S
    assert t.approach.first_max_speed == executor.FIRST_APPROACH_MAX_SPEED
    assert t.payload.profile.value == "bare"


def test_wire_format_uses_the_float_alias():
    dumped = TuningConfig().dump()
    assert "float" in dumped
    assert "float_" not in dumped


def test_load_coerces_stale_profile_when_the_motor_is_wired(monkeypatch, tmp_path):
    """A rig saved as bare/camera and later wired must not come up
    inconsistent: the motor on the bus means the mass is on the arm."""
    import yaml

    from backend import assets
    from backend.tuning import PayloadProfile

    path = tmp_path / "tuning.yaml"
    path.write_text(
        yaml.safe_dump(
            {"payload": {"profile": "camera", "camera": {"mass": 0.74}}},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(assets, "has_gripper", lambda: True)
    config = TuningStore(path).load()
    assert config.payload.profile is PayloadProfile.GRIPPER
    assert config.payload.camera.mass == 0.74  # kept, just not selected


def test_camera_profile_requires_a_weighed_mass():
    with pytest.raises(ValidationError, match="camera.mass"):
        merge_patch(TuningConfig(), {"payload": {"profile": "camera"}})


def test_camera_profile_accepts_mass_and_com_in_either_order():
    with_mass = merge_patch(TuningConfig(), {"payload": {"camera": {"mass": 0.74}}})
    cfg = merge_patch(with_mass, {"payload": {"profile": "camera"}})
    assert cfg.payload.profile.value == "camera"
    assert cfg.payload.camera.mass == 0.74


def test_out_of_range_values_are_refused():
    with pytest.raises(ValidationError):
        merge_patch(TuningConfig(), {"float": {"kp": 99.0}})
    with pytest.raises(ValidationError):
        merge_patch(TuningConfig(), {"approach": {"first_max_speed": 0.0}})


def test_hysteresis_band_cannot_invert():
    with pytest.raises(ValidationError, match="hysteresis"):
        merge_patch(
            TuningConfig(),
            {"floatlock": {"release_factor": 0.5, "lock_factor": 0.9}},
        )


def test_unknown_section_is_rejected():
    with pytest.raises(TuningRejected, match="unknown tuning section"):
        merge_patch(TuningConfig(), {"turbo": {"enabled": True}})


def test_missing_file_is_defaults(monkeypatch, tmp_path: Path):
    """On a gripper-less rig the missing file is exactly the code defaults."""
    from backend import assets

    monkeypatch.setattr(assets, "has_gripper", lambda: False)
    assert TuningStore(tmp_path / "tuning.yaml").load() == TuningConfig()


def test_store_roundtrip(monkeypatch, tmp_path: Path):
    from backend import assets

    monkeypatch.setattr(assets, "has_gripper", lambda: False)
    store = TuningStore(tmp_path / "tuning.yaml")
    cfg = merge_patch(TuningConfig(), {"float": {"kp": 3.5}})
    store.save(cfg)
    assert store.load() == cfg


def test_dirty_sections_names_only_what_changed():
    saved = TuningConfig()
    live = merge_patch(saved, {"float": {"kp": 3.0}, "settle": {"min_s": 0.3}})
    assert live.dirty_sections(saved) == ["float", "settle"]
    assert saved.dirty_sections(saved) == []
