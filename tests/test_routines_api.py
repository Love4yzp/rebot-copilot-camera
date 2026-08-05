from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.routines import RoutineStore


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    app.state.routine_store = RoutineStore(tmp_path / "routines")
    return TestClient(app)


@pytest.fixture
def rid(client: TestClient) -> str:
    return client.post("/api/routines", json={"name": "multi-angle"}).json()["id"]


def add(client: TestClient, rid: str, j1: float, **kwargs) -> dict:
    body = {"joints": {"joint1": j1}, **kwargs}
    r = client.post(f"/api/routines/{rid}/waypoints", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def angles(routine: dict) -> list[float]:
    return [w["joints"]["joint1"] for w in routine["waypoints"]]


# ── routines ─────────────────────────────────────────────────────────────────


def test_create_list_get_rename_delete(client: TestClient):
    created = client.post("/api/routines", json={"name": "first"})
    assert created.status_code == 201
    rid = created.json()["id"]

    assert [s["name"] for s in client.get("/api/routines").json()] == ["first"]
    assert client.get(f"/api/routines/{rid}").json()["name"] == "first"

    renamed = client.patch(f"/api/routines/{rid}", json={"name": "renamed"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "renamed"

    assert client.delete(f"/api/routines/{rid}").status_code == 204
    assert client.get("/api/routines").json() == []


def test_unknown_routine_is_404(client: TestClient):
    assert client.get("/api/routines/nope").status_code == 404
    assert client.delete("/api/routines/nope").status_code == 404
    assert client.patch("/api/routines/nope", json={"name": "x"}).status_code == 404


def test_empty_name_is_rejected(client: TestClient):
    assert client.post("/api/routines", json={"name": ""}).status_code == 422


def test_created_routine_survives_a_restart(client: TestClient, tmp_path: Path):
    """Routines are the operator's work; a process restart must not lose them."""
    rid = client.post("/api/routines", json={"name": "durable"}).json()["id"]
    add(client, rid, 0.5, settle_ms=900)

    app.state.routine_store = RoutineStore(tmp_path / "routines")
    reloaded = TestClient(app).get(f"/api/routines/{rid}").json()

    assert reloaded["name"] == "durable"
    assert reloaded["waypoints"][0]["settle_ms"] == 900


# ── waypoints ────────────────────────────────────────────────────────────────


def test_waypoints_append_in_capture_order(client: TestClient, rid: str):
    for q in (0.1, 0.2, 0.3):
        add(client, rid, q)
    assert angles(client.get(f"/api/routines/{rid}").json()) == [0.1, 0.2, 0.3]


def test_waypoint_can_be_inserted_at_an_index(client: TestClient, rid: str):
    add(client, rid, 0.1)
    add(client, rid, 0.3)
    routine = add(client, rid, 0.2, index=1)
    assert angles(routine) == [0.1, 0.2, 0.3]


def test_insert_past_the_end_is_404(client: TestClient, rid: str):
    add(client, rid, 0.1)
    r = client.post(f"/api/routines/{rid}/waypoints", json={"joints": {"joint1": 0.0}, "index": 9})
    assert r.status_code == 404


def test_deleting_a_middle_waypoint_leaves_the_rest_in_order(client: TestClient, rid: str):
    for q in (0.1, 0.2, 0.3):
        add(client, rid, q)

    routine = client.delete(f"/api/routines/{rid}/waypoints/1").json()
    assert angles(routine) == [0.1, 0.3]


def test_waypoint_index_out_of_range_is_404(client: TestClient, rid: str):
    add(client, rid, 0.1)
    assert client.delete(f"/api/routines/{rid}/waypoints/5").status_code == 404
    assert client.patch(f"/api/routines/{rid}/waypoints/5", json={"note": "x"}).status_code == 404
    assert client.delete(f"/api/routines/{rid}/waypoints/-1").status_code == 404


def test_update_changes_only_what_was_sent(client: TestClient, rid: str):
    add(client, rid, 0.1, settle_ms=300, note="original")

    routine = client.patch(f"/api/routines/{rid}/waypoints/0", json={"settle_ms": 1200}).json()
    waypoint = routine["waypoints"][0]

    assert waypoint["settle_ms"] == 1200
    assert waypoint["note"] == "original"
    assert waypoint["joints"] == {"joint1": 0.1}


def test_update_still_validates(client: TestClient, rid: str):
    """model_copy skips validators, so an invalid patch could otherwise be
    written straight to disk."""
    add(client, rid, 0.1)
    r = client.patch(f"/api/routines/{rid}/waypoints/0", json={"settle_ms": -5})
    assert r.status_code == 422
    assert client.get(f"/api/routines/{rid}").json()["waypoints"][0]["settle_ms"] == 300


def test_actions_are_replaced_wholesale_and_keep_their_types(client: TestClient, rid: str):
    add(client, rid, 0.1)
    routine = client.patch(
        f"/api/routines/{rid}/waypoints/0",
        json={"actions": [{"type": "sleep", "duration_s": 2}, {"type": "shutter"}]},
    ).json()

    actions = routine["waypoints"][0]["actions"]
    assert [a["type"] for a in actions] == ["sleep", "shutter"]
    assert actions[1]["on_failure"] == "abort"


def test_unknown_action_type_is_rejected(client: TestClient, rid: str):
    add(client, rid, 0.1)
    r = client.patch(f"/api/routines/{rid}/waypoints/0", json={"actions": [{"type": "nope"}]})
    assert r.status_code == 422


def test_reorder_permutes(client: TestClient, rid: str):
    for q in (0.1, 0.2, 0.3):
        add(client, rid, q)

    routine = client.post(f"/api/routines/{rid}/waypoints/reorder", json={"order": [2, 0, 1]})
    assert angles(routine.json()) == [0.3, 0.1, 0.2]


@pytest.mark.parametrize("order", [[0, 0, 1], [0, 1], [0, 1, 2, 3], [0, 1, 5]])
def test_reorder_rejects_anything_that_is_not_a_permutation(client, rid: str, order: list[int]):
    """A non-permutation silently drops or duplicates a waypoint, and the
    operator finds out mid-shoot."""
    for q in (0.1, 0.2, 0.3):
        add(client, rid, q)

    r = client.post(f"/api/routines/{rid}/waypoints/reorder", json={"order": order})
    assert r.status_code == 400
    assert angles(client.get(f"/api/routines/{rid}").json()) == [0.1, 0.2, 0.3]


def test_editing_is_allowed_while_the_stop_is_engaged(client: TestClient, rid: str):
    """Often exactly what the operator is doing *because* the arm is stopped."""
    client.post("/api/estop", json={"reason": "stop"})
    try:
        add(client, rid, 0.4)
        assert client.patch(f"/api/routines/{rid}", json={"name": "renamed"}).status_code == 200
    finally:
        client.post("/api/estop/clear")


def test_summary_reports_waypoint_and_action_counts(client: TestClient, rid: str):
    add(client, rid, 0.1, actions=[{"type": "shutter"}])
    add(client, rid, 0.2)

    summary = client.get("/api/routines").json()[0]
    assert summary["waypoint_count"] == 2
    assert summary["action_count"] == 1


# ── safety validation ────────────────────────────────────────────────────────


def test_out_of_range_waypoint_is_rejected_with_the_joint_name(client: TestClient, rid: str):
    r = client.post(f"/api/routines/{rid}/waypoints", json={"joints": {"joint1": 9.0}})
    assert r.status_code == 400

    detail = r.json()["detail"]
    assert detail["error"] == "unsafe_pose"
    assert any("joint1" in reason for reason in detail["reasons"])


def test_self_colliding_waypoint_is_rejected(client: TestClient, rid: str):
    """link3 folded back into the base — legal per joint, illegal as a pose."""
    folded = {
        "joint1": 2.394,
        "joint2": 3.039,
        "joint3": 0.046,
        "joint4": 1.142,
        "joint5": 1.511,
        "joint6": 2.871,
    }
    r = client.post(f"/api/routines/{rid}/waypoints", json={"joints": folded})
    assert r.status_code == 400
    assert any("collides" in reason for reason in r.json()["detail"]["reasons"])


def test_patching_a_waypoint_out_of_range_is_rejected(client: TestClient, rid: str):
    add(client, rid, 0.1)
    r = client.patch(f"/api/routines/{rid}/waypoints/0", json={"joints": {"joint1": 9.0}})
    assert r.status_code == 400
    assert angles(client.get(f"/api/routines/{rid}").json()) == [0.1]


def test_rest_pose_is_accepted_despite_sitting_on_joint2s_lower_bound(client, rid: str):
    """joint2's lower limit is exactly 0.0 and the arm rests at 0. Without
    tolerance the arm would be rejected for standing still."""
    r = client.post(
        f"/api/routines/{rid}/waypoints",
        json={"joints": {f"joint{i}": 0.0 for i in range(1, 7)}},
    )
    assert r.status_code == 201
