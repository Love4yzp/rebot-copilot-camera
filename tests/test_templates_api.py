"""Templates: structural recipes with pose slots, and instantiation."""

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
        actions=runner,
    )
    return TestClient(app), app.state.controller, arm, clock


@pytest.fixture
def client(rig) -> TestClient:
    return rig[0]


def make_pose(client: TestClient, j1: float, name: str) -> str:
    r = client.post("/api/poses", json={"name": name, "joints": {"joint1": j1, "joint2": 0.0}})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def make_two_station_sequence(client: TestClient) -> tuple[str, str, str]:
    a = make_pose(client, 0.2, "正面")
    b = make_pose(client, 0.5, "侧面")
    sid = client.post("/api/sequences", json={"name": "两站位"}).json()["id"]
    r = client.patch(f"/api/sequences/{sid}", json={"blocks": [
        {"type": "hold", "pose_id": a, "duration_s": 3.0, "markers": [
            {"kind": "shutter", "params": {"count": 1, "interval_s": 0.0, "focus_first": True},
             "at": 2.0, "estimate_s": 0.3}]},
        {"type": "transition", "duration_s": 4.0, "easing": "linear", "markers": []},
        {"type": "hold", "pose_id": b, "duration_s": 5.0, "markers": []},
    ]})
    assert r.status_code == 200, r.text
    return sid, a, b


def test_a_sequence_becomes_a_slot_recipe(client: TestClient):
    sid, _, _ = make_two_station_sequence(client)

    r = client.post("/api/templates", json={"sequence_id": sid})
    assert r.status_code == 201
    template = r.json()
    assert template["name"] == "两站位", "defaults to the sequence's name"
    assert template["station_count"] == 2
    assert set(template) == {"id", "name", "created_at", "station_count", "recipe"}

    recipe = template["recipe"]
    assert [b["type"] for b in recipe] == ["hold", "transition", "hold"]
    assert recipe[0]["pose_id"] == "slot:1"
    assert recipe[2]["pose_id"] == "slot:2"
    # Structure kept: durations, easing, markers — but no joint angles anywhere.
    assert recipe[0]["duration_s"] == 3.0
    assert recipe[1]["easing"] == "linear"
    assert recipe[0]["markers"][0]["kind"] == "shutter"
    assert "joints" not in str(recipe)


def test_an_explicit_name_wins(client: TestClient):
    sid, _, _ = make_two_station_sequence(client)
    r = client.post("/api/templates", json={"sequence_id": sid, "name": "四方位"})
    assert r.json()["name"] == "四方位"


def test_a_sequence_with_no_stations_cannot_be_a_template(client: TestClient):
    sid = client.post("/api/sequences", json={"name": "empty"}).json()["id"]
    r = client.post("/api/templates", json={"sequence_id": sid})
    assert r.status_code == 400


def test_template_from_an_unknown_sequence_is_404(client: TestClient):
    assert client.post("/api/templates", json={"sequence_id": "nope"}).status_code == 404


def test_list_and_delete(client: TestClient):
    sid, _, _ = make_two_station_sequence(client)
    tid = client.post("/api/templates", json={"sequence_id": sid}).json()["id"]

    assert [t["id"] for t in client.get("/api/templates").json()] == [tid]
    assert client.delete(f"/api/templates/{tid}").status_code == 204
    assert client.get("/api/templates").json() == []
    assert client.delete(f"/api/templates/{tid}").status_code == 404


def test_instantiate_binds_each_slot_to_a_library_pose(client: TestClient):
    sid, _, _ = make_two_station_sequence(client)
    tid = client.post("/api/templates", json={"sequence_id": sid}).json()["id"]
    x = make_pose(client, 0.1, "新正面")
    y = make_pose(client, 0.6, "新侧面")

    r = client.post(f"/api/templates/{tid}/instantiate",
                    json={"name": "新一论", "pose_ids": [x, y]})
    assert r.status_code == 201
    sequence = r.json()
    assert sequence["schema_version"] == 2
    blocks = sequence["blocks"]
    assert [b["type"] for b in blocks] == ["hold", "transition", "hold"]
    assert blocks[0]["pose_id"] == x
    assert blocks[2]["pose_id"] == y
    assert blocks[0]["markers"][0]["kind"] == "shutter", "the marker recipe came along"


def test_instantiate_copies_are_detached(client: TestClient):
    """New ids everywhere: editing the instance must never edit the recipe."""
    sid, _, _ = make_two_station_sequence(client)
    tid = client.post("/api/templates", json={"sequence_id": sid}).json()["id"]
    x = make_pose(client, 0.1, "x")
    y = make_pose(client, 0.6, "y")
    sequence = client.post(f"/api/templates/{tid}/instantiate",
                           json={"name": "copy", "pose_ids": [x, y]}).json()

    recipe = client.get("/api/templates").json()[0]["recipe"]
    recipe_ids = {b["id"] for b in recipe} | {m["id"] for b in recipe for m in b["markers"]}
    instance_ids = {b["id"] for b in sequence["blocks"]} | {
        m["id"] for b in sequence["blocks"] for m in b["markers"]}
    assert recipe_ids.isdisjoint(instance_ids)


def test_instantiate_with_the_same_pose_twice_drops_the_transition(client: TestClient):
    """Same pose adjacent is "stop halfway and take one more frame", not a move."""
    sid, _, _ = make_two_station_sequence(client)
    tid = client.post("/api/templates", json={"sequence_id": sid}).json()["id"]
    x = make_pose(client, 0.1, "x")

    sequence = client.post(f"/api/templates/{tid}/instantiate",
                           json={"name": "copy", "pose_ids": [x, x]}).json()
    assert [b["type"] for b in sequence["blocks"]] == ["hold", "hold"]


def test_instantiate_validates_the_pose_list(client: TestClient):
    sid, _, _ = make_two_station_sequence(client)
    tid = client.post("/api/templates", json={"sequence_id": sid}).json()["id"]
    x = make_pose(client, 0.1, "x")

    r = client.post(f"/api/templates/{tid}/instantiate", json={"name": "c", "pose_ids": [x]})
    assert r.status_code == 400
    assert "2 poses" in r.json()["detail"]

    r = client.post(f"/api/templates/{tid}/instantiate",
                    json={"name": "c", "pose_ids": [x, "ghost"]})
    assert r.status_code == 400
    assert "ghost" in r.json()["detail"]

    assert client.post(f"/api/templates/{tid}/instantiate",
                       json={"name": " ", "pose_ids": [x, x]}).status_code == 400
    assert client.post("/api/templates/nope/instantiate",
                       json={"name": "c", "pose_ids": [x, x]}).status_code == 404
