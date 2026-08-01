"""SimArm behaviour.

Everything testable without hardware stands on this, so its own behaviour has
to be pinned down first.
"""

import pytest

from backend.arm import ArmDriver, SimArm

JOINTS = ("joint1", "joint2", "joint3", "joint4", "joint5", "joint6", "gripper")


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def arm(clock: FakeClock) -> SimArm:
    a = SimArm(JOINTS, clock=clock)
    a.connect()
    return a


def run(arm: SimArm, seconds: float, dt: float = 0.01) -> None:
    for _ in range(int(round(seconds / dt))):
        arm.step(dt)


def test_satisfies_the_arm_driver_protocol(arm: SimArm):
    assert isinstance(arm, ArmDriver)


def test_starts_at_zero_unless_told_otherwise(arm: SimArm, clock: FakeClock):
    assert arm.read_state().positions == dict.fromkeys(JOINTS, 0.0)


def test_initial_positions_are_honoured(clock: FakeClock):
    arm = SimArm(JOINTS, clock=clock, initial={"joint2": 1.5})
    assert arm.read_state().positions["joint2"] == 1.5


def test_rejects_unknown_joints(arm: SimArm):
    with pytest.raises(KeyError):
        arm.hold({"elbow": 1.0})


def test_converges_to_the_commanded_target(arm: SimArm):
    arm.hold({"joint1": 1.0, "joint4": -0.5})
    run(arm, seconds=2.0)

    pos = arm.read_state().positions
    assert pos["joint1"] == pytest.approx(1.0, abs=1e-3)
    assert pos["joint4"] == pytest.approx(-0.5, abs=1e-3)


def test_convergence_does_not_depend_on_step_size(clock: FakeClock):
    """The lag is integrated exactly, so coarse and fine ticks must agree.

    Otherwise executor timing tests would quietly depend on the tick rate the
    test happened to pick.
    """
    coarse = SimArm(JOINTS, clock=FakeClock())
    fine = SimArm(JOINTS, clock=FakeClock())
    for a in (coarse, fine):
        a.connect()
        a.hold({"joint1": 1.0})

    run(coarse, seconds=1.0, dt=0.1)
    run(fine, seconds=1.0, dt=0.001)

    assert coarse.read_state().positions["joint1"] == pytest.approx(
        fine.read_state().positions["joint1"], abs=1e-6
    )


def test_velocity_is_finite_differenced_not_zero(arm: SimArm):
    """Velocity must come from position differences — the real motor's velocity
    register is not rad/s on this firmware, and float/lock rides on velocity."""
    arm.hold({"joint1": 1.0})
    arm.step(0.01)

    v = arm.read_state().velocities["joint1"]
    assert v > 0
    assert v == pytest.approx((arm.read_state().positions["joint1"] - 0.0) / 0.01)


def test_velocity_decays_as_it_settles(arm: SimArm):
    """Velocity must fall away as the arm arrives.

    Asserted as a ratio rather than against a fixed epsilon: the absolute
    residual is just exp(-t/tau)/tau, so a hard-coded bound would silently
    become a tau-dependent trap for whoever retunes the simulator.
    """
    arm.hold({"joint1": 1.0})
    arm.step(0.01)
    early = arm.read_state().velocities["joint1"]

    run(arm, seconds=1.0)
    late = arm.read_state().velocities["joint1"]

    assert 0 < late < early / 100


def test_a_held_arm_does_not_move_on_its_own(arm: SimArm):
    run(arm, seconds=1.0)
    assert arm.read_state().positions == dict.fromkeys(JOINTS, 0.0)


# ── float / drag ─────────────────────────────────────────────────────────────


def test_floating_arm_does_not_chase_its_target(arm: SimArm):
    arm.hold({"joint1": 1.0})
    arm.set_float(True)
    run(arm, seconds=2.0)

    assert arm.read_state().positions["joint1"] == pytest.approx(0.0, abs=1e-9)


def test_drag_moves_a_floating_arm(arm: SimArm):
    arm.set_float(True)
    arm.drag({"joint1": 0.3})
    arm.drag({"joint1": 0.2})

    assert arm.read_state().positions["joint1"] == pytest.approx(0.5)


def test_a_held_arm_can_be_pushed_but_returns(arm: SimArm):
    """Real hardware behaves this way, and it matters.

    "Held" is an MIT hold at finite stiffness, so an operator can push through
    it — which is exactly how drag teaching *starts*: the arm is locked until
    something moves it, and the thing that moves it is a hand on a held arm. A
    simulator that refused to be pushed would make the teach loop unreachable.

    The difference from floating is what happens next: this one springs back.
    """
    arm.drag({"joint1": 0.3})
    assert arm.read_state().positions["joint1"] == pytest.approx(0.3), "pushed"

    run(arm, seconds=2.0)
    assert arm.read_state().positions["joint1"] == pytest.approx(0.0, abs=1e-3), "returned"


def test_releasing_float_holds_at_the_dragged_position(arm: SimArm):
    """"Let go and it stays put" — the core of drag teaching.

    If the target were stale, the arm would snap back the instant the operator
    released it.
    """
    arm.hold({"joint1": 1.0})
    arm.set_float(True)
    arm.drag({"joint1": -0.4})
    arm.set_float(False)

    run(arm, seconds=2.0)
    assert arm.read_state().positions["joint1"] == pytest.approx(-0.4, abs=1e-6)


def test_dragging_to_three_poses_records_three_distinct_positions(arm: SimArm):
    """The teach loop: drag, release, capture — three times."""
    captured = []
    for delta in (0.3, 0.5, -0.2):
        arm.set_float(True)
        arm.drag({"joint1": delta})
        arm.set_float(False)
        run(arm, seconds=0.5)
        captured.append(round(arm.read_state().positions["joint1"], 6))

    assert captured == [0.3, 0.8, 0.6]


def test_connect_and_disconnect_track_state(clock: FakeClock):
    arm = SimArm(JOINTS, clock=clock)
    assert arm.is_connected is False
    arm.connect()
    assert arm.is_connected is True
    arm.disconnect()
    assert arm.is_connected is False


def test_read_state_does_not_alias_internal_state(arm: SimArm):
    state = arm.read_state()
    state.positions["joint1"] = 99.0
    assert arm.read_state().positions["joint1"] == 0.0


def test_negative_dt_is_rejected(arm: SimArm):
    with pytest.raises(ValueError):
        arm.step(-0.01)


def test_zero_tau_is_rejected(clock: FakeClock):
    with pytest.raises(ValueError):
        SimArm(JOINTS, clock=clock, tau=0.0)
