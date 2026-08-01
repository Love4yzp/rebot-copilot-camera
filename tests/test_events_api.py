"""The semantic event stream, and who asked for a move.

Two things an integration needs and a screen does not: being told what happened
without re-deriving it from a position stream, and being able to say afterwards
which of several possible triggers moved the arm.
"""

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.actions import ActionRegistry, InlineRunner, ShutterProvider
from backend.app import app
from backend.arm import SimArm
from backend.core import Broadcaster, Controller, events
from backend.routines import RoutineStore
from backend.safety import LatchSource, SafetyLatch
from backend.shutter import SimShutter, ShutterTimeout

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
    shutter = SimShutter()
    runner = InlineRunner()

    app.state.latch = SafetyLatch(clock=clock)
    app.state.routine_store = RoutineStore(tmp_path / "routines")
    app.state.broadcaster = Broadcaster()
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

    seen: list[dict] = []
    app.state.broadcaster.publish = _capture(app.state.broadcaster, seen)
    return TestClient(app), app.state.controller, arm, clock, seen, shutter


def _capture(broadcaster, seen):
    original = broadcaster.publish

    def publish(message):
        if isinstance(message, dict) and message.get("type") == events.TOPIC:
            seen.append(message["data"])
        original(message)

    return publish


def names(seen) -> list[str]:
    return [e["event"] for e in seen]


def anchor(client: TestClient, actions=(), note="正面") -> str:
    rid = client.post("/api/routines", json={"name": "events"}).json()["id"]
    client.post(
        f"/api/routines/{rid}/waypoints",
        json={
            "joints": {"joint1": 0.2, "joint2": 0.0},
            "settle_ms": 0,
            "note": note,
            "actions": list(actions),
        },
    )
    return rid


def run(controller, arm, clock, steps: int = 5000) -> None:
    for _ in range(steps):
        clock.now += 0.01
        arm.step(0.01)
        controller.tick()
        if not controller.is_playing:
            return


# ── what gets reported ───────────────────────────────────────────────────────


def test_a_run_reports_arrival_and_each_action(rig):
    client, controller, arm, clock, seen, _ = rig
    rid = anchor(client, [{"type": "shutter", "count": 2, "interval_s": 0.0}])
    client.post(f"/api/routines/{rid}/waypoints/0/goto")
    run(controller, arm, clock)

    # Only the host's own events; a provider is free to interleave its own
    # (the shutter reports shutter.fired), which is the point of ctx.emit.
    host = [n for n in names(seen) if n.startswith(("routine.", "anchor.", "action."))]
    assert host == [
        events.ROUTINE_STARTED,
        events.ANCHOR_ARRIVED,
        events.ACTION_STARTED,
        events.ACTION_DONE,
        events.ACTION_STARTED,
        events.ACTION_DONE,
        events.ROUTINE_DONE,
    ]

    arrived = seen[1]
    assert arrived["data"]["anchor"] == "正面", "the operator's name for it, not an index"
    assert [e["data"]["frame"] for e in seen if e["event"] == events.ACTION_DONE] == [1, 2]


def test_a_failed_action_is_reported_with_what_went_wrong(rig):
    client, controller, arm, clock, seen, shutter = rig
    rid = anchor(client, [{"type": "shutter"}])
    shutter.script([ShutterTimeout("camera asleep")])
    client.post(f"/api/routines/{rid}/waypoints/0/goto")
    run(controller, arm, clock)

    failed = next(e for e in seen if e["event"] == events.ACTION_FAILED)
    assert failed["data"]["provider"] == "shutter"
    assert "camera asleep" in failed["data"]["error"]
    assert failed["data"]["kind"] == "ShutterTimeout"
    assert events.ROUTINE_ABORTED in names(seen)


def test_the_stop_is_reported_on_the_transition_not_every_tick(rig):
    """The loop sees the latch every tick at up to 500 Hz. A subscriber wants
    the edge, not the level."""
    client, controller, arm, clock, seen, _ = rig
    client.post("/api/estop", json={"reason": "hand on the button"})
    run(controller, arm, clock, steps=50)

    engaged = [e for e in seen if e["event"] == events.ESTOP_ENGAGED]
    assert len(engaged) == 1
    assert engaged[0]["data"]["reason"] == "hand on the button"
    assert engaged[0]["data"]["source"] == LatchSource.API.value

    client.post("/api/estop/clear")
    run(controller, arm, clock, steps=50)
    assert len([e for e in seen if e["event"] == events.ESTOP_CLEARED]) == 1


