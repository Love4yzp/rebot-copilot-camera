"""The plugin surface: registry, manifest, and the two pre-flights.

The claims worth pinning down are all about *when* a bad plugin is noticed. The
expensive moment is the ACTING phase — arm at the anchor, subject waiting — so
everything here is about catching it earlier: on write, on play, or at startup.
"""

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from backend.actions import (
    ActionContext,
    ActionRegistry,
    ActionUnavailable,
    FieldSpec,
    InlineRunner,
    ShutterProvider,
    ThreadedRunner,
)
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


class RelayParams(BaseModel):
    channel: int = Field(default=1, ge=1, le=4)


class RelayProvider:
    """A plausible third-party provider: some contact, closed for a moment."""

    id = "relay"
    label = "继电器"
    params_model = RelayParams
    retryable = True

    def __init__(self, healthy: bool = True) -> None:
        self.healthy = healthy
        self.calls: list[RelayParams] = []
        self.probes = 0

    def fields(self):
        return [FieldSpec(key="channel", kind="stepper", label="通道", default=1, min=1, max=4)]

    def probe(self) -> None:
        self.probes += 1
        if not self.healthy:
            raise ActionUnavailable("relay board not answering")

    def run(self, params, ctx) -> None:
        self.calls.append(params)
        ctx.emit("relay.closed", {"channel": params.channel})


class UnrepeatableProvider(RelayProvider):
    """Something whose side effect cannot be taken back — a strobe mid-recycle."""

    id = "strobe"
    label = "闪光"
    retryable = False

    def run(self, params, ctx) -> None:
        self.calls.append(params)
        raise ActionUnavailable("still recycling")


@pytest.fixture
def rig(tmp_path: Path):
    clock = FakeClock()
    arm = SimArm(JOINTS, clock=clock, tau=0.05)
    arm.connect()
    shutter = SimShutter()
    runner = InlineRunner()

    app.state.latch = SafetyLatch(clock=clock)
    app.state.pose_store = PoseStore(tmp_path / "poses")
    app.state.sequence_store = SequenceStore(tmp_path / "sequences")
    app.state.template_store = TemplateStore(tmp_path / "templates")
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
    return TestClient(app), app.state.plugins, app.state.controller, arm, clock


@pytest.fixture
def client(rig) -> TestClient:
    return rig[0]


def make_station(client: TestClient, markers: list[dict]) -> str:
    """A pose plus a one-station sequence with the given markers. Returns sid."""
    pose_id = client.post("/api/poses", json={
        "name": "p", "joints": {"joint1": 0.2, "joint2": 0.0}}).json()["id"]
    sid = client.post("/api/sequences", json={"name": "plugins"}).json()["id"]
    r = client.patch(f"/api/sequences/{sid}", json={"blocks": [
        {"type": "hold", "pose_id": pose_id, "duration_s": 1.0, "markers": markers}]})
    assert r.status_code == 200, r.text
    return sid


# ── the manifest ─────────────────────────────────────────────────────────────


def test_the_manifest_describes_controls_rather_than_drawing_them(client: TestClient):
    """The host owns the widgets, so every provider inherits the touch targets
    and focus behaviour that were settled once."""
    entries = client.get("/api/plugins").json()

    shutter = next(e for e in entries if e["id"] == "shutter")
    assert shutter["available"] is True
    kinds = {f["key"]: f["kind"] for f in shutter["fields"]}
    assert kinds == {"count": "stepper", "interval_s": "tiers", "focus_first": "switch"}

    interval = next(f for f in shutter["fields"] if f["key"] == "interval_s")
    assert interval["when"] == {"key": "count", "min": 2}, "a gap needs two frames to mean anything"


def test_a_provider_that_fails_its_self_test_stays_listed_with_the_reason(rig):
    """Vanishing from the list would read as "I configured it wrong", and send
    the operator looking in the wrong place."""
    client, registry, *_ = rig
    registry.register(RelayProvider(healthy=False))

    relay = next(e for e in client.get("/api/plugins").json() if e["id"] == "relay")
    assert relay["available"] is False
    assert "not answering" in relay["reason"]
    assert relay["label"] == "继电器"


def test_registering_a_provider_does_not_reach_for_its_hardware(rig):
    """Probing pings a board. A side effect hidden inside wiring is one nobody
    can predict, so health is established explicitly, not on registration."""
    _, registry, *_ = rig
    relay = RelayProvider()
    registry.register(relay)

    assert relay.probes == 0
    registry.probe_all()
    assert relay.probes == 1


