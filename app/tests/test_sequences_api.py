"""Sequence CRUD, normalization on write, execution lockout, execute."""

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


@pytest.fixture
def rig(tmp_path: Path):
    clock = FakeClock()
    arm = SimArm(JOINTS, clock=clock, tau=0.05)
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
def client(rig) -> TestClient:
    return rig[0]


def make_pose(client: TestClient, j1: float, name: str = "正面") -> str:
    r = client.post("/api/poses", json={"name": name, "joints": {"joint1": j1, "joint2": 0.0}})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def make_sequence(client: TestClient, name: str = "shoot") -> str:
    return client.post("/api/sequences", json={"name": name}).json()["id"]


def hold(pose_id: str, duration_s: float = 1.0, markers=()) -> dict:
    return {"type": "hold", "pose_id": pose_id, "duration_s": duration_s,
            "markers": list(markers)}


def shutter(at: float, **params) -> dict:
    return {"kind": "shutter", "params": {"count": 1, "interval_s": 0.0,
            "focus_first": True, **params}, "at": at, "estimate_s": 0.3}


def set_blocks(client: TestClient, sid: str, blocks: list[dict]):
    return client.patch(f"/api/sequences/{sid}", json={"blocks": blocks})


def run_loop(controller, arm, clock, max_steps: int = 5000) -> None:
    """Drive the control loop by hand until the executor finishes."""
    for _ in range(max_steps):
        clock.now += 0.01
        arm.step(0.01)
        controller.tick()
        if not controller.is_playing:
            break


# ── CRUD ─────────────────────────────────────────────────────────────────────


