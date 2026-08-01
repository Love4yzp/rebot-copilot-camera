"""The plugin surface: registry, manifest, and the two pre-flights.

The claims worth pinning down are all about *when* a bad plugin is noticed. The
expensive moment is the ACTING phase — arm at the anchor, subject waiting — so
everything here is about catching it earlier: on write, on play, or at startup.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from backend.actions import (
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
from backend.routines import PluginAction, RoutineStore
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
    return TestClient(app), app.state.plugins, app.state.controller, arm, clock


@pytest.fixture
def client(rig) -> TestClient:
    return rig[0]


def make_anchor(client: TestClient, actions: list[dict]) -> tuple[str, int]:
    rid = client.post("/api/routines", json={"name": "plugins"}).json()["id"]
    r = client.post(
        f"/api/routines/{rid}/waypoints",
        json={"joints": {"joint1": 0.2, "joint2": 0.0}, "settle_ms": 0, "actions": actions},
    )
    assert r.status_code == 201, r.text
    return rid, 0


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
    assert "misspelled" in entry["reason"]
    assert entry["fields"] == []
    assert registry.provider("exploder") is None


# ── caught on write ──────────────────────────────────────────────────────────


def test_params_the_provider_rejects_are_refused_on_write(rig):
    """Stored bad params fail in the ACTING phase — arm at the anchor, subject
    waiting, an hour after the typo. So they are refused as the anchor is saved.
    """
    client, registry, *_ = rig
    registry.register(RelayProvider())

    rid = client.post("/api/routines", json={"name": "plugins"}).json()["id"]
    r = client.post(
        f"/api/routines/{rid}/waypoints",
        json={
            "joints": {"joint1": 0.2, "joint2": 0.0},
            "actions": [{"type": "plugin", "provider": "relay", "params": {"channel": 99}}],
        },
    )

    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "bad_action_params"
    assert "channel" in r.json()["detail"]["reasons"][0]


def test_an_action_for_a_provider_nobody_installed_is_refused_on_write(client: TestClient):
    rid = client.post("/api/routines", json={"name": "plugins"}).json()["id"]
    r = client.post(
        f"/api/routines/{rid}/waypoints",
        json={
            "joints": {"joint1": 0.2, "joint2": 0.0},
            "actions": [{"type": "plugin", "provider": "nobody", "params": {}}],
        },
    )

    assert r.status_code == 400
    assert "nobody" in r.json()["detail"]["reasons"][0]


def test_good_params_round_trip_through_storage(rig):
    client, registry, *_ = rig
    registry.register(RelayProvider())
    rid, _ = make_anchor(client, [{"type": "plugin", "provider": "relay", "params": {"channel": 3}}])

    stored = client.get(f"/api/routines/{rid}").json()["waypoints"][0]["actions"][0]
    assert stored == {
        "type": "plugin",
        "provider": "relay",
        "params": {"channel": 3},
        "timeout_s": 5.0,
        "on_failure": "abort",
        "retries": 0,
    }


# ── caught before anything moves ─────────────────────────────────────────────


def test_playing_a_routine_whose_plugin_is_gone_is_refused_before_moving(rig):
    """A routine outlives the plugin it was written against."""
    client, registry, _, arm, _ = rig
    registry.register(RelayProvider())
    rid, _ = make_anchor(client, [{"type": "plugin", "provider": "relay", "params": {}}])

    # Stand in for the JSON on disk outliving the install that wrote it: edit
    # the stored routine directly, the way an older version of the plugin, or a
    # hand-edited file, would leave it.
    store = app.state.routine_store
    stored = store.get(rid)
    stored.waypoints[0].actions = [PluginAction(provider="uninstalled")]
    store.save(stored)
    before = dict(arm.read_state().positions)

    r = client.post(f"/api/routines/{rid}/play")

    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "missing_providers"
    assert arm.read_state().positions == before, "the arm moved before refusing"


def test_goto_refuses_an_anchor_whose_provider_is_down(rig):
    client, registry, _, arm, _ = rig
    relay = RelayProvider()
    registry.register(relay)
    rid, index = make_anchor(client, [{"type": "plugin", "provider": "relay", "params": {}}])

    relay.healthy = False
    registry.probe_all()
    before = dict(arm.read_state().positions)

    r = client.post(f"/api/routines/{rid}/waypoints/{index}/goto")

    assert r.status_code == 400
    assert "not answering" in str(r.json()["detail"]["reasons"])
    assert arm.read_state().positions == before


def test_a_healthy_plugin_action_runs_on_arrival(rig):
    client, registry, controller, arm, clock = rig
    relay = RelayProvider()
    registry.register(relay)
    rid, index = make_anchor(client, [{"type": "plugin", "provider": "relay", "params": {"channel": 2}}])

    assert client.post(f"/api/routines/{rid}/waypoints/{index}/goto").status_code == 200
    for _ in range(5000):
        clock.now += 0.01
        arm.step(0.01)
        controller.tick()
        if not controller.is_playing:
            break

    assert controller.executor.phase.value == "done"
    assert [p.channel for p in relay.calls] == [2]


# ── retry policy ─────────────────────────────────────────────────────────────


def test_a_provider_that_declares_itself_unrepeatable_is_never_retried():
    """A silently ignored retry setting is worse than either honouring it or
    refusing it, so the host downgrades to abort and says so."""
    from backend.core import Phase, RoutineExecutor
    from backend.routines import PluginAction, Routine, Waypoint

    clock = FakeClock()
    arm = SimArm(JOINTS, clock=clock, tau=0.05)
    arm.connect()
    strobe = UnrepeatableProvider()
    runner = InlineRunner([strobe])

    routine = Routine(
        name="x",
        waypoints=[
            Waypoint(
                joints={"joint1": 0.1, "joint2": 0.0},
                settle_ms=0,
                actions=[PluginAction(provider="strobe", on_failure="retry", retries=3)],
            )
        ],
    )
    executor = RoutineExecutor(routine, arm=arm, actions=runner, clock=clock)
    executor.start()
    for _ in range(5000):
        clock.now += 0.01
        arm.step(0.01)
        executor.tick()
        if executor.is_finished:
            break

    assert executor.phase is Phase.ABORTED
    assert len(strobe.calls) == 1, "retried something that said it could not be"


def test_a_retryable_provider_still_gets_its_retries():
    from backend.core import Phase, RoutineExecutor
    from backend.routines import PluginAction, Routine, Waypoint

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
    routine = Routine(
        name="x",
        waypoints=[
            Waypoint(
                joints={"joint1": 0.1, "joint2": 0.0},
                settle_ms=0,
                actions=[PluginAction(provider="flaky", on_failure="retry", retries=2)],
            )
        ],
    )
    executor = RoutineExecutor(routine, arm=arm, actions=InlineRunner([flaky]), clock=clock)
    executor.start()
    for _ in range(5000):
        clock.now += 0.01
        arm.step(0.01)
        executor.tick()
        if executor.is_finished:
            break

    assert executor.phase is Phase.DONE
    assert len(flaky.calls) == 2


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