def test_probe_refreshes_a_provider_that_has_come_back(rig):
    client, registry, *_ = rig
    relay = RelayProvider(healthy=False)
    registry.register(relay)
    assert client.get("/api/plugins").json()

    relay.healthy = True
    entries = client.post("/api/plugins/probe").json()

    assert next(e for e in entries if e["id"] == "relay")["available"] is True


def test_a_plugin_that_will_not_load_never_stops_the_service(rig, monkeypatch):
    """A device missing one accessory is not a missing machine — but a plugin
    that quietly vanished is worse than one that is listed as broken."""
    _, registry, *_ = rig

    class Entry:
        name = "exploder"

        def load(self):
            raise ImportError("no module named 'pyserial_but_misspelled'")

    monkeypatch.setattr("backend.actions.registry.entry_points", lambda group: [Entry()])
    registry.discover()  # must not raise

    entry = next(e for e in registry.manifest() if e["id"] == "exploder")
    assert entry["available"] is False
    assert entry["installed"] is False, "nothing can be configured against it"
    assert "misspelled" in entry["reason"]
    assert entry["fields"] == []
    assert registry.provider("exploder") is None


def test_a_provider_missing_an_attribute_is_refused_rather_than_registered(rig, monkeypatch):
    """The bad plugin used to be the one shape that *did* stop the service: the
    host read ``provider.id`` outside its own guard, so a misspelled attribute
    left an AttributeError coming out of discovery and the process never came
    up. A device missing one accessory is not a missing machine."""
    _, registry, *_ = rig

    class Shapeless:
        """Everything a provider needs except the one line the author forgot."""

        label = "无名"
        params_model = RelayParams

        def fields(self):
            return []

        def probe(self) -> None: ...

        def run(self, params, ctx) -> None: ...

    class Entry:
        name = "shapeless"

        def load(self):
            return Shapeless

    monkeypatch.setattr("backend.actions.registry.entry_points", lambda group: [Entry()])
    registry.discover()  # must not raise

    entry = next(e for e in registry.manifest() if e["id"] == "shapeless")
    assert entry["installed"] is False
    assert "id" in entry["reason"]
    assert registry.provider("shapeless") is None
    # And the accessory that was already working is untouched.
    assert registry.ensure_status("shutter").available is True


def test_a_plugin_cannot_take_over_an_id_that_is_already_registered(rig, monkeypatch):
    """The most expensive failure this layer can have. Every ShutterAction is
    dispatched to the literal id ``shutter``, so a plugin claiming that id would
    quietly become the camera: the arm walks the whole set, nothing raises, and
    the frames are somewhere else — or nowhere."""
    client, registry, *_ = rig
    real_shutter = registry.provider("shutter")

    class Impostor(RelayProvider):
        id = "shutter"
        label = "不是快门"

    class Entry:
        name = "impostor"

        def load(self):
            return Impostor

    monkeypatch.setattr("backend.actions.registry.entry_points", lambda group: [Entry()])
    registry.discover()

    assert registry.provider("shutter") is real_shutter, "the built-in still owns its id"
    impostor = next(e for e in client.get("/api/plugins").json() if e["id"] == "impostor")
    assert impostor["installed"] is False
    assert "already registered" in impostor["reason"], "refused loudly, never silently"


def test_a_provider_that_cannot_describe_its_form_costs_only_itself(rig):
    """The manifest carries every provider, so an exception escaping one
    ``fields()`` answered GET /api/plugins with a 500 and took every other
    accessory off the edit sheet with it."""
    client, registry, *_ = rig

    class Formless(RelayProvider):
        id = "formless"

        def fields(self):
            raise RuntimeError("cannot build my controls")

    registry.register(Formless())

    entries = client.get("/api/plugins").json()

    assert next(e for e in entries if e["id"] == "shutter")["available"] is True
    formless = next(e for e in entries if e["id"] == "formless")
    assert formless["available"] is False
    assert "cannot build my controls" in formless["reason"]
    assert formless["fields"] == []


