"""The pose library over HTTP: CRUD, capture, links, and goto."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.actions import ActionRegistry, InlineRunner, ShutterProvider
from backend.app import app
from backend.arm import SimArm
from backend.core import Broadcaster, Controller
from backend.sequences import PoseStore, SequenceStore, TemplateStore
from backend.safety import SafetyLatch
from backend.shutter import SimShutter

JOINTS = ("joint1", "joint2")


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def wire(tmp_path: Path, joints=JOINTS):
    """Swap the app's stores and controller for a two-joint test rig."""
    clock = FakeClock()
    arm = SimArm(joints, clock=clock, tau=0.05)
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
        # Inline, so the fake clock above drives everything and no assertion
        # depends on thread scheduling. That the threaded runner keeps the loop
        # free while a provider blocks is tested in test_action_runner.py.
        actions=runner,
    )
    return TestClient(app), app.state.controller, arm, clock


@pytest.fixture
def rig(tmp_path: Path):
    return wire(tmp_path)


@pytest.fixture
def client(rig) -> TestClient:
    return rig[0]


def make_pose(client: TestClient, j1: float, name: str = "正面") -> str:
    r = client.post("/api/poses", json={"name": name, "joints": {"joint1": j1, "joint2": 0.0}})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def run_loop(controller, arm, clock, max_steps: int = 5000) -> None:
    """Drive the control loop by hand until the executor finishes."""
    for _ in range(max_steps):
        clock.now += 0.01
        arm.step(0.01)
        controller.tick()
        if not controller.is_playing:
            break


# ── CRUD ─────────────────────────────────────────────────────────────────────


