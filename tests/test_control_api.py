"""Motion endpoints and the state websocket, over HTTP."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.arm import SimArm
from backend.core import Broadcaster, Controller
from backend.routines import RoutineStore
from backend.safety import SafetyLatch
from backend.shutter import SimShutter

JOINTS = ("joint1", "joint2")


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def rig(tmp_path: Path):
    clock = FakeClock()
    arm = SimArm(JOINTS, clock=clock, tau=0.05)
    arm.connect()

    app.state.latch = SafetyLatch(clock=clock)
    app.state.routine_store = RoutineStore(tmp_path / "routines")
    app.state.broadcaster = Broadcaster()
    app.state.controller = Controller(
        arm=arm,
        shutter=SimShutter(),
        latch=app.state.latch,
        broadcaster=app.state.broadcaster,
        clock=clock,
    )
    return TestClient(app), app.state.controller, arm, clock


@pytest.fixture
def client(rig) -> TestClient:
    return rig[0]


def make_routine(client: TestClient, *angles: float) -> str:
    rid = client.post("/api/routines", json={"name": "shoot"}).json()["id"]
    for q in angles:
        client.post(
            f"/api/routines/{rid}/waypoints",
            json={"joints": {"joint1": q, "joint2": 0.0}},
        )
    return rid


# ── playback ─────────────────────────────────────────────────────────────────


def test_play_starts_a_routine(client: TestClient):
    rid = make_routine(client, 0.2, 0.4)

    r = client.post(f"/api/routines/{rid}/play")
    assert r.status_code == 200
    assert r.json()["mode"] == "playback"
    assert r.json()["playback"]["waypoint_total"] == 2


def test_play_refuses_an_empty_routine(client: TestClient):
    rid = client.post("/api/routines", json={"name": "empty"}).json()["id"]
    r = client.post(f"/api/routines/{rid}/play")
    assert r.status_code == 400


def test_play_is_409_while_the_stop_is_engaged(client: TestClient):
    rid = make_routine(client, 0.2)
    client.post("/api/estop", json={"reason": "stop"})

    r = client.post(f"/api/routines/{rid}/play")
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "estop_latched"
    assert r.json()["detail"]["reason"] == "stop"


def test_play_is_409_when_already_playing(client: TestClient):
    rid = make_routine(client, 0.9)
    client.post(f"/api/routines/{rid}/play")

    assert client.post(f"/api/routines/{rid}/play").status_code == 409


def test_stop_works_while_the_estop_is_engaged(client: TestClient):
    """Stopping must never be blocked by the thing that stopped you."""
    rid = make_routine(client, 0.9)
    client.post(f"/api/routines/{rid}/play")
    client.post("/api/estop", json={"reason": "stop"})

    assert client.post("/api/playback/stop").status_code == 200


def test_play_on_unknown_routine_is_404(client: TestClient):
    assert client.post("/api/routines/nope/play").status_code == 404


# ── teaching ─────────────────────────────────────────────────────────────────


def test_teach_toggles_float(rig):
    client, controller, arm, _ = rig

    assert client.post("/api/teach", json={"enabled": True}).json()["teaching"] is True
    assert arm.is_floating is True

    assert client.post("/api/teach", json={"enabled": False}).json()["teaching"] is False
    assert arm.is_floating is False


def test_teach_is_409_while_stopped(client: TestClient):
    client.post("/api/estop", json={"reason": "stop"})
    assert client.post("/api/teach", json={"enabled": True}).status_code == 409


# ── capture ──────────────────────────────────────────────────────────────────


def test_capture_records_the_arms_current_pose(rig):
    client, controller, arm, _ = rig
    rid = client.post("/api/routines", json={"name": "taught"}).json()["id"]

    client.post("/api/teach", json={"enabled": True})
    arm.drag({"joint1": 0.42})

    r = client.post(f"/api/routines/{rid}/waypoints/capture", json={"settle_ms": 700})
    assert r.status_code == 201

    waypoint = r.json()["waypoints"][0]
    assert waypoint["joints"]["joint1"] == pytest.approx(0.42)
    assert waypoint["settle_ms"] == 700


def test_capturing_three_poses_records_them_in_order(rig):
    """Drag, let go, press — three times. The whole teach loop."""
    client, controller, arm, _ = rig
    rid = client.post("/api/routines", json={"name": "taught"}).json()["id"]
    client.post("/api/teach", json={"enabled": True})

    for delta in (0.2, 0.3, -0.1):
        arm.drag({"joint1": delta})
        routine = client.post(f"/api/routines/{rid}/waypoints/capture").json()

    angles = [round(w["joints"]["joint1"], 6) for w in routine["waypoints"]]
    assert angles == [0.2, 0.5, 0.4]


def test_capture_works_while_stopped(rig):
    """An operator who just hit stop may well want the pose it stopped at."""
    client, controller, arm, _ = rig
    rid = client.post("/api/routines", json={"name": "taught"}).json()["id"]
    client.post("/api/estop", json={"reason": "stop"})

    assert client.post(f"/api/routines/{rid}/waypoints/capture").status_code == 201


# ── websocket ────────────────────────────────────────────────────────────────


def test_websocket_streams_control_state(rig):
    client, controller, arm, clock = rig

    with client.websocket_connect("/ws") as ws:
        controller.tick()
        message = ws.receive_json()

    assert message["type"] == "state"
    assert message["data"]["mode"] == "idle"
    assert "positions" in message["data"]


def test_websocket_reports_the_stop(rig):
    client, controller, arm, clock = rig

    with client.websocket_connect("/ws") as ws:
        client.post("/api/estop", json={"reason": "cable snagged", "source": "ui"})
        controller.tick()

        while True:
            message = ws.receive_json()
            if message["type"] == "state" and message["data"]["estop"]["latched"]:
                break

    assert message["data"]["mode"] == "estop"
    assert message["data"]["estop"]["reason"] == "cable snagged"


# ── pre-flight ───────────────────────────────────────────────────────────────


def test_play_preflights_the_path_between_legal_waypoints(client: TestClient):
    """Both poses are legal; the straight line between them goes through the
    base. Refuse before anything moves."""
    a = {
        "joint1": -0.882, "joint2": 3.107, "joint3": 0.686,
        "joint4": -0.132, "joint5": 1.482, "joint6": -3.098,
    }
    b = {
        "joint1": -1.148, "joint2": 2.579, "joint3": 0.301,
        "joint4": 1.345, "joint5": 1.051, "joint6": -2.242,
    }
    rid = client.post("/api/routines", json={"name": "through the base"}).json()["id"]
    for pose in (a, b):
        assert client.post(f"/api/routines/{rid}/waypoints", json={"joints": pose}).status_code == 201

    r = client.post(f"/api/routines/{rid}/play")
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "unsafe_routine"
    assert any("path 0->1" in reason for reason in r.json()["detail"]["reasons"])
    assert client.get("/api/control").json()["playing"] is False


def test_a_clean_routine_still_plays(client: TestClient):
    rid = make_routine(client, 0.2, 0.5)
    assert client.post(f"/api/routines/{rid}/play").status_code == 200


# ── shutter self-test ────────────────────────────────────────────────────────


def test_shutter_test_pings_without_burning_a_frame(rig):
    client, controller, _, _ = rig

    r = client.post("/api/shutter/test")
    assert r.status_code == 200
    assert r.json() == {
        "ok": True,
        "connected": True,
        "fired": False,
        "firmware_version": None,
        "error": None,
    }
    assert controller.shutter.pings == 1
    assert controller.shutter.shots == 0


def test_shutter_test_can_fire_on_request(rig):
    client, controller, _, _ = rig

    body = client.post("/api/shutter/test?focus=true&shoot=true").json()
    assert body["fired"] is True
    assert controller.shutter.focuses == 1
    assert controller.shutter.shots == 1


def test_shutter_test_reports_a_dead_link_rather_than_raising(rig):
    """Setting up on site, this is how a dead BLE link gets found before the
    arm walks a whole set with nothing landing on the card."""
    client, controller, _, _ = rig
    controller.shutter.set_connected(False)

    body = client.post("/api/shutter/test").json()
    assert body["ok"] is False
    assert body["connected"] is False
    assert "no link" in body["error"]
