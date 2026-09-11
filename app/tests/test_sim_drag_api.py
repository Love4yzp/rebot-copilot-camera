"""POST /api/sim/drag — the simulator-only hand push.

A hand, not a command: the endpoint deliberately bypasses the activity table,
so no test picks an activity to refuse it in -- the two interactions worth
pinning are with the motion gate (an engaged stop outranks every hand) and
with teaching (a push into a locked teaching arm is how drag teaching
starts).
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.actions import ActionRegistry, InlineRunner, ShutterProvider
from backend.api.sim import DragRequest
from backend.app import app
from backend.arm import ArmState, SimArm
from backend.arm.limits import urdf_joint_bounds
from backend.arm.profile import DEFAULT_LIMITS
from backend.core import Broadcaster, Controller
from backend.sequences import PoseStore, SequenceStore, TemplateStore
from backend.safety import SafetyLatch
from backend.shutter import SimShutter

#: The full hardware joint set, so the drag response and the broadcast
#: positions share one shape.
JOINTS = ("joint1", "joint2", "joint3", "joint4", "joint5", "joint6", "gripper")


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def wire(tmp_path: Path, arm=None):
    clock = FakeClock()
    arm = arm or SimArm(JOINTS, clock=clock, tau=0.05)
    arm.connect()

    app.state.latch = SafetyLatch(clock=clock)
    app.state.pose_store = PoseStore(tmp_path / "poses")
    app.state.sequence_store = SequenceStore(tmp_path / "sequences")
    app.state.template_store = TemplateStore(tmp_path / "templates")
    app.state.broadcaster = Broadcaster()
    shutter = SimShutter()
    runner = InlineRunner()
    app.state.plugins = ActionRegistry(runner)
    app.state.plugins.register(ShutterProvider(shutter))
    app.state.controller = Controller(
        arm=arm,
        shutter=shutter,
        latch=app.state.latch,
        broadcaster=app.state.broadcaster,
        clock=clock,
        actions=runner,
    )
    return TestClient(app), app.state.controller, arm, clock


@pytest.fixture
def rig(tmp_path: Path):
    return wire(tmp_path)


def step(controller, arm, clock, n: int = 1) -> None:
    for _ in range(n):
        clock.now += 0.01
        arm.step(0.01)
        controller.tick()


def test_drag_reports_a_full_position_map_matching_read_state(rig):
    client, controller, arm, clock = rig

    r = client.post("/api/sim/drag", json={"deltas": {"joint1": 0.2, "gripper": 0.01}})
    assert r.status_code == 200
    assert set(r.json()["positions"]) == set(JOINTS)
    assert r.json()["positions"] == dict(arm.read_state().positions)
    assert r.json()["positions"]["joint1"] == 0.2
    assert r.json()["positions"]["gripper"] == 0.01
    assert r.json()["positions"]["joint3"] == 0.0


def test_drag_clamps_to_the_urdf_joint_bounds(rig):
    client, controller, arm, clock = rig
    upper = urdf_joint_bounds()["joint1"][1]

    last = None
    for _ in range(10):
        r = client.post("/api/sim/drag", json={"deltas": {"joint1": 0.5}})
        assert r.status_code == 200
        last = r.json()["positions"]["joint1"]
        assert last <= upper

    # Unclamped, ten 0.5 pushes would land at 5.0 — well past the URDF limit.
    assert last == upper


def test_unknown_joint_is_a_400_with_the_frozen_body(rig):
    client, controller, arm, clock = rig

    r = client.post("/api/sim/drag", json={"deltas": {"joint9": 0.01}})
    assert r.status_code == 400
    assert r.json() == {"detail": "unknown joints: joint9"}


def test_unknown_joints_are_sorted_and_joined(rig):
    client, controller, arm, clock = rig

    r = client.post("/api/sim/drag", json={"deltas": {"joint9": 0.1, "joint7": 0.1}})
    assert r.status_code == 400
    assert r.json() == {"detail": "unknown joints: joint7, joint9"}


def test_deltas_outside_plus_minus_half_are_422(rig):
    client, controller, arm, clock = rig

    assert client.post("/api/sim/drag", json={"deltas": {"joint1": 0.51}}).status_code == 422
    assert client.post("/api/sim/drag", json={"deltas": {"joint1": -0.51}}).status_code == 422
    assert client.post("/api/sim/drag", json={"deltas": {"joint1": "a rad"}}).status_code == 422
    # The inclusive edges are legal.
    assert client.post("/api/sim/drag", json={"deltas": {"joint1": 0.5}}).status_code == 200
    assert client.post("/api/sim/drag", json={"deltas": {"joint1": -0.5}}).status_code == 200


def test_nan_and_inf_deltas_are_rejected_by_the_model(rig):
    """``allow_inf_nan=False`` keeps non-finite deltas out of the controller.

    NaN cannot be exercised through the wire here: a raw ``NaN`` payload is
    the one case FastAPI's own 422 envelope cannot answer -- starlette
    refuses to serialize the ``input: nan`` it echoes back -- and httpx
    refuses to encode it client-side. That echo quirk is FastAPI-wide, shared
    by every endpoint in the app, not a gap in this endpoint's validation.
    """
    with pytest.raises(ValidationError):
        DragRequest.model_validate({"deltas": {"joint1": float("nan")}})
    with pytest.raises(ValidationError):
        DragRequest.model_validate({"deltas": {"joint1": float("inf")}})
    with pytest.raises(ValidationError):
        DragRequest.model_validate({"deltas": {"joint1": float("-inf")}})


def test_drag_is_refused_while_the_stop_is_engaged(rig):
    client, controller, arm, clock = rig
    assert client.post("/api/estop", json={"reason": "hand in the way"}).status_code == 200

    r = client.post("/api/sim/drag", json={"deltas": {"joint1": 0.1}})
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "estop_latched"


class StubArm:
    """Minimal non-simulator ArmDriver: every verb exists, nothing moves."""

    joint_names = ("joint1", "joint2")
    is_connected = True
    is_floating = False

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        pass

    def read_state(self) -> ArmState:
        return ArmState(positions={n: 0.0 for n in self.joint_names})

    def hold(self, q_target) -> None:
        pass

    def move_to(self, q_target, duration_s: float) -> float:
        return duration_s

    def prepare_move(self, q_target, requested_duration: float, *, limits=DEFAULT_LIMITS):
        raise NotImplementedError

    def commit_move(self, prepared) -> float:
        raise NotImplementedError

    def relax(self) -> None:
        pass

    def set_float(self, enabled: bool) -> None:
        pass

    def follow(self) -> None:
        pass

    def set_gravity_correction(self, scale, bias) -> None:
        pass

    def set_float_gains(self, kp: float, kd: float) -> None:
        pass

    def reload_dynamics(self, payload=None) -> None:
        pass


def test_drag_is_409_on_a_non_simulator_arm(tmp_path: Path):
    client, controller, arm, clock = wire(tmp_path, arm=StubArm())

    r = client.post("/api/sim/drag", json={"deltas": {"joint1": 0.1}})
    assert r.status_code == 409
    assert r.json() == {"detail": "drag is only available against the simulator"}


def test_teach_drag_capture_chain(rig):
    """Dragging a teaching arm is how drag teaching starts: the push wakes
    the floatlock out of its lock, float holds the pose, and capture records
    exactly where the arm was left."""
    client, controller, arm, clock = rig

    r = client.post("/api/teach", json={"enabled": True})
    assert r.status_code == 200
    assert r.json()["teaching"] is True

    r = client.post("/api/sim/drag", json={"deltas": {"joint1": 0.15, "joint2": 0.1}})
    assert r.status_code == 200

    steps = 0
    while not arm.is_floating and steps < 200:
        step(controller, arm, clock)
        steps += 1
    assert arm.is_floating, "a push into a locked teaching arm must wake the floatlock"

    # The arm is free now; a push lands exactly and follow holds it there.
    held = client.post("/api/sim/drag", json={"deltas": {"joint1": 0.05}}).json()["positions"]

    # Settle past the 0.25 s still window: the lock re-engages, the pose stays.
    step(controller, arm, clock, n=60)
    assert arm.read_state().positions == held

    r = client.post("/api/poses/capture", json={"name": "拖拽位"})
    assert r.status_code == 201
    pose = app.state.pose_store.get(r.json()["id"])
    assert pose.joints == held