def test_create_list_patch_delete(client: TestClient):
    created = client.post("/api/poses", json={"name": "正面", "joints": {"joint1": 0.2}})
    assert created.status_code == 201
    pose = created.json()
    assert pose["joints"] == {"joint1": 0.2}
    assert set(pose) == {"id", "name", "joints", "created_at", "updated_at"}

    assert [p["name"] for p in client.get("/api/poses").json()] == ["正面"]

    renamed = client.patch(f"/api/poses/{pose['id']}", json={"name": "侧面"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "侧面"
    assert renamed.json()["joints"] == {"joint1": 0.2}, "a rename does not move the joints"

    assert client.delete(f"/api/poses/{pose['id']}").status_code == 204
    assert client.get("/api/poses").json() == []


def test_unknown_pose_is_404(client: TestClient):
    assert client.patch("/api/poses/nope", json={"name": "x"}).status_code == 404
    assert client.delete("/api/poses/nope").status_code == 404
    assert client.get("/api/poses/nope/links").status_code == 404
    assert client.post("/api/poses/nope/goto").status_code == 404


def test_empty_name_and_missing_joints_are_400(client: TestClient):
    assert client.post("/api/poses", json={"name": "", "joints": {"joint1": 0.1}}).status_code == 400
    assert client.post("/api/poses", json={"name": "x"}).status_code == 400
    pid = make_pose(client, 0.1)
    assert client.patch(f"/api/poses/{pid}", json={"name": " "}).status_code == 400


def test_an_out_of_range_pose_is_rejected_with_the_joint_name(client: TestClient):
    r = client.post("/api/poses", json={"name": "x", "joints": {"joint1": 9.0}})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail["error"] == "unsafe_pose"
    assert any("joint1" in reason for reason in detail["reasons"])


def test_a_self_colliding_pose_is_rejected(client: TestClient):
    """link3 folded back into the base — legal per joint, illegal as a pose."""
    folded = {
        "joint1": 2.394, "joint2": 3.039, "joint3": 0.046,
        "joint4": 1.142, "joint5": 1.511, "joint6": 2.871,
    }
    r = client.post("/api/poses", json={"name": "folded", "joints": folded})
    assert r.status_code == 400
    assert any("collides" in reason for reason in r.json()["detail"]["reasons"])


def test_patching_joints_still_validates(client: TestClient):
    pid = make_pose(client, 0.1)
    r = client.patch(f"/api/poses/{pid}", json={"joints": {"joint1": 9.0}})
    assert r.status_code == 400
    assert client.get("/api/poses").json()[0]["joints"]["joint1"] == 0.1


# ── capture ──────────────────────────────────────────────────────────────────


def test_capture_records_the_arms_current_pose(rig):
    client, _, arm, _ = rig
    client.post("/api/teach", json={"enabled": True})
    arm.drag({"joint1": 0.42})

    r = client.post("/api/poses/capture", json={"name": "示教位姿"})
    assert r.status_code == 201
    assert r.json()["joints"]["joint1"] == pytest.approx(0.42)
    assert r.json()["name"] == "示教位姿"


def test_capture_works_while_stopped(rig):
    """An operator who just hit stop may well want the pose it stopped at."""
    client, *_ = rig
    client.post("/api/estop", json={"reason": "stop"})
    try:
        assert client.post("/api/poses/capture", json={"name": "停下这"}).status_code == 201
    finally:
        client.post("/api/estop/clear")


def test_capture_needs_a_name(client: TestClient):
    assert client.post("/api/poses/capture", json={"name": ""}).status_code == 400


# ── links ────────────────────────────────────────────────────────────────────


def test_links_report_which_sequences_reference_a_pose(client: TestClient):
    a = make_pose(client, 0.2, "被引用")
    unused = make_pose(client, 0.3, "没人用")

    sid = client.post("/api/sequences", json={"name": "两轮"}).json()["id"]
    client.patch(f"/api/sequences/{sid}", json={"blocks": [
        {"type": "hold", "pose_id": a, "duration_s": 1.0, "markers": []},
        {"type": "hold", "pose_id": a, "duration_s": 2.0, "markers": []},
    ]})

    links = client.get(f"/api/poses/{a}/links").json()
    assert links["pose_id"] == a
    assert links["count"] == 1
    assert links["links"] == [
        {"sequence_id": sid, "sequence_name": "两轮", "block_count": 2}
    ]

    assert client.get(f"/api/poses/{unused}/links").json()["count"] == 0


# ── goto ─────────────────────────────────────────────────────────────────────


def test_goto_moves_to_the_pose_and_stays(rig):
    """The library card's "去这里": eased move, arrival, hold, done."""
    client, controller, arm, clock = rig
    pid = make_pose(client, 0.8, "侧面")

    r = client.post(f"/api/poses/{pid}/goto")
    assert r.status_code == 200
    playback = r.json()["playback"]
    assert playback["sequence_id"] == pid
    assert playback["sequence_name"] == "位姿 · 侧面"
    assert playback["block_total"] == 1
    assert playback["phase"] == "transition"

    run_loop(controller, arm, clock)
    assert controller.executor.phase.value == "done"
    assert arm.read_state().positions["joint1"] == pytest.approx(0.8, abs=0.02)


def test_goto_is_409_while_the_stop_is_engaged(client: TestClient):
    pid = make_pose(client, 0.2)
    client.post("/api/estop", json={"reason": "stop"})

    r = client.post(f"/api/poses/{pid}/goto")
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "estop_latched"


def test_goto_is_409_while_teaching_or_playing(rig):
    client, *_ = rig
    a = make_pose(client, 0.2)
    b = make_pose(client, 0.9)

    client.post("/api/teach", json={"enabled": True})
    assert client.post(f"/api/poses/{a}/goto").status_code == 409
    client.post("/api/teach", json={"enabled": False})

    sid = client.post("/api/sequences", json={"name": "long"}).json()["id"]
    client.patch(f"/api/sequences/{sid}", json={"blocks": [
        {"type": "hold", "pose_id": b, "duration_s": 30.0, "markers": []}]})
    assert client.post(f"/api/sequences/{sid}/execute").status_code == 200
    assert client.post(f"/api/poses/{a}/goto").status_code == 409


def test_goto_preflights_the_path_from_the_current_pose(tmp_path: Path):
    """Two legal poses, an illegal line between them — refuse before moving.

    Needs a six-joint arm so the "current pose" half of the path is real, so
    this test builds its own rig rather than using the two-joint fixture.
    """
    client, controller, arm, _ = wire(
        tmp_path, ("joint1", "joint2", "joint3", "joint4", "joint5", "joint6"))

    here = {
        "joint1": -0.882, "joint2": 3.107, "joint3": 0.686,
        "joint4": -0.132, "joint5": 1.482, "joint6": -3.098,
    }
    there = {
        "joint1": -1.148, "joint2": 2.579, "joint3": 0.301,
        "joint4": 1.345, "joint5": 1.051, "joint6": -2.242,
    }
    pid = client.post("/api/poses", json={"name": "那头", "joints": there}).json()["id"]
    arm.drag(here)  # from rest, a delta is an absolute pose

    r = client.post(f"/api/poses/{pid}/goto")
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "unsafe_path"
    assert client.get("/api/control").json()["playing"] is False