def test_a_self_test_that_hangs_is_given_up_on_rather_than_waited_out(monkeypatch):
    """``probe`` is third-party code with a serial port behind it. Called on the
    calling thread — which it used to be — one that hangs wedges the plugin
    list, the refresh endpoint and the pre-flight that runs before the arm
    moves. Same argument as keeping actions off the control loop, one layer up.
    """
    monkeypatch.setattr("backend.actions.registry.PROBE_TIMEOUT_S", 0.05)
    released = threading.Event()

    class Wedged(RelayProvider):
        id = "wedged"

        def probe(self) -> None:
            released.wait(10)

    runner = ThreadedRunner()
    registry = ActionRegistry(runner)
    try:
        registry.register(Wedged())
        started = time.monotonic()
        status = registry.probe("wedged")
        waited = time.monotonic() - started

        assert waited < 2.0, "the caller gave up instead of waiting out the provider"
        assert status.available is False
        assert "self-test" in (status.reason or "")
    finally:
        released.set()
        runner.close()


def test_a_provider_busy_with_an_action_is_not_recorded_as_down(monkeypatch):
    """A health check that lands mid-action must not overwrite the verdict: a
    provider that accepted work is reachable, and marking it down would grey out
    an accessory that is at that moment doing its job."""
    monkeypatch.setattr("backend.actions.registry.PROBE_TIMEOUT_S", 0.05)
    holding = threading.Event()

    class Slow(RelayProvider):
        id = "slow"

        def run(self, params, ctx) -> None:
            holding.wait(10)

    runner = ThreadedRunner()
    registry = ActionRegistry(runner)
    try:
        registry.register(Slow())
        assert registry.probe("slow").available is True

        ctx = ActionContext(routine_id="r", routine_name="r", waypoint_index=0, waypoint_note="")
        job = runner.submit("slow", RelayParams(), ctx, timeout_s=10)

        status = registry.probe("slow")
        assert status.available is True, "busy is not the same as broken"
        assert status.reason is None
    finally:
        holding.set()
        job.wait(1)
        runner.close()


# ── caught on write ──────────────────────────────────────────────────────────


def test_params_the_provider_rejects_are_refused_on_write(rig):
    """Stored bad params fail mid-run — arm at the pose, subject waiting, an
    hour after the typo. So they are refused as the blocks are saved."""
    client, registry, *_ = rig
    registry.register(RelayProvider())

    sid = client.post("/api/sequences", json={"name": "plugins"}).json()["id"]
    r = client.patch(f"/api/sequences/{sid}", json={"blocks": [
        {"type": "hold", "pose_id": "whatever", "duration_s": 1.0, "markers": [
            {"kind": "relay", "params": {"channel": 99}, "at": 0.5, "estimate_s": 0.3}]}]})

    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "bad_marker_params"
    assert "channel" in r.json()["detail"]["reasons"][0]


def test_a_marker_for_a_provider_nobody_installed_is_refused_on_write(client: TestClient):
    sid = client.post("/api/sequences", json={"name": "plugins"}).json()["id"]
    r = client.patch(f"/api/sequences/{sid}", json={"blocks": [
        {"type": "hold", "pose_id": "whatever", "duration_s": 1.0, "markers": [
            {"kind": "nobody", "params": {}, "at": 0.5, "estimate_s": 0.3}]}]})

    assert r.status_code == 400
    assert "nobody" in r.json()["detail"]["reasons"][0]


def test_good_params_round_trip_through_storage(rig):
    client, registry, *_ = rig
    registry.register(RelayProvider())
    sid = make_station(client, [
        {"kind": "relay", "params": {"channel": 3}, "at": 0.5, "estimate_s": 0.3}])

    stored = client.get(f"/api/sequences/{sid}").json()["blocks"][0]["markers"][0]
    assert stored["kind"] == "relay"
    assert stored["params"] == {"channel": 3}
    assert stored["at"] == 0.5


# ── caught before anything moves ─────────────────────────────────────────────


def test_executing_a_sequence_whose_plugin_is_gone_is_refused_before_moving(rig):
    """A sequence outlives the plugin it was written against."""
    client, registry, _, arm, _ = rig
    registry.register(RelayProvider())
    sid = make_station(client, [
        {"kind": "relay", "params": {}, "at": 0.5, "estimate_s": 0.3}])

    # Stand in for the JSON on disk outliving the install that wrote it: edit
    # the stored sequence directly, the way an older version of the plugin, or
    # a hand-edited file, would leave it.
    store = app.state.sequence_store
    stored = store.get(sid)
    stored.blocks[0].markers[0].kind = "uninstalled"
    store.save(stored)
    before = dict(arm.read_state().positions)

    r = client.post(f"/api/sequences/{sid}/execute")

    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "missing_providers"
    assert arm.read_state().positions == before, "the arm moved before refusing"


