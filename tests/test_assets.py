"""Asset resolution guards.

These exist because the failure mode they cover is silent: the upstream
library's default URDF lookup yields the B601-DM arm, which loads fine and then
produces wrong kinematics forever.
"""

import pytest

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
        "gripper",
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
