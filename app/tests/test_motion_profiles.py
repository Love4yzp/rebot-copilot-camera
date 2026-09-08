"""Persistent regression coverage for analytical motion preparation."""

import pytest

from backend.arm.profile import MotionRejected, MotionLimits, Quintic, prepare_profiles
from backend.arm import SimArm


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


def test_nonzero_boundary_velocity_and_acceleration_end_at_rest():
    profile = Quintic.create(0.2, 0.4, -0.3, 0.8, 2.0)
    assert profile.eval(0.0)[:3] == pytest.approx((0.2, 0.4, -0.3))
    assert profile.eval(2.0)[:3] == pytest.approx((0.8, 0.0, 0.0), abs=1e-9)


def test_position_extrema_rejects_curved_overshoot():
    with pytest.raises(MotionRejected):
        prepare_profiles(
            {"joint1": (0.8, 5.0, 0.0)},
            {"joint1": 0.9},
            0.5,
            1,
            MotionLimits(100.0, 100.0, 1000.0),
            {"joint1": (0.0, 1.0)},
        )


def test_sim_target_outside_urdf_bounds_is_rejected_without_changing_float():
    clock = Clock()
    arm = SimArm(("joint1", "joint2"), clock=clock)
    arm.connect()
    arm.set_float(True)
    with pytest.raises(MotionRejected):
        arm.move_to({"joint1": 100.0}, 1.0)
    assert arm.is_floating is True


def test_urdf_lower_bound_noise_is_tolerated_but_excess_is_rejected():
    clock = Clock()
    arm = SimArm(("joint2",), clock=clock)
    arm.connect()
    arm._q["joint2"] = -2e-6
    arm._q_target["joint2"] = -2e-6
    assert arm.prepare_move({"joint2": 0.8}, 1.0).target["joint2"] == pytest.approx(0.8)
    with pytest.raises(MotionRejected):
        arm.prepare_move({"joint2": -0.020001}, 1.0)


def test_first_approach_speed_limit_is_reflected_in_analytic_profile():
    from backend.actions import InlineRunner
    from backend.core import SequenceExecutor
    from backend.sequences import Pose, Sequence, TransitionBlock

    clock = Clock()
    arm = SimArm(("joint1",), clock=clock)
    arm.connect()
    target = Pose(name="one", joints={"joint1": 1.0})
    executor = SequenceExecutor(
        Sequence(name="goto", blocks=[TransitionBlock(duration_s=1.0)]),
        {target.id: target}, arm=arm, actions=InlineRunner([]), clock=clock,
        goto=target, first_approach_max_speed=0.05,
    )
    executor.start()
    assert arm._motion is not None
    assert arm._motion.profiles["joint1"].extrema()[0] <= 0.05 + 1e-8
