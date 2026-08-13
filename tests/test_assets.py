"""Asset resolution guards.

These exist because the failure mode they cover is silent: the upstream
library's default URDF lookup yields the B601-DM arm, which loads fine and then
produces wrong kinematics forever.
"""

import pytest
import yaml

from backend import assets


def test_urdf_resolves_to_an_existing_rs_file():
    path = assets.urdf_path()
    assert path.is_absolute()
    assert path.exists(), "submodule missing — run `git submodule update --init`"
    assert "00-arm-rs_asm-v3" in str(path)


def test_end_effector_frame_is_the_rs_gripper():
    assert assets.end_effector_frame() == "gripper_end"


def test_joint_names_are_in_hardware_order():
    assert assets.joint_names() == [
        "joint1",
        "joint2",
        "joint3",
        "joint4",
        "joint5",
        "joint6",
    ]


def test_assert_rs_model_passes_on_the_shipped_config():
    assets.assert_rs_model()


def test_assert_rs_model_rejects_the_dm_arm(monkeypatch):
    """A DM config must fail loudly rather than load a valid-but-wrong model."""
    dm_config = {
        "urdf_path": "urdf/reBot-DevArm_fixend_description/urdf/reBot-DevArm_fixend.urdf",
        "end_effector_frame": "end_link",
        "joints": [],
    }
    monkeypatch.setattr(assets, "hardware_config", lambda: dm_config)

    with pytest.raises(RuntimeError, match="reBot-RS"):
        assets.assert_rs_model()


def test_has_gripper_defaults_to_true_without_the_switch(monkeypatch):
    """A config written before the switch existed behaves as it always did."""
    monkeypatch.setattr(assets, "hardware_config", lambda: {"joints": []})
    assert assets.has_gripper() is True


def test_joint_names_keep_the_gripper_when_enabled(monkeypatch):
    cfg = {**assets.hardware_config(), "gripper": True}
    monkeypatch.setattr(assets, "hardware_config", lambda: cfg)
    assert assets.joint_names()[-1] == "gripper"


def test_effective_hardware_yaml_strips_the_gripper_when_disabled():
    """The shipped config has the switch off: upstream must see six joints and
    no gripper group, or connect() would register a motor that is not on the bus."""
    path = assets.effective_hardware_yaml()
    assert path != assets.HARDWARE_YAML

    stripped = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "gripper" not in stripped["groups"]
    assert [j["name"] for j in stripped["joints"]] == [
        "joint1",
        "joint2",
        "joint3",
        "joint4",
        "joint5",
        "joint6",
    ]


def test_effective_hardware_yaml_is_the_config_itself_when_enabled(monkeypatch):
    cfg = {**assets.hardware_config(), "gripper": True}
    monkeypatch.setattr(assets, "hardware_config", lambda: cfg)
    assert assets.effective_hardware_yaml() == assets.HARDWARE_YAML


def test_effective_urdf_zeroes_gripper_link_masses_when_disabled():
    """The shipped config has no gripper attached: its links must leave the
    dynamics model, or the feedforward compensates for 0.8 kg that is not
    there and the floating arm pushes itself upward."""
    import xml.etree.ElementTree as ET

    path = assets.effective_urdf_path()
    assert path != assets.urdf_path()

    masses = {
        link.get("name"): float(link.find("inertial/mass").get("value"))
        for link in ET.parse(path).getroot().iter("link")
        if link.find("inertial/mass") is not None
    }
    for name in ("gripper_end", "gripper_left", "gripper_right"):
        assert masses[name] == 0.0
    # arm links keep their calibrated masses
    assert masses["link2"] == pytest.approx(1.552)


def test_effective_urdf_is_the_vendored_file_when_gripper_enabled(monkeypatch):
    cfg = {**assets.hardware_config(), "gripper": True}
    monkeypatch.setattr(assets, "hardware_config", lambda: cfg)
    assert assets.effective_urdf_path() == assets.urdf_path()


def test_the_stripped_model_carries_no_gripper_gravity():
    """The real behaviour this guards: at q=0 the vendored model demands
    +6.76 N·m on joint3, of which ~3.3 N·m is the detached gripper's phantom
    torque. The feedforward must not command it."""
    import numpy as np
    import pinocchio as pin

    full = pin.buildModelFromUrdf(str(assets.urdf_path()))
    stripped = pin.buildModelFromUrdf(str(assets.effective_urdf_path()))

    assert pin.computeTotalMass(stripped) == pytest.approx(
        pin.computeTotalMass(full) - 0.8004, abs=1e-3
    )

    q = np.zeros(stripped.nq)
    g_full = pin.computeGeneralizedGravity(full, full.createData(), q)
    g_stripped = pin.computeGeneralizedGravity(stripped, stripped.createData(), q)
    assert g_stripped[2] < g_full[2] - 3.0  # joint3: phantom lift gone
    assert g_stripped[0] == pytest.approx(g_full[0])  # joint1 carries no gravity either way
