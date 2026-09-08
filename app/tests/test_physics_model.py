"""Opt-in MuJoCo model checks; skipped when the physics extra is absent."""

import importlib.util

import numpy as np
import pytest

if importlib.util.find_spec("mujoco") is None:
    pytest.skip("install the optional physics extra", allow_module_level=True)
import mujoco
import pinocchio as pin

from backend import assets
from backend.validation.physics import JOINTS, MitCommand, PhysicsPlant, RecordingTransport, load_model
from backend.arm.session import ArmSession


def test_rs_model_keeps_tcp_body_and_joint_mapping():
    bundle = load_model()
    try:
        assert bundle.model.nq == bundle.model.nv == 6
        assert [bundle.model.joint(i).name for i in range(bundle.model.njnt)] == list(JOINTS)
        assert "base_link" in bundle.body_ids
        assert "gripper_end" in bundle.body_ids
    finally:
        bundle.close()


def test_session_accepts_injected_transport_without_connecting_hardware():
    def clock():
        return 0.0

    transport = RecordingTransport(clock=clock)
    session = ArmSession(clock=clock, transport=transport)
    session.hold({name: 0.0 for name in JOINTS})
    assert transport.commands
    assert "joint2" in transport.commands[-2].q_des


def test_command_does_not_teleport_feedback_and_plant_uses_one_ms_step():
    def clock():
        return 0.0

    q0 = {name: 0.0 for name in JOINTS}
    q0.update(joint1=0.2, joint2=0.7, joint3=0.4, joint4=-0.3, joint5=0.2, joint6=0.1)
    transport = RecordingTransport(q0=q0, clock=clock)
    session = ArmSession(clock=clock, transport=transport)
    bundle = load_model()
    try:
        plant = PhysicsPlant(bundle, transport)
        before = tuple(x.copy() for x in transport.get_state())
        session.hold(q0)
        after = transport.get_state()
        np.testing.assert_array_equal(after[0], before[0])
        np.testing.assert_array_equal(after[1], before[1])
        np.testing.assert_array_equal(after[2], before[2])
        plant.step(0.001)
        assert bundle.data.time == pytest.approx(0.001)
        assert float(transport.get_state()[0][1]) != 0.7
    finally:
        bundle.close()


def _plant_with_command(q_des, v_des=None, kp=None, kd=None, ff=None):
    transport = RecordingTransport()
    bundle = load_model()
    names = list(JOINTS)
    transport.commands.append(MitCommand(
        0.0, dict(q_des), dict(v_des or {n: 0.0 for n in names}),
        dict(kp or {n: 0.0 for n in names}), dict(kd or {n: 0.0 for n in names}),
        dict(ff or {n: 0.0 for n in names}),
    ))
    return bundle, transport, PhysicsPlant(bundle, transport)


def test_general_actuator_applies_position_velocity_and_feedforward_contract():
    q0 = {n: 0.0 for n in JOINTS}
    ff = {n: 0.0 for n in JOINTS}
    ff["joint2"] = 1.25
    bundle, transport, plant = _plant_with_command(
        q0, kp={n: 50.0 for n in JOINTS}, kd={n: 3.0 for n in JOINTS}, ff=ff
    )
    try:
        plant.step(0.001)
        np.testing.assert_allclose(plant.samples[-1]["tau"], [0.0, 1.25, 0, 0, 0, 0], atol=1e-6)
    finally:
        bundle.close()

    qdes = {n: 0.0 for n in JOINTS}
    qdes["joint1"] = 0.1
    bundle, transport, plant = _plant_with_command(qdes, kp={n: 50.0 for n in JOINTS})
    try:
        plant.step(0.001)
        assert plant.samples[-1]["tau"][0] == pytest.approx(5.0, abs=1e-5)
    finally:
        bundle.close()

    vdes = {n: 0.0 for n in JOINTS}
    vdes["joint1"] = 1.0
    bundle, transport, plant = _plant_with_command(
        q0, v_des=vdes, kd={n: 3.0 for n in JOINTS}
    )
    try:
        plant.step(0.001)
        assert plant.samples[-1]["tau"][0] == pytest.approx(3.0, abs=1e-5)
    finally:
        bundle.close()


def test_general_actuator_force_limit_saturates():
    qdes = {n: 0.0 for n in JOINTS}
    qdes["joint2"] = 2.0
    bundle, transport, plant = _plant_with_command(qdes, kp={n: 50.0 for n in JOINTS})
    try:
        plant.step(0.001)
        assert plant.samples[-1]["tau"][1] == pytest.approx(36.0, abs=1e-5)
    finally:
        bundle.close()