def test_create_list_get_patch_delete(client: TestClient):
    created = client.post("/api/sequences", json={"name": "first"})
    assert created.status_code == 201
    sid = created.json()["id"]
    assert created.json()["schema_version"] == 2
    assert created.json()["blocks"] == []

    summaries = client.get("/api/sequences").json()
    assert [s["name"] for s in summaries] == ["first"]
    assert set(summaries[0]) == {"id", "name", "updated_at", "station_count", "duration_s"}
    assert client.get(f"/api/sequences/{sid}").json()["name"] == "first"

    renamed = client.patch(f"/api/sequences/{sid}", json={"name": "renamed"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "renamed"

    assert client.delete(f"/api/sequences/{sid}").status_code == 204
    assert client.get("/api/sequences").json() == []


def test_unknown_sequence_is_404(client: TestClient):
    assert client.get("/api/sequences/nope").status_code == 404
    assert client.delete("/api/sequences/nope").status_code == 404
    assert client.patch("/api/sequences/nope", json={"name": "x"}).status_code == 404
    assert client.post("/api/sequences/nope/execute").status_code == 404


def test_empty_name_is_rejected(client: TestClient):
    assert client.post("/api/sequences", json={"name": ""}).status_code == 400
    assert client.post("/api/sequences", json={"name": "  "}).status_code == 400


def test_created_sequence_survives_a_restart(client: TestClient, tmp_path: Path):
    """Sequences are the operator's work; a process restart must not lose them."""
    sid = make_sequence(client, "durable")
    pose_id = make_pose(client, 0.5)
    set_blocks(client, sid, [hold(pose_id, 2.0)])

    app.state.sequence_store = SequenceStore(tmp_path / "sequences")
    reloaded = TestClient(app).get(f"/api/sequences/{sid}").json()

    assert reloaded["name"] == "durable"
    assert reloaded["blocks"][0]["duration_s"] == 2.0


# ── write-side normalization ─────────────────────────────────────────────────


def test_blocks_are_normalized_on_write(client: TestClient):
    """The client sends holds; the stored document has the transitions the
    physics requires."""
    a, b = make_pose(client, 0.2, "a"), make_pose(client, 0.5, "b")
    sid = make_sequence(client)

    r = set_blocks(client, sid, [hold(a, 3.0), hold(b, 5.0)])
    assert r.status_code == 200

    blocks = r.json()["blocks"]
    assert [b["type"] for b in blocks] == ["hold", "transition", "hold"]
    assert blocks[1]["duration_s"] == 2.0
    assert blocks[1]["easing"] == "ease_in_out"

    stored = client.get(f"/api/sequences/{sid}").json()
    assert [b["type"] for b in stored["blocks"]] == ["hold", "transition", "hold"]


def test_same_pose_adjacent_holds_get_no_transition(client: TestClient):
    a = make_pose(client, 0.2)
    sid = make_sequence(client)
    blocks = set_blocks(client, sid, [hold(a), hold(a)]).json()["blocks"]
    assert [b["type"] for b in blocks] == ["hold", "hold"]


def test_summary_reports_stations_and_duration(client: TestClient):
    a, b = make_pose(client, 0.2, "a"), make_pose(client, 0.5, "b")
    sid = make_sequence(client)
    set_blocks(client, sid, [hold(a, 3.0), hold(b, 5.0)])

    summary = client.get("/api/sequences").json()[0]
    assert summary["station_count"] == 2
    assert summary["duration_s"] == 3.0 + 2.0 + 5.0


def test_marker_params_are_validated_on_write(client: TestClient):
    """A bad param found now is a typo; found mid-run it is an aborted shoot."""
    a = make_pose(client, 0.2)
    sid = make_sequence(client)

    r = set_blocks(client, sid, [hold(a, 1.0, [shutter(0.5, count=99)])])
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "bad_marker_params"
    assert "count" in r.json()["detail"]["reasons"][0]


def test_a_marker_for_a_provider_nobody_installed_is_refused_on_write(client: TestClient):
    a = make_pose(client, 0.2)
    sid = make_sequence(client)
    r = set_blocks(client, sid, [hold(a, 1.0, [
        {"kind": "nobody", "params": {}, "at": 0.5, "estimate_s": 0.3}])])
    assert r.status_code == 400
    assert "nobody" in r.json()["detail"]["reasons"][0]


def test_a_malformed_block_is_422_and_nothing_is_written(client: TestClient):
    sid = make_sequence(client)
    r = set_blocks(client, sid, [{"type": "loop", "duration_s": 1}])
    assert r.status_code == 422
    assert client.get(f"/api/sequences/{sid}").json()["blocks"] == []


def test_editing_is_allowed_while_the_stop_is_engaged(client: TestClient):
    """Often exactly what the operator is doing *because* the arm is stopped."""
    a = make_pose(client, 0.2)
    sid = make_sequence(client)
    client.post("/api/estop", json={"reason": "stop"})
    try:
        assert set_blocks(client, sid, [hold(a)]).status_code == 200
        assert client.patch(f"/api/sequences/{sid}", json={"name": "renamed"}).status_code == 200
    finally:
        client.post("/api/estop/clear")


# ── execute ──────────────────────────────────────────────────────────────────


def test_execute_starts_a_run(rig):
    client, controller, arm, clock = rig
    a, b = make_pose(client, 0.2, "a"), make_pose(client, 0.5, "b")
    sid = make_sequence(client)
    set_blocks(client, sid, [hold(a, 0.3), hold(b, 0.3)])

    r = client.post(f"/api/sequences/{sid}/execute")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "playback"
    assert body["playback"]["sequence_id"] == sid
    assert body["playback"]["block_total"] == 3
    assert body["playback"]["phase"] == "hold"

    run_loop(controller, arm, clock)
    assert controller.executor.phase.value == "done"
    assert arm.read_state().positions["joint1"] == pytest.approx(0.5, abs=0.02)


def test_execute_refuses_an_empty_sequence(client: TestClient):
    sid = make_sequence(client)
    r = client.post(f"/api/sequences/{sid}/execute")
    assert r.status_code == 400
    assert "no blocks" in r.json()["detail"]


def test_execute_is_409_while_the_stop_is_engaged(client: TestClient):
    a = make_pose(client, 0.2)
    sid = make_sequence(client)
    set_blocks(client, sid, [hold(a)])
    client.post("/api/estop", json={"reason": "stop"})

    r = client.post(f"/api/sequences/{sid}/execute")
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "estop_latched"
    assert r.json()["detail"]["reason"] == "stop"


def test_execute_is_409_when_already_playing(rig):
    client, *_ = rig
    a = make_pose(client, 0.9)
    sid = make_sequence(client)
    set_blocks(client, sid, [hold(a, 30.0)])
    assert client.post(f"/api/sequences/{sid}/execute").status_code == 200

    assert client.post(f"/api/sequences/{sid}/execute").status_code == 409


def test_execute_is_409_while_teaching(client: TestClient):
    a = make_pose(client, 0.2)
    sid = make_sequence(client)
    set_blocks(client, sid, [hold(a)])
    client.post("/api/teach", json={"enabled": True})

    assert client.post(f"/api/sequences/{sid}/execute").status_code == 409


def test_execute_refuses_a_sequence_whose_pose_is_gone(rig):
    """A pose deleted out from under a sequence is found before anything moves."""
    client, _, arm, _ = rig
    a = make_pose(client, 0.2)
    sid = make_sequence(client)
    set_blocks(client, sid, [hold(a)])
    assert client.delete(f"/api/poses/{a}").status_code == 204

    before = dict(arm.read_state().positions)
    r = client.post(f"/api/sequences/{sid}/execute")

    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "missing_poses"
    assert arm.read_state().positions == before


def test_execute_preflights_the_path_between_legal_poses(client: TestClient):
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
    pa = client.post("/api/poses", json={"name": "a", "joints": a})
    pb = client.post("/api/poses", json={"name": "b", "joints": b})
    assert pa.status_code == 201 and pb.status_code == 201
    sid = make_sequence(client)
    set_blocks(client, sid, [hold(pa.json()["id"]), hold(pb.json()["id"])])

    r = client.post(f"/api/sequences/{sid}/execute")
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "unsafe_sequence"
    assert any("path 1->2" in reason for reason in r.json()["detail"]["reasons"])
    assert client.get("/api/control").json()["playing"] is False


def test_execute_refuses_when_arm_is_at_a_self_colliding_pose(rig):
    """The arm's current position is self-colliding — the pre-flight check
    catches it before anything moves, even if the sequence's poses are all
    safe. Same logic as goto_pose reading the arm where it stands: two legal
    poses can have an illegal path between them, and the arm's position right
    now is only known at runtime."""
    client, controller, arm, clock = rig

    # Drag the arm to a self-colliding configuration (link3 folded into base).
    arm.drag({"joint1": 2.394, "joint2": 3.039})

    # A sequence with a single safe pose at rest.
    rest = {n: 0.0 for n in ("joint1", "joint2", "joint3", "joint4", "joint5", "joint6")}
    pa = client.post("/api/poses", json={"name": "rest", "joints": rest})
    assert pa.status_code == 201
    sid = make_sequence(client)
    set_blocks(client, sid, [hold(pa.json()["id"])])

    before = dict(arm.read_state().positions)
    r = client.post(f"/api/sequences/{sid}/execute")
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "unsafe_sequence"
    assert any("waypoint 0" in reason for reason in r.json()["detail"]["reasons"])
    assert arm.read_state().positions == before


def test_execute_allows_safe_path_when_arm_is_at_rest(rig):
    """The arm is at rest and the sequence is safe — execute proceeds.
    This is the baseline: with the current position prepended, a safe arm
    must not be rejected."""
    client, controller, arm, clock = rig
    a, b = make_pose(client, 0.2, "a"), make_pose(client, 0.5, "b")
    sid = make_sequence(client)
    set_blocks(client, sid, [hold(a, 0.3), hold(b, 0.3)])

    r = client.post(f"/api/sequences/{sid}/execute")
    assert r.status_code == 200
    run_loop(controller, arm, clock)
    assert controller.executor.phase.value == "done"
    assert arm.read_state().positions["joint1"] == pytest.approx(0.5, abs=0.02)


# ── the execution lockout (TIMELINE rule 5) ──────────────────────────────────


def test_blocks_cannot_be_edited_while_executing(rig):
    client, *_ = rig
    a, b = make_pose(client, 0.9, "a"), make_pose(client, 0.1, "b")
    sid = make_sequence(client)
    set_blocks(client, sid, [hold(a, 30.0)])
    assert client.post(f"/api/sequences/{sid}/execute").status_code == 200

    r = set_blocks(client, sid, [hold(b)])
    assert r.status_code == 409
    assert "executing" in r.json()["detail"]

    # Renaming is not structural — the mock allows it mid-run.
    assert client.patch(f"/api/sequences/{sid}", json={"name": "new name"}).status_code == 200


def test_a_running_sequence_cannot_be_deleted(rig):
    client, *_ = rig
    a = make_pose(client, 0.9)
    sid = make_sequence(client)
    set_blocks(client, sid, [hold(a, 30.0)])
    client.post(f"/api/sequences/{sid}/execute")

    assert client.delete(f"/api/sequences/{sid}").status_code == 409

    client.post("/api/execute/stop")
    assert client.delete(f"/api/sequences/{sid}").status_code == 204


def test_a_finished_run_releases_the_sequence(rig):
    client, controller, arm, clock = rig
    a = make_pose(client, 0.2)
    sid = make_sequence(client)
    set_blocks(client, sid, [hold(a, 0.2)])
    client.post(f"/api/sequences/{sid}/execute")
    run_loop(controller, arm, clock)

    assert set_blocks(client, sid, [hold(a, 0.4)]).status_code == 200


# ── marker position validation ───────────────────────────────────────────────


def test_patch_rejects_marker_beyond_hold_duration(client: TestClient):
    """A marker pinned past its hold's end fires late or never — the editor
    clamps `at`, but the endpoint does not trust the editor."""
    pose = make_pose(client, 0.3)
    sid = make_sequence(client)
    r = set_blocks(client, sid, [hold(pose, duration_s=1.0, markers=[shutter(at=1.5)])])
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "marker_out_of_range"


def test_patch_rejects_marker_beyond_transition_proportion(client: TestClient):
    a = make_pose(client, 0.3)
    b = make_pose(client, -0.3, name="侧面")
    sid = make_sequence(client)
    r = set_blocks(client, sid, [
        hold(a, 1.0),
        {"type": "transition", "duration_s": 2.0, "easing": "linear",
         "markers": [{"kind": "wait", "params": {}, "at": 1.5, "estimate_s": 0.0}]},
        hold(b, 1.0),
    ])
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "marker_out_of_range"


def test_patch_accepts_marker_at_block_boundary(client: TestClient):
    pose = make_pose(client, 0.3)
    sid = make_sequence(client)
    r = set_blocks(client, sid, [hold(pose, duration_s=1.0, markers=[shutter(at=1.0)])])
    assert r.status_code == 200, r.text
