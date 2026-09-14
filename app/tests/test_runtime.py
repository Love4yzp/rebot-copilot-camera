"""Physical runtime integration; no CAN, backend server or operator data."""

import asyncio
from dataclasses import replace
from threading import Event
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytest.importorskip("mujoco")
from reBotArm_control_py.end_effector import EndEffectorSpec, build_end_effector_model
from reBotArm_control_py.physics import MujocoTransport
from reBotArm_control_py.collision import RobotModel
from backend import assets
from backend.api import simulation
from backend.arm.session import ArmSession
from backend.core import Broadcaster, Controller
from backend.runtime import Runtime, runtime_tuning_store
from backend.safety import SafetyLatch, LatchSource
from backend.tuning import TuningConfig


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


@pytest.fixture
def rig():
    clock = Clock()
    transport = MujocoTransport(
        urdf_path=assets.urdf_path(),
        joint_names=assets.joint_names(),
        arm_joint_names=assets.arm_joint_names(),
        clock=clock,
    )
    arm = ArmSession(transport=transport, clock=clock, model_path=str(assets.urdf_path()))
    arm.connect()
    latch = SafetyLatch(clock=clock)
    controller = Controller(arm, latch, Broadcaster(), clock=clock)
    controller.simulation = transport
    controller.tick()
    app = FastAPI()
    app.include_router(simulation.router)
    app.state.controller, app.state.latch = controller, latch
    yield SimpleNamespace(
        transport=transport,
        arm=arm,
        clock=clock,
        controller=controller,
        latch=latch,
        client=TestClient(app),
    )
    transport.close()


def test_sim_uses_the_same_mit_session_and_getters_do_not_step(rig):
    rig.arm.move_to({"joint2": 0.2}, 2)
    before = rig.arm.read_state().positions
    for _ in range(10):
        assert rig.arm.read_state().positions == before
    assert rig.transport.plant.t == 0
    for _ in range(10):
        rig.clock.now += 0.001
        rig.transport.step()
    assert rig.transport.plant.t == pytest.approx(0.01)
    assert rig.transport.diagnostics()["model_source"] == "simulation"
    assert len(rig.transport.diagnostics()["error"]) == 6


def test_force_input_is_sim_only_teach_only_bounded_and_expires(rig):
    body = {"joint": "joint1", "torque": 1, "duration_s": 0.15}
    assert rig.client.post("/api/sim/perturb", json=body).status_code == 409
    rig.controller.set_teaching(True)
    assert rig.client.post("/api/sim/perturb", json=body).status_code == 200
    rig.transport.step()
    assert rig.transport.bundle.data.qfrc_applied[0] == 1
    rig.clock.now = 0.15
    rig.transport.step()
    assert rig.transport.bundle.data.qfrc_applied[0] == 0
    for invalid in (
        {"duration_s": 0.201},
        {"duration_s": 0},
        {"torque": 1e6},
        {"joint": "gripper"},
    ):
        assert rig.client.post("/api/sim/perturb", json=body | invalid).status_code in {409, 422}
    rig.controller.simulation = None
    assert rig.client.post("/api/sim/perturb", json=body).status_code == 409


@pytest.mark.parametrize("stop", [False, True])
def test_external_force_is_cleared_on_exit_teach_or_estop(rig, stop):
    rig.controller.set_teaching(True)
    rig.controller.perturb("joint1", 1, 0.2)
    if stop:
        rig.latch.engage("test", LatchSource.API)
    else:
        rig.controller.set_teaching(False)
    rig.controller.tick()
    rig.transport.step()
    assert not np.any(rig.transport.bundle.data.qfrc_applied)
    if stop:
        assert rig.controller.mode == "estop"
        assert rig.transport.commands[-1].kp


def test_physics_advances_without_any_browser(monkeypatch):
    transport = MujocoTransport(
        urdf_path=assets.urdf_path(),
        joint_names=assets.joint_names(),
        arm_joint_names=assets.arm_joint_names(),
    )
    stepped = Event()
    original = transport.step

    def observed_step():
        original()
        if transport.plant.t >= 0.01:
            stepped.set()

    monkeypatch.setattr(transport, "step", observed_step)
    try:
        transport.start()
        assert stepped.wait(3), "the physical worker never advanced"
    finally:
        transport.close()