@pytest.mark.parametrize("q", [
    np.zeros(8),
    np.array([0.2, 0.7, 0.4, -0.3, 0.2, 0.1, 0.0, 0.0]),
    np.array([-0.3, 0.5, 0.8, 0.2, -0.2, 0.3, 0.0, 0.0]),
])
def test_mujoco_tcp_fk_matches_pinocchio(q):
    bundle = load_model()
    try:
        bundle.data.qpos[:] = q[:6]
        mujoco.mj_forward(bundle.model, bundle.data)
        pm = pin.buildModelFromUrdf(str(assets.urdf_path()))
        pd = pm.createData()
        pin.forwardKinematics(pm, pd, q)
        pin.updateFramePlacements(pm, pd)
        frame = pd.oMf[pm.getFrameId("gripper_end")]
        body = bundle.body_ids["gripper_end"]
        np.testing.assert_allclose(bundle.data.xpos[body], frame.translation, rtol=0, atol=1e-6)
        np.testing.assert_allclose(bundle.data.xmat[body].reshape(3, 3), frame.rotation, rtol=0, atol=1e-6)
    finally:
        bundle.close()


def test_motion_trajectory_converges_between_one_and_half_ms_steps():
    q_target = {name: 0.06 for name in JOINTS}
    trajectories = []
    for dt in (0.001, 0.0005):
        now = [0.0]
        transport = RecordingTransport(clock=lambda: now[0])
        session = ArmSession(clock=lambda: now[0], transport=transport)
        bundle = load_model()
        plant = PhysicsPlant(bundle, transport)
        for i in range(int(1.0 / dt)):
            now[0] = i * dt
            if i % int(round(0.01 / dt)) == 0:
                session.move_to(q_target, 1.0)
            plant.step(dt)
        trajectories.append(np.asarray([sample["q"] for sample in plant.samples]))
        bundle.close()
    coarse = trajectories[0]
    fine = trajectories[1][1::2][: len(coarse)]
    assert np.max(np.abs(coarse - fine)) <= 0.002


def test_no_command_has_zero_actuator_torque_but_gravity_still_moves_plant():
    q0 = {name: 0.0 for name in JOINTS}
    q0["joint2"] = 0.7
    transport = RecordingTransport(q0=q0)
    bundle = load_model()
    try:
        plant = PhysicsPlant(bundle, transport)
        plant.step(0.001)
        assert np.max(np.abs(plant.samples[-1]["tau"])) == pytest.approx(0.0, abs=1e-9)
        assert plant.samples[-1]["q"][1] != pytest.approx(0.7)
    finally:
        bundle.close()


def test_nonzero_hold_has_bounded_drift_and_reports_actual_torque():
    q = {n: 0.0 for n in JOINTS}
    q.update(joint1=0.2, joint2=0.7, joint3=0.4, joint4=-0.3, joint5=0.2, joint6=0.1)
    transport = RecordingTransport(q)
    session = ArmSession(transport=transport)
    bundle = load_model()
    try:
        plant = PhysicsPlant(bundle, transport)
        session.hold(q)
        for _ in range(2000):
            plant.step(0.001)
        drift = np.max(np.abs(np.asarray(plant.samples[-1]["q"]) - np.asarray(list(q.values()))))
        assert drift < 1e-4
        assert np.max(np.abs(plant.samples[-1]["tau"])) > 0.1
        assert np.max(np.abs(transport.get_state()[2][:6])) > 0.1
    finally:
        bundle.close()


@pytest.mark.parametrize("q", [
    np.zeros(8),
    np.array([0.2, 0.7, 0.4, -0.3, 0.2, 0.1, 0.01, 0.02]),
])
def test_mujoco_gravity_matches_pinocchio(q):
    bundle = load_model()
    try:
        bundle.data.qpos[:] = q[:6]
        bundle.data.qvel[:] = 0
        mujoco.mj_forward(bundle.model, bundle.data)
        pm = pin.buildModelFromUrdf(str(assets.urdf_path()))
        pd = pm.createData()
        q_pin = q.copy()
        q_pin[6:8] = 0.0
        gravity = pin.computeGeneralizedGravity(pm, pd, q_pin)
        np.testing.assert_allclose(bundle.data.qfrc_bias, gravity[:6], rtol=0, atol=1e-5)
    finally:
        bundle.close()