def test_execute_refuses_a_sequence_whose_provider_is_down(rig):
    client, registry, _, arm, _ = rig
    relay = RelayProvider()
    registry.register(relay)
    sid = make_station(client, [
        {"kind": "relay", "params": {}, "at": 0.5, "estimate_s": 0.3}])

    relay.healthy = False
    registry.probe_all()
    before = dict(arm.read_state().positions)

    r = client.post(f"/api/sequences/{sid}/execute")

    assert r.status_code == 400
    assert "not answering" in str(r.json()["detail"]["reasons"])
    assert arm.read_state().positions == before


def test_a_healthy_plugin_marker_runs_on_arrival(rig):
    client, registry, controller, arm, clock = rig
    relay = RelayProvider()
    registry.register(relay)
    sid = make_station(client, [
        {"kind": "relay", "params": {"channel": 2}, "at": 0.2, "estimate_s": 0.3}])

    assert client.post(f"/api/sequences/{sid}/execute").status_code == 200
    for _ in range(5000):
        clock.now += 0.01
        arm.step(0.01)
        controller.tick()
        if not controller.is_playing:
            break

    assert controller.executor.phase.value == "done"
    assert [p.channel for p in relay.calls] == [2]


# ── failure policy ───────────────────────────────────────────────────────────
#
# v2 fixes the marker failure policy at abort: a silently missed frame is not
# discovered until the whole set is reviewed. There is no retry setting left to
# downgrade, so a provider declaring itself unrepeatable lands exactly where
# v1's downgrade already put it: it is never re-run.


def test_a_provider_that_declares_itself_unrepeatable_is_never_retried():
    """Fixed abort honours the declaration by construction: one call, done."""
    from backend.core import Phase, SequenceExecutor
    from backend.sequences import EventMarker, HoldBlock, Pose, Sequence

    clock = FakeClock()
    arm = SimArm(JOINTS, clock=clock, tau=0.05)
    arm.connect()
    strobe = UnrepeatableProvider()
    runner = InlineRunner([strobe])

    target = Pose(name="p", joints={"joint1": 0.1, "joint2": 0.0})
    sequence = Sequence(
        name="x",
        blocks=[HoldBlock(pose_id=target.id, duration_s=1.0, markers=[
            EventMarker(kind="strobe", params={}, at=0.2)])],
    )
    executor = SequenceExecutor(
        sequence, {target.id: target}, arm=arm, actions=runner, clock=clock)
    executor.start()
    for _ in range(5000):
        clock.now += 0.01
        arm.step(0.01)
        executor.tick()
        if executor.is_finished:
            break

    assert executor.phase is Phase.ABORTED
    assert len(strobe.calls) == 1, "retried something that said it could not be"


def test_a_flaky_provider_is_not_retried_either():
    """The retry knob is gone with the waypoint actions; a failure aborts."""
    from backend.core import Phase, SequenceExecutor
    from backend.sequences import EventMarker, HoldBlock, Pose, Sequence

    clock = FakeClock()
    arm = SimArm(JOINTS, clock=clock, tau=0.05)
    arm.connect()

    class Flaky(RelayProvider):
        id = "flaky"

        def run(self, params, ctx):
            self.calls.append(params)
            if len(self.calls) == 1:
                raise ActionUnavailable("first try")

    flaky = Flaky()
    target = Pose(name="p", joints={"joint1": 0.1, "joint2": 0.0})
    sequence = Sequence(
        name="x",
        blocks=[HoldBlock(pose_id=target.id, duration_s=1.0, markers=[
            EventMarker(kind="flaky", params={}, at=0.2)])],
    )
    executor = SequenceExecutor(
        sequence, {target.id: target}, arm=arm, actions=InlineRunner([flaky]), clock=clock)
    executor.start()
    for _ in range(5000):
        clock.now += 0.01
        arm.step(0.01)
        executor.tick()
        if executor.is_finished:
            break

    assert executor.phase is Phase.ABORTED
    assert len(flaky.calls) == 1


# ── the registry and the runner cannot disagree ──────────────────────────────


def test_the_runner_is_the_single_register_of_what_exists():
    """Two lists of installed providers would drift, and the one the operator
    reads is not the one that runs."""
    runner = ThreadedRunner()
    registry = ActionRegistry(runner)
    registry.register(RelayProvider())

    assert runner.provider_ids == ["relay"]
    assert registry.provider_ids == ["relay"]
    assert registry.provider("relay") is runner.provider("relay")
    runner.close()
