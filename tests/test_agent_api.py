"""Agent control leases and endpoints."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.agent import AgentLease
from backend.app import app
from backend.arm import SimArm
from backend.core import Broadcaster, Controller
from backend.routines import RoutineStore
from backend.safety import SafetyLatch
from backend.shutter import SimShutter

JOINTS = ("joint1", "joint2", "joint3", "joint4", "joint5", "joint6", "gripper")


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, dt: float) -> None:
        self.now += dt


@pytest.fixture
def rig(tmp_path: Path):
    clock = FakeClock()
    arm = SimArm(JOINTS, clock=clock, tau=0.05)
    arm.connect()

    app.state.latch = SafetyLatch(clock=clock)
    app.state.routine_store = RoutineStore(tmp_path / "routines")
    app.state.broadcaster = Broadcaster()
    app.state.agent_lease = AgentLease(clock=clock)
    app.state.controller = Controller(
        arm=arm,
        shutter=SimShutter(),
        latch=app.state.latch,
        broadcaster=app.state.broadcaster,
        clock=clock,
    )
    return TestClient(app), clock, arm


@pytest.fixture
def client(rig) -> TestClient:
    return rig[0]


def take(client: TestClient, owner: str = "agent") -> str:
    r = client.post("/api/agent/acquire", json={"owner": owner})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def head(token: str) -> dict:
    return {"X-Agent-Token": token}


# ── lease ────────────────────────────────────────────────────────────────────


def test_acquire_grants_a_token(client: TestClient):
    assert client.get("/api/agent").json()["held"] is False

    token = take(client, "shot-list script")
    info = client.get("/api/agent").json()

    assert info["held"] is True
    assert info["owner"] == "shot-list script"
    assert token


def test_a_second_agent_is_refused_not_queued(client: TestClient):
    """Two callers interleaving commands on one arm produces motion neither
    asked for."""
    take(client, "first")
    r = client.post("/api/agent/acquire", json={"owner": "second"})

    assert r.status_code == 409
    assert "first" in r.json()["detail"]


def test_release_frees_the_arm_for_the_next_agent(client: TestClient):
    token = take(client)
    assert client.post("/api/agent/release", headers=head(token)).status_code == 200
    assert client.get("/api/agent").json()["held"] is False

    take(client, "next")


def test_release_needs_the_right_token(client: TestClient):
    take(client)
    assert client.post("/api/agent/release", headers=head("wrong")).status_code == 403
    assert client.get("/api/agent").json()["held"] is True


def test_the_ui_can_force_release_without_the_token(client: TestClient):
    """The person standing next to the arm outranks the process controlling it,
    and will not have its token."""
    take(client, "runaway agent")

    assert client.post("/api/agent/release?force=true").status_code == 200
    assert client.get("/api/agent").json()["held"] is False


def test_an_idle_lease_lapses(rig):
    """A crashed agent must not hold the arm until someone notices."""
    client, clock, _ = rig
    take(client)

    clock.advance(5 * 60 + 1)
    assert client.get("/api/agent").json()["held"] is False


def test_activity_keeps_the_lease_alive(rig):
    client, clock, _ = rig
    token = take(client)

    for _ in range(5):
        clock.advance(4 * 60)
        assert client.post("/api/agent/control/stop", headers=head(token)).status_code == 200

    assert client.get("/api/agent").json()["held"] is True


def test_the_hard_ceiling_expires_even_a_busy_lease(rig):
    """A stuck loop that keeps sending commands must not hold the arm forever."""
    client, clock, _ = rig
    token = take(client)

    for _ in range(20):
        clock.advance(2 * 60)
        client.post("/api/agent/control/stop", headers=head(token))

    assert client.get("/api/agent").json()["held"] is False


# ── motion ───────────────────────────────────────────────────────────────────


def test_commands_without_a_lease_are_refused(client: TestClient):
    r = client.post("/api/agent/control/joints", json={"joints": {"joint1": 0.2}})
    assert r.status_code == 403


def test_a_wrong_token_is_refused(client: TestClient):
    take(client)
    r = client.post(
        "/api/agent/control/joints", json={"joints": {"joint1": 0.2}}, headers=head("nope")
    )
    assert r.status_code == 403


def test_a_valid_command_moves_the_arm(rig):
    client, _, arm = rig
    token = take(client)

    r = client.post(
        "/api/agent/control/joints",
        json={"joints": {"joint1": 0.2}, "duration_s": 1.0},
        headers=head(token),
    )
    assert r.status_code == 200

    for _ in range(400):
        arm.step(0.01)
    assert arm.read_state().positions["joint1"] == pytest.approx(0.2, abs=1e-3)


def test_an_out_of_range_pose_is_rejected(client: TestClient):
    token = take(client)
    r = client.post(
        "/api/agent/control/joints", json={"joints": {"joint1": 9.0}}, headers=head(token)
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "unsafe_pose"


def test_a_huge_single_move_is_rejected(client: TestClient):
    """Almost always a model mistake rather than an intention, and the one that
    hurts."""
    token = take(client)
    r = client.post(
        "/api/agent/control/joints", json={"joints": {"joint1": 2.5}}, headers=head(token)
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "move_too_large"


def test_an_agent_is_refused_while_the_stop_is_engaged(client: TestClient):
    """Holding the lease grants control, not permission to move a stopped arm."""
    token = take(client)
    client.post("/api/estop", json={"reason": "operator stop"})

    r = client.post(
        "/api/agent/control/joints", json={"joints": {"joint1": 0.2}}, headers=head(token)
    )
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "estop_latched"


def test_an_agent_can_play_a_stored_routine(client: TestClient):
    rid = client.post("/api/routines", json={"name": "agent shoot"}).json()["id"]
    client.post(f"/api/routines/{rid}/waypoints", json={"joints": {"joint1": 0.2}})

    token = take(client)
    r = client.post(f"/api/agent/control/play/{rid}", headers=head(token))

    assert r.status_code == 200
    assert client.get("/api/control").json()["mode"] == "playback"


def test_playing_an_unknown_routine_is_404(client: TestClient):
    token = take(client)
    assert client.post("/api/agent/control/play/nope", headers=head(token)).status_code == 404


def test_an_agent_cannot_command_joints_while_teaching(client: TestClient):
    token = take(client)
    client.post("/api/teach", json={"enabled": True})

    r = client.post(
        "/api/agent/control/joints", json={"joints": {"joint1": 0.2}}, headers=head(token)
    )
    assert r.status_code == 409
    assert "teach" in r.json()["detail"]


def test_the_agent_api_appears_in_openapi(client: TestClient):
    """The schema imports directly as a tool definition, so it has to be there
    and it has to describe the token."""
    paths = client.get("/openapi.json").json()["paths"]

    assert "/api/agent/acquire" in paths
    assert "/api/agent/control/joints" in paths

    params = paths["/api/agent/control/joints"]["post"].get("parameters", [])
    assert any(p["name"] == "X-Agent-Token" for p in params)
