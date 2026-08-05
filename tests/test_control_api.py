"""Motion endpoints and the state websocket, over HTTP."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.actions import ActionRegistry, InlineRunner, ShutterProvider
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
    shutter = SimShutter()
    # Registered through the registry, not into the runner directly, so the
    # two cannot disagree about what is installed.
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


# ── goto ─────────────────────────────────────────────────────────────────────


def run_loop(controller, arm, clock, max_steps: int = 5000) -> None:
    """Drive the control loop by hand until the executor finishes."""
    for _ in range(max_steps):
        clock.now += 0.01
        arm.step(0.01)
        controller.tick()
        if not controller.is_playing:
            break


def test_goto_visits_one_waypoint_fires_its_actions_and_stays(rig):
    """The use-layer atomic operation: tap an anchor — go, settle, act, hold."""
    client, controller, arm, clock = rig
    rid = client.post("/api/routines", json={"name": "four angles"}).json()["id"]
    client.post(f"/api/routines/{rid}/waypoints", json={"joints": {"joint1": 0.4, "joint2": 0.0}})
    client.post(
        f"/api/routines/{rid}/waypoints",
        json={
            "joints": {"joint1": 0.8, "joint2": 0.0},
            "note": "side",
            "actions": [{"type": "shutter", "count": 2, "interval_s": 0.1}],
        },
    )

    r = client.post(f"/api/routines/{rid}/waypoints/1/goto")
    assert r.status_code == 200
    assert r.json()["mode"] == "playback"
    assert r.json()["playback"]["waypoint_total"] == 1

    run_loop(controller, arm, clock)

    assert not controller.is_playing
    assert arm.read_state().positions["joint1"] == pytest.approx(0.8, abs=0.02)
    assert controller.shutter.shots == 2, "the anchor's burst ran on arrival"

    # The stored routine is untouched — the ephemeral one was never persisted.
    stored = client.get(f"/api/routines/{rid}").json()
    assert [w["joints"]["joint1"] for w in stored["waypoints"]] == [0.4, 0.8]


def test_goto_is_409_while_the_stop_is_engaged(client: TestClient):
    rid = make_routine(client, 0.2)
    client.post("/api/estop", json={"reason": "stop"})

    r = client.post(f"/api/routines/{rid}/waypoints/0/goto")
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "estop_latched"


def test_goto_is_409_while_teaching_or_playing(client: TestClient):
    rid = make_routine(client, 0.2, 0.4)

    client.post("/api/teach", json={"enabled": True})
    assert client.post(f"/api/routines/{rid}/waypoints/0/goto").status_code == 409
    client.post("/api/teach", json={"enabled": False})

    client.post(f"/api/routines/{rid}/play")
    assert client.post(f"/api/routines/{rid}/waypoints/0/goto").status_code == 409


def test_goto_on_a_bad_index_or_routine_is_404(client: TestClient):
    rid = make_routine(client, 0.2)
    assert client.post(f"/api/routines/{rid}/waypoints/5/goto").status_code == 404
    assert client.post("/api/routines/nope/waypoints/0/goto").status_code == 404


def test_goto_preflights_the_path_from_the_current_pose(tmp_path: Path):
    """Two legal poses, an illegal line between them — refuse before moving.

    Needs a six-joint arm so the "current pose" half of the path is real, so
    this test builds its own rig rather than using the two-joint fixture.
    """
    clock = FakeClock()
    arm = SimArm(
        ("joint1", "joint2", "joint3", "joint4", "joint5", "joint6"), clock=clock, tau=0.05
    )
    arm.connect()
    app.state.latch = SafetyLatch(clock=clock)
    app.state.routine_store = RoutineStore(tmp_path / "routines")
    app.state.broadcaster = Broadcaster()
    shutter = SimShutter()
    # Registered through the registry, not into the runner directly, so the
    # two cannot disagree about what is installed.
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
    client = TestClient(app)

    here = {
        "joint1": -0.882, "joint2": 3.107, "joint3": 0.686,
        "joint4": -0.132, "joint5": 1.482, "joint6": -3.098,
    }
    there = {
        "joint1": -1.148, "joint2": 2.579, "joint3": 0.301,
        "joint4": 1.345, "joint5": 1.051, "joint6": -2.242,
    }
    rid = client.post("/api/routines", json={"name": "through the base"}).json()["id"]
    assert client.post(f"/api/routines/{rid}/waypoints", json={"joints": there}).status_code == 201
    arm.drag(here)  # from rest, a delta is an absolute pose

    r = client.post(f"/api/routines/{rid}/waypoints/0/goto")
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "unsafe_path"
    assert client.get("/api/control").json()["playing"] is False


# ── teaching ─────────────────────────────────────────────────────────────────


def test_teach_toggles_the_mode(rig):
    """The endpoint switches modes; whether the arm is currently floating is
    the control loop's decision, made from measured motion."""
    client, controller, arm, _ = rig

    assert client.post("/api/teach", json={"enabled": True}).json()["teaching"] is True
    assert controller.mode == "teach"

    assert client.post("/api/teach", json={"enabled": False}).json()["teaching"] is False
    assert controller.mode == "idle"
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
        "camera": True,
        "fired": False,
        "firmware_version": None,
        "error": None,
    }
    assert controller.shutter.pings == 1
    assert controller.shutter.shots == 0