def test_capturing_a_pose_by_hand_is_reported(rig):
    """Teaching happens over HTTP, so the control loop cannot see it."""
    client, *_, seen, _ = rig
    rid = client.post("/api/routines", json={"name": "events"}).json()["id"]
    client.post(f"/api/routines/{rid}/waypoints/capture", json={"note": "侧面"})

    captured = next(e for e in seen if e["event"] == events.TEACH_CAPTURED)
    assert captured["data"]["anchor"] == "侧面"


def test_a_provider_can_report_its_own_facts(rig):
    """ctx.emit is how a plugin answers "tell me when you did the thing"
    without the host having to know what the thing was."""
    client, controller, arm, clock, seen, _ = rig
    rid = anchor(client, [{"type": "shutter"}])
    client.post(f"/api/routines/{rid}/waypoints/0/goto")
    run(controller, arm, clock)

    assert "shutter.fired" in names(seen)


# ── the socket ───────────────────────────────────────────────────────────────


def test_the_event_socket_carries_events_and_not_the_position_stream(rig):
    """A subscriber that only wants "a frame was taken" should not have to eat
    20 Hz of joint angles over a studio LAN to find it."""
    client, controller, *_ = rig

    with client.websocket_connect("/api/events") as socket:
        controller.emit_event(events.ANCHOR_ARRIVED, {"anchor": "正面"})
        controller.broadcaster.publish({"type": "state", "data": {"positions": {}}})
        controller.emit_event(events.ROUTINE_DONE, {"routine_id": "r"})

        first = socket.receive_json()
        second = socket.receive_json()

    assert first["event"] == events.ANCHOR_ARRIVED
    assert first["data"] == {"anchor": "正面"}
    assert "type" not in first, "the envelope is noise when every message is an event"
    assert second["event"] == events.ROUTINE_DONE


def test_the_state_socket_does_not_carry_events(rig):
    """/ws is what a browser reads. Adding traffic it has no consumer for is
    bandwidth spent on a device that is often on a tablet over wifi."""
    client, controller, *_ = rig

    with client.websocket_connect("/ws") as socket:
        controller.emit_event(events.ANCHOR_ARRIVED, {"anchor": "正面"})
        controller.broadcaster.publish({"type": "state", "data": {"positions": {}}})

        message = socket.receive_json()

    assert message["type"] == "state"


def test_a_slow_subscriber_loses_messages_rather_than_stalling_the_loop():
    """A control loop that stops because something stopped reading is a control
    loop that stops holding the arm up."""

    async def scenario():
        broadcaster = Broadcaster()
        sub = broadcaster.subscribe(asyncio.get_running_loop(), topics={events.TOPIC})
        for i in range(200):
            broadcaster.publish({"type": events.TOPIC, "data": {"event": "x", "n": i}})
        await asyncio.sleep(0)
        return sub

    sub = asyncio.run(scenario())
    assert sub.dropped > 0
    assert sub.queue.qsize() <= 8


# ── who asked ────────────────────────────────────────────────────────────────


def test_a_trigger_says_who_it_was(rig):
    """On a machine several things can trigger — a card, an agent, a foot
    switch, a script — "why did the arm move" is the first question asked."""
    client, controller, *_ = rig
    rid = anchor(client)

    r = client.post(f"/api/routines/{rid}/waypoints/0/goto", json={"source": "footswitch"})

    assert r.status_code == 200
    assert r.json()["source"] == "footswitch"
    assert controller.playback_source == "footswitch"


def test_the_source_defaults_to_the_ui_and_grants_nothing(rig):
    client, controller, *_ = rig
    rid = anchor(client)
    client.post("/api/estop", json={"reason": "stop"})

    r = client.post(f"/api/routines/{rid}/waypoints/0/goto", json={"source": "footswitch"})
    assert r.status_code == 409, "a source is a label, not a permission"

    client.post("/api/estop/clear")
    assert client.post(f"/api/routines/{rid}/play").json()["source"] == "ui"
