"""Control state, execution control, teaching, the websocket, shutter endpoints."""

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


def wire(tmp_path: Path, joints=JOINTS, self_driven: bool = False):
    clock = FakeClock()
    arm = SimArm(joints, clock=clock, tau=0.05, self_driven=self_driven)
    arm.connect()

    app.state.latch = SafetyLatch(clock=clock)
    app.state.pose_store = PoseStore(tmp_path / "poses")
    app.state.sequence_store = SequenceStore(tmp_path / "sequences")
    app.state.template_store = TemplateStore(tmp_path / "templates")
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
def rig(tmp_path: Path):
    return wire(tmp_path)


@pytest.fixture
def client(rig) -> TestClient:
    return rig[0]


def run_loop(controller, arm, clock, max_steps: int = 5000) -> None:
    """Drive the control loop by hand until the executor finishes."""
    for _ in range(max_steps):
        clock.now += 0.01
        arm.step(0.01)
        controller.tick()
        if not controller.is_playing:
            break


def make_playing_sequence(client: TestClient, hold_s: float = 30.0) -> str:
    """A sequence with one long hold, executing. Returns its id."""
    pose = client.post("/api/poses", json={
        "name": "p", "joints": {"joint1": 0.2, "joint2": 0.0}}).json()["id"]
    sid = client.post("/api/sequences", json={"name": "shoot"}).json()["id"]
    client.patch(f"/api/sequences/{sid}", json={"blocks": [
        {"type": "hold", "pose_id": pose, "duration_s": hold_s, "markers": []}]})
    assert client.post(f"/api/sequences/{sid}/execute").status_code == 200
    return sid


# ── execution control ────────────────────────────────────────────────────────


def test_stop_returns_to_idle_and_clears_the_progress(rig):
    client, controller, arm, clock = rig
    make_playing_sequence(client)

    r = client.post("/api/execute/stop")
    assert r.status_code == 200
    assert r.json()["mode"] == "idle"
    assert r.json()["playback"] is None, "an explicit stop clears the progress"
    assert not controller.is_playing


def test_stop_works_while_the_estop_is_engaged(client: TestClient):
    """Stopping must never be blocked by the thing that stopped you."""
    make_playing_sequence(client)
    client.post("/api/estop", json={"reason": "stop"})

    assert client.post("/api/execute/stop").status_code == 200


def test_resume_continues_past_a_wait_marker(rig):
    client, controller, arm, clock = rig
    pose = client.post("/api/poses", json={
        "name": "p", "joints": {"joint1": 0.2, "joint2": 0.0}}).json()["id"]
    sid = client.post("/api/sequences", json={"name": "waits"}).json()["id"]
    client.patch(f"/api/sequences/{sid}", json={"blocks": [
        {"type": "hold", "pose_id": pose, "duration_s": 5.0, "markers": [
            {"kind": "wait", "params": {}, "at": 1.0, "estimate_s": 0.0}]}]})
    client.post(f"/api/sequences/{sid}/execute")

    for _ in range(500):
        clock.now += 0.01
        arm.step(0.01)
        controller.tick()
        if controller.executor and controller.executor.is_waiting:
            break

    state = client.get("/api/control").json()
    assert state["playback"]["phase"] == "wait"
    assert state["playback"]["t_in_block"] == pytest.approx(1.0, abs=0.05)

    r = client.post("/api/execute/resume")
    assert r.status_code == 200
    assert r.json()["playback"]["phase"] == "hold"

    run_loop(controller, arm, clock)
    assert controller.executor.phase.value == "done"


def test_resume_without_a_wait_is_409(client: TestClient):
    r = client.post("/api/execute/resume")
    assert r.status_code == 409
    assert "no wait marker" in r.json()["detail"]


def test_resume_is_gated_during_the_stop(rig):
    """Resuming is motion; a stop engaged during the wait already aborted."""
    client, *_ = rig
    make_playing_sequence(client)
    client.post("/api/estop", json={"reason": "stop"})

    r = client.post("/api/execute/resume")
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "estop_latched"


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


