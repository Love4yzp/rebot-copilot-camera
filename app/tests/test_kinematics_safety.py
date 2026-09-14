"""Joint limits and self-collision, both read from the URDF.

The poses used here are not invented. They were found by sampling the URDF's
own limit box and keeping configurations Pinocchio reports as colliding, so
each one is a real geometry result rather than a guess that happens to pass.
"""

import pytest

from backend.safety.kinematics import (
    LIMIT_TOLERANCE_RAD,
    ArmModel,
    arm_model,
    validate_pose,
    validate_sequence,
)

REST = dict.fromkeys(
    ("joint1", "joint2", "joint3", "joint4", "joint5", "joint6"), 0.0
)

#: link3 swung back into the base — the arm folded onto itself.
SELF_COLLIDING = {
    "joint1": 2.394,
    "joint2": 3.039,
    "joint3": 0.046,
    "joint4": 1.142,
    "joint5": 1.511,
    "joint6": 2.871,
}

#: Both legal on their own; the straight line between them passes through the base.
PATH_A = {
    "joint1": -0.882,
    "joint2": 3.107,
    "joint3": 0.686,
    "joint4": -0.132,
    "joint5": 1.482,
    "joint6": -3.098,
}
PATH_B = {
    "joint1": -1.148,
    "joint2": 2.579,
    "joint3": 0.301,
    "joint4": 1.345,
    "joint5": 1.051,
    "joint6": -2.242,
}


@pytest.fixture(scope="module")
def model() -> ArmModel:
    return arm_model()


# ── limits ───────────────────────────────────────────────────────────────────


def test_limits_come_from_the_urdf_not_a_hand_copied_table(model: ArmModel):
    limits = model.limits()
    assert limits["joint1"] == pytest.approx((-2.8, 2.8))
    assert limits["joint4"] == pytest.approx((-1.57, 1.57))


def test_the_gripper_is_not_limit_checked(model: ArmModel):
    """One motor drives two prismatic fingers whose limits are in metres.

    There is no calibrated angle-to-travel mapping, so checking it would mean
    inventing a conversion and then trusting it.
    """
    assert "gripper" not in model.limits()
    assert validate_pose({"gripper": 999.0}, model) == []


def test_rest_pose_is_legal(model: ArmModel):
    assert validate_pose(REST, model) == []


def test_out_of_range_is_rejected_and_names_the_joint(model: ArmModel):
    problems = validate_pose({**REST, "joint1": 5.0}, model)
    assert len(problems) == 1
    assert "joint1" in problems[0]
    assert "5.0" in problems[0]


def test_boundary_values_are_accepted(model: ArmModel):
    lower, upper = model.limits()["joint1"]
    assert validate_pose({**REST, "joint1": lower}, model) == []
    assert validate_pose({**REST, "joint1": upper}, model) == []


def test_the_rest_pose_survives_encoder_noise_on_joint2(model: ArmModel):
    """joint2 and joint3 have a lower limit of exactly 0.0 and the arm rests at
    0, so the rest pose sits on the boundary. Without tolerance the arm would
    be rejected for standing still."""
    assert model.limits()["joint2"][0] == 0.0

    assert validate_pose({**REST, "joint2": -LIMIT_TOLERANCE_RAD / 2}, model) == []
    assert validate_pose({**REST, "joint2": -0.5}, model) != []


def test_unknown_joint_names_are_ignored_rather_than_rejected(model: ArmModel):
    """Limit checking is not the place to police names — the URDF is authority
    on ranges, not on what the hardware happens to call things."""
    assert validate_pose({"elbow": 1.0}, model) == []


# ── self-collision ───────────────────────────────────────────────────────────


def test_structural_pairs_are_excluded(model: ArmModel):
    """Adjacent links are bolted together and always touching. Without dropping
    them every pose would be a collision."""
    assert len(model.geom.collisionPairs) == 36
    assert model.check_self_collision(REST) == []


def test_a_folded_arm_is_detected(model: ArmModel):
    hits = model.check_self_collision(SELF_COLLIDING)
    assert hits, "this configuration folds link3 into the base"
    assert "base_link" in str(hits[0])


def test_validate_pose_reports_collisions_as_well_as_limits(model: ArmModel):
    problems = validate_pose(SELF_COLLIDING, model)
    assert any("collides" in p for p in problems)


# ── paths ────────────────────────────────────────────────────────────────────


def test_two_legal_poses_can_have_an_illegal_path(model: ArmModel):
    """The failure this whole check exists for. Both endpoints pass; the
    straight line between them goes through the arm's own base, and the
    operator would find out by hearing it."""
    assert validate_pose(PATH_A, model) == []
    assert validate_pose(PATH_B, model) == []

    assert model.check_path(PATH_A, PATH_B), "midpoint collision not detected"


def test_a_safe_path_reports_nothing(model: ArmModel):
    assert model.check_path(REST, {**REST, "joint1": 0.5}) == []


def test_validate_sequence_labels_which_waypoint_or_leg_failed(model: ArmModel):
    problems = validate_sequence([REST, SELF_COLLIDING], model)
    assert any(p.startswith("waypoint 1") for p in problems)

    problems = validate_sequence([PATH_A, PATH_B], model)
    assert any(p.startswith("path 0->1") for p in problems)


def test_validate_sequence_passes_a_clean_routine(model: ArmModel):
    poses = [REST, {**REST, "joint1": 0.4}, {**REST, "joint1": 0.8}]
    assert validate_sequence(poses, model) == []


def test_empty_sequence_is_fine(model: ArmModel):
    assert validate_sequence([], model) == []


# ── forward kinematics ───────────────────────────────────────────────────────


def test_forward_kinematics_returns_the_camera_pose(model: ArmModel):
    pose = model.forward_kinematics(REST)
    assert pose.translation.shape == (3,)
    # Rest pose reaches forward along +x and stands above the base.
    assert pose.translation[0] > 0.2
    assert pose.translation[2] > 0.1


def test_rotating_the_base_swings_the_camera_sideways(model: ArmModel):
    straight = model.forward_kinematics(REST).translation
    turned = model.forward_kinematics({**REST, "joint1": 1.0}).translation

    assert abs(turned[1]) > abs(straight[1])
    assert turned[2] == pytest.approx(straight[2], abs=1e-6)


def test_earlier_fk_results_are_not_overwritten_by_later_calls(model: ArmModel):
    """``data.oMf`` is live storage that the next call overwrites.

    Returning it directly would make a previously computed pose silently change
    under the caller — which is how this was found.
    """
    first = model.forward_kinematics(REST)
    before = first.translation.copy()

    model.forward_kinematics({**REST, "joint1": 1.0})

    assert first.translation == pytest.approx(before)
