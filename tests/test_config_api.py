"""Tuning endpoints: shapes, hot-apply, persistence, and the safety gates.

The gates are the point of the suite: a tuning write that the arm state
forbids must come back 409 with the reason, not slip through.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import assets
from backend.app import app
from backend.arm import SimArm
from backend.core import Broadcaster, Controller
from backend.safety import SafetyLatch
from backend.shutter import SimShutter
from backend.tuning import TuningStore


@pytest.fixture
def rig(tmp_path: Path):
    arm = SimArm(assets.joint_names(), clock=lambda: 0.0)
    arm.connect()
    app.state.latch = SafetyLatch()
    app.state.broadcaster = Broadcaster()
    app.state.tuning_store = TuningStore(tmp_path / "tuning.yaml")
    app.state.controller = Controller(
        arm=arm,
        shutter=SimShutter(),
        latch=app.state.latch,
        broadcaster=app.state.broadcaster,
        clock=lambda: 0.0,
        tuning=app.state.tuning_store.load(),
    )
    return TestClient(app), arm


def test_get_reports_live_saved_dirty_and_options(rig):
    client, _ = rig
    body = client.get("/api/config/tuning").json()
    assert body["current"]["float"]["kp"] == 2.0
    assert body["saved"] == body["current"]
    assert body["dirty"] == []
    assert body["gripper_motor"] is True
    assert body["payload_options"] == ["gripper"]
    assert body["current"]["payload"]["profile"] == "gripper"


def test_put_float_gains_applies_live_and_marks_dirty(rig):
    client, arm = rig
    r = client.put("/api/config/tuning", json={"float": {"kp": 3.0, "kd": 1.5}})
    assert r.status_code == 200
    body = r.json()
    assert body["current"]["float"] == {"kp": 3.0, "kd": 1.5}
    assert body["dirty"] == ["float"]
    # The change reached the arm driver, not just the controller's bookkeeping.
    assert arm._float_gains == (3.0, 1.5)


def test_put_unknown_section_is_422(rig):
    client, _ = rig
    assert client.put("/api/config/tuning", json={"turbo": {}}).status_code == 422


def test_put_out_of_range_is_422(rig):
    client, _ = rig
    assert client.put("/api/config/tuning", json={"float": {"kp": 99.0}}).status_code == 422


def test_camera_profile_without_a_weighed_mass_is_422(rig):
    client, _ = rig
    r = client.put("/api/config/tuning", json={"payload": {"profile": "camera"}})
    assert r.status_code == 422


def test_save_persists_and_clears_dirty(rig):
    client, _ = rig
    client.put("/api/config/tuning", json={"float": {"kp": 3.0}})
    body = client.post("/api/config/tuning/save").json()
    assert body["dirty"] == []
    assert body["saved"]["float"]["kp"] == 3.0


def test_reset_reloads_the_saved_file(rig):
    client, _ = rig
    client.put("/api/config/tuning", json={"float": {"kp": 3.0}})
    client.post("/api/config/tuning/save")
    client.put("/api/config/tuning", json={"float": {"kp": 9.9}})
    body = client.post("/api/config/tuning/reset").json()
    assert body["current"]["float"]["kp"] == 3.0
    assert body["dirty"] == []


def test_put_is_refused_while_a_sequence_executes(rig, monkeypatch):
    client, _ = rig
    monkeypatch.setattr(
        Controller, "is_playing", property(lambda self: True)
    )
    r = client.put("/api/config/tuning", json={"float": {"kp": 3.0}})
    assert r.status_code == 409
    assert "executing" in r.json()["detail"]


def test_payload_switch_is_refused_while_floating(rig, monkeypatch):
    client, arm = rig
    monkeypatch.setattr(assets, "has_gripper", lambda: False)
    arm.set_float(True)
    r = client.put(
        "/api/config/tuning",
        json={"payload": {"profile": "camera", "camera": {"mass": 0.74}}},
    )
    assert r.status_code == 409
    assert "floating" in r.json()["detail"]


def test_float_gains_may_change_mid_float(rig):
    """The carve-out that makes tuning-by-feel possible: the follow target is
    the arm's own position, so a gain change mid-float causes no torque jump."""
    client, arm = rig
    arm.set_float(True)
    r = client.put("/api/config/tuning", json={"float": {"kp": 4.0}})
    assert r.status_code == 200
    assert arm._float_gains == (4.0, 1.0)


def test_non_gripper_profile_is_refused_when_the_motor_is_wired(rig, monkeypatch):
    """Motor on the bus: the gripper's mass is physically on the arm whatever
    the profile claims, so 'gripper' is the only legal answer."""
    client, _ = rig
    monkeypatch.setattr(assets, "has_gripper", lambda: True)
    r = client.put("/api/config/tuning", json={"payload": {"profile": "bare"}})
    assert r.status_code == 409
    assert "must be 'gripper'" in r.json()["detail"]

    ok = client.put("/api/config/tuning", json={"payload": {"profile": "gripper"}})
    assert ok.status_code == 200


def test_gripper_profile_without_the_motor_is_dead_weight(rig):
    """A mounted-but-unwired gripper is a legal payload: its mass hangs off
    the arm even though no motor is on the bus to answer for it. The profile
    says what mass hangs; the yaml switch says whether the motor is wired."""
    client, _ = rig
    r = client.put("/api/config/tuning", json={"payload": {"profile": "gripper"}})
    assert r.status_code == 200
    assert r.json()["current"]["payload"]["profile"] == "gripper"