def test_teach_is_409_while_a_sequence_is_executing(client: TestClient):
    make_playing_sequence(client)
    assert client.post("/api/teach", json={"enabled": True}).status_code == 409


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


def test_websocket_streams_seq_playback_progress(rig):
    """The progress message is the SeqPlayback shape the frontend clamps."""
    client, controller, arm, clock = rig
    pose = client.post("/api/poses", json={
        "name": "p", "joints": {"joint1": 0.3, "joint2": 0.0}}).json()["id"]
    sid = client.post("/api/sequences", json={"name": "ws"}).json()["id"]
    client.patch(f"/api/sequences/{sid}", json={"blocks": [
        {"type": "hold", "pose_id": pose, "duration_s": 0.3, "markers": []}]})

    with client.websocket_connect("/ws") as ws:
        client.post(f"/api/sequences/{sid}/execute")
        run_loop(controller, arm, clock)
        controller.tick()

        seen_playback = None
        for _ in range(100):
            message = ws.receive_json()
            if message["type"] == "state" and message["data"]["playback"]:
                seen_playback = message["data"]["playback"]
            if seen_playback and seen_playback["phase"] == "done":
                break

    assert seen_playback is not None
    assert set(seen_playback) == {
        "sequence_id", "sequence_name", "block_index", "block_total",
        "phase", "t_in_block", "error", "finished",
    }
    assert seen_playback["phase"] == "done"
    assert seen_playback["block_index"] == seen_playback["block_total"]


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
        # The sim carries a banner like the real board's VERSION line, so the
        # endpoint's response keeps the same shape on both.
        "firmware_version": "sim-1.0.0",
        "error": None,
    }
    assert controller.shutter.pings == 1
    assert controller.shutter.shots == 0


def test_a_reachable_board_with_no_camera_paired_is_not_a_pass(rig):
    """The gap this endpoint used to have. `ping` answers for the USB cable and
    the firmware deliberately does not touch the camera for it, so a board that
    answers perfectly says nothing about whether a frame will be taken. Reported
    green, the first anyone would hear of an unpaired camera is a sequence
    failing at the first station with the subject already in place.

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


def test_pairing_is_refused_while_a_sequence_is_executing(rig):
    """The driver takes one command at a time, so a thirty-second pairing scan
    would stall the frames queued behind it."""
    client, controller, _, _ = rig
    make_playing_sequence(client)
    assert controller.is_playing

    r = client.post("/api/shutter/pair")

    assert r.status_code == 409
    assert "executing" in r.json()["detail"]
    assert controller.shutter.pairs == 0


def test_smart_pairing_attaches_the_camera(rig):
    """Smartphone-mode pairing: the camera is in "connect to smartphone" mode
    and the user confirms on its screen. Same endpoint contract as pair()."""
    client, controller, _, _ = rig
    controller.shutter.set_camera_connected(False)

    body = client.post("/api/shutter/pair_smart").json()

    assert body["ok"] is True
    assert body["camera"] is True
    assert controller.shutter.smart_pairs == 1


def test_smart_pairing_is_refused_while_a_sequence_is_executing(rig):
    """A 75-second smart-pairing scan would stall the frames queued behind it,
    same as the ordinary pair."""
    client, controller, _, _ = rig
    make_playing_sequence(client)
    assert controller.is_playing

    r = client.post("/api/shutter/pair_smart")

    assert r.status_code == 409
    assert "executing" in r.json()["detail"]
    assert controller.shutter.smart_pairs == 0


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
    goto ended in "not reached". This drives nothing but the control loop,
    exactly as the service does.
    """
    client, controller, arm, clock = wire(tmp_path, self_driven=True)

    pid = client.post("/api/poses", json={
        "name": "sim", "joints": {"joint1": 0.3, "joint2": 0.0}}).json()["id"]
    assert client.post(f"/api/poses/{pid}/goto").status_code == 200

    for _ in range(5000):
        clock.now += 0.01
        controller.tick()   # no arm.step() — that is the point
        if not controller.is_playing:
            break

    assert controller.executor.phase.value == "done"
    assert arm.read_state().positions["joint1"] == pytest.approx(0.3, abs=0.02)