def test_sim_tuning_is_separate_from_real_files(tmp_path, monkeypatch):
    from backend import config

    real = tmp_path / "real.yaml"
    real.write_text("operator calibration, do not touch")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "TUNING_FILE", real)
    store = runtime_tuning_store(True)
    store.save(store.load())
    assert real.read_text() == "operator calibration, do not touch"
    assert (tmp_path / "sim/tuning.yaml").exists()


@pytest.mark.parametrize("profile", ["bare", "gripper", "custom"])
def test_end_effector_model_is_shared_by_session_physics_and_viewer(profile, monkeypatch, tmp_path):
    import json

    tuning = TuningConfig()
    if profile == "custom":
        spec = tmp_path / "tool.json"
        spec.write_text(
            json.dumps(dict(name="test tool", mass=0.2, com=[0, 0, 0.05], box=[0.04, 0.04, 0.1]))
        )
        monkeypatch.setenv("REBOT_END_EFFECTOR_FILE", str(spec))
    else:
        monkeypatch.delenv("REBOT_END_EFFECTOR_FILE", raising=False)
        tuning = tuning.model_copy(
            update={
                "payload": tuning.payload.model_copy(
                    update={"profile": type(tuning.payload.profile)(profile)}
                )
            }
        )
    runtime = Runtime(simulated=True, tuning=tuning)
    try:
        assert isinstance(runtime.arm, ArmSession)
        assert runtime.arm.model_path == runtime.viewer.urdf_path == str(runtime.model_path)
        assert runtime.physics.bundle.model.nq == 6
        assert runtime.inertia_source == ("box-estimate" if profile == "custom" else "stock-urdf")
        assert runtime.arm.model_locked
    finally:
        asyncio.run(runtime.close(SimpleNamespace()))


def test_invalid_inertia_is_not_silently_accepted():
    spec = EndEffectorSpec(name="tool", mass=0.2, com=(0, 0, 0.05), box=(0.04, 0.04, 0.1))
    assert spec.inertia_estimated
    with pytest.raises(ValueError, match="triangle"):
        replace(spec, inertia=(1, 1, 100, 0, 0, 0))


def test_a_failed_physics_worker_does_not_publish_frozen_feedback_as_live(rig, monkeypatch):
    def fail():
        raise ValueError("integration failed")

    monkeypatch.setattr(rig.transport, "step", fail)
    rig.transport._run()
    with pytest.raises(RuntimeError, match="physical simulation stopped"):
        rig.arm.read_state()


def test_unmodelled_gripper_motion_is_rejected_at_preflight(rig):
    assert any("gripper" in reason for reason in rig.controller.preflight_pose({"gripper": 0.3}))


def test_new_tool_collision_at_neutral_is_not_excluded_as_structural():
    spec = EndEffectorSpec(name="oversize", mass=0.2, com=(0, 0, 0), box=(2, 2, 2))
    owner, path = build_end_effector_model(str(assets.urdf_path()), profile="custom", spec=spec)
    try:
        model = RobotModel(
            urdf_path=str(path),
            structural_urdf_path=str(assets.urdf_path()),
            package_dir=str(assets.urdf_path().parent.parent),
            joint_names=assets.arm_joint_names(),
            end_effector_frame=assets.end_effector_frame(),
        )
        assert model.check_self_collision({n: 0 for n in assets.arm_joint_names()})
    finally:
        owner.cleanup()


def test_hardware_connect_failure_does_not_fall_back_to_simulation(monkeypatch):
    runtime = Runtime(simulated=False, tuning=TuningConfig())
    monkeypatch.setattr(runtime.viewer, "start", lambda: None)
    monkeypatch.setattr(
        runtime.arm, "connect", lambda: (_ for _ in ()).throw(OSError("can0 unavailable"))
    )
    try:
        with pytest.raises(OSError, match="can0"):
            runtime.connect()
        assert runtime.physics is None
    finally:
        asyncio.run(runtime.close(SimpleNamespace()))