def test_a_reachable_board_with_no_camera_paired_is_not_a_pass(rig):
    """The gap this endpoint used to have. `ping` answers for the USB cable and
    the firmware deliberately does not touch the camera for it, so a board that
    answers perfectly says nothing about whether a frame will be taken. Reported
    green, the first anyone would hear of an unpaired camera is a routine
    failing at the first anchor with the subject already in place.

    With the three-state fix, the endpoint now distinguishes "never paired"
    (needs a human with the menu) from "paired but sleeping" (resolves on the
    next frame). This test covers the former.
    """
    client, controller, _, _ = rig
    controller.shutter.set_paired(False)

    body = client.post("/api/shutter/test").json()

    assert body["connected"] is True, "the board itself is fine"
    assert body["camera"] is False
    assert body["ok"] is False
    assert "no camera is paired" in body["error"]


def test_self_test_reconnects_a_sleeping_camera(rig):
    """A camera that is paired but not connected (sleeping, just booted) should
    resolve itself when the self-test sends a `FOCUS` -- no frame burned, just
    a lazy BLE connect. The endpoint should report green without the operator
    having to know about the distinction."""
    client, controller, _, _ = rig
    controller.shutter.set_camera_connected(False)  # paired, but BLE down

    body = client.post("/api/shutter/test").json()

    assert body["connected"] is True
    assert body["camera"] is True
    assert body["ok"] is True
    assert body["error"] is None
    assert controller.shutter.focuses == 1, "FOCUS was sent to force the BLE connect"


def test_self_test_reports_camera_unreachable(rig):
    """A camera that is paired but unreachable (powered off, out of range) is
    distinct from one that was never paired. The FOCUS itself fails, and the
    endpoint reports red with the right explanation."""
    client, controller, _, _ = rig
    controller.shutter.set_camera_connected(False, unreachable=True)

    body = client.post("/api/shutter/test").json()

    assert body["connected"] is True
    assert body["camera"] is None  # could not be determined
    assert body["ok"] is False
    assert "camera unreachable" in body["error"]


def test_pairing_attaches_the_camera_and_says_so(rig):
    """Without this endpoint the only way to attach a camera was a serial
    terminal — so a board that reset, which drops its pairing, could not be
    recovered from the screen that was reporting the problem."""
    client, controller, _, _ = rig
    controller.shutter.set_camera_connected(False)

    body = client.post("/api/shutter/pair").json()

    assert body["ok"] is True
    assert body["camera"] is True
    assert controller.shutter.pairs == 1
    assert client.post("/api/shutter/test").json()["ok"] is True


def test_pairing_reports_a_camera_that_never_showed_up(rig):
    """The ordinary way this fails: the camera was never put into its own
    pairing mode. A 200 with a reason, like the self-test — someone is standing
    at the machine working a camera menu and needs a sentence, not a stack."""
    client, controller, _, _ = rig
    controller.shutter.set_camera_connected(False, pair_fails=True)

    body = client.post("/api/shutter/pair").json()

    assert body["ok"] is False
    assert body["camera"] is False
    assert "no camera found" in body["error"]


def test_pairing_is_refused_while_a_routine_is_playing(rig):
    """The driver takes one command at a time, so a thirty-second pairing scan
    would stall the frames queued behind it."""
    client, controller, _, _ = rig
    rid = make_routine(client, 0.2, 0.4)
    assert client.post(f"/api/routines/{rid}/play").status_code == 200
    assert controller.is_playing

    r = client.post("/api/shutter/pair")

    assert r.status_code == 409
    assert "playing" in r.json()["detail"]
    assert controller.shutter.pairs == 0


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


def test_the_service_arm_moves_without_anything_stepping_it(tmp_path: Path):
    """The regression that made `--sim` useless.

    Controller.tick() only reads the arm, so a simulator that has to be
    step()ed by hand never moves in a running service: every play and every
    goto ended in "waypoint 0 not reached within 6.0s". This drives nothing but
    the control loop, exactly as the service does.
    """
    clock = FakeClock()
    arm = SimArm(JOINTS, clock=clock, tau=0.05, self_driven=True)
    arm.connect()
    app.state.latch = SafetyLatch(clock=clock)
    app.state.routine_store = RoutineStore(tmp_path / "routines")
    app.state.broadcaster = Broadcaster()
    shutter = SimShutter()
    # Registered through the registry, not into the runner directly, so the
    # two cannot disagree about what is installed.
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
    client = TestClient(app)

    rid = client.post("/api/routines", json={"name": "sim"}).json()["id"]
    client.post(
        f"/api/routines/{rid}/waypoints",
        json={
            "joints": {"joint1": 0.3, "joint2": 0.0},
            "settle_ms": 50,
            "actions": [{"type": "shutter"}],
        },
    )
    assert client.post(f"/api/routines/{rid}/waypoints/0/goto").status_code == 200

    for _ in range(5000):
        clock.now += 0.01
        app.state.controller.tick()   # no arm.step() — that is the point
        if not app.state.controller.is_playing:
            break

    assert app.state.controller.executor.phase.value == "done"
    assert arm.read_state().positions["joint1"] == pytest.approx(0.3, abs=0.02)
    assert shutter.shots == 1
