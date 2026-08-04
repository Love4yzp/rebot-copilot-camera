"""The claim that installing a plugin is all it takes.

Everything else about plugins is tested against providers defined in the test
file, which proves the *host* behaves — and proves nothing about the packaging
metadata a third party has to write. That metadata is the part the guide teaches
and the part with no feedback: a misspelled entry point group, a factory that
needs an argument, a ``module:Class`` path that does not resolve. All three
produce a plugin that simply never appears, and the first person to find out
would be someone following ``docs/PLUGINS.md`` on a device.

So the worked example from that document is a real package in the dev
environment (see ``[tool.uv.sources]``), and these tests reach it the way the
service does: ``importlib.metadata``, no monkeypatching.
"""

from __future__ import annotations

import pytest

from backend.actions import ActionContext, ActionRegistry, ThreadedRunner

pytest.importorskip(
    "rebot_plugin_turntable",
    reason="the example plugin is a dev dependency; run `uv sync`",
)


@pytest.fixture
def registry():
    runner = ThreadedRunner()
    registry = ActionRegistry(runner)
    yield registry
    runner.close()


def test_installing_a_package_is_all_it_takes_to_add_an_action(registry):
    """No host change, no front-end change, no registration call in the plugin.

    This is the whole promise of the entry point mechanism, and the only test
    that exercises the real metadata rather than a stand-in for it.
    """
    registry.discover()

    assert "turntable" in registry.provider_ids
    entry = next(e for e in registry.manifest() if e["id"] == "turntable")
    assert entry["installed"] is True
    assert entry["label"] == "转台"
    # Its form arrives described, so the edit sheet can draw it with the host's
    # own widgets without the plugin shipping any markup.
    assert [f["kind"] for f in entry["fields"]] == ["tiers"]


def test_a_relative_move_declares_itself_unrepeatable(registry):
    """Retrying a rotation turns the table *again*, which is a different pose —
    not the same one attempted twice. The host has to be told."""
    registry.discover()

    entry = next(e for e in registry.manifest() if e["id"] == "turntable")
    assert entry["retryable"] is False


def test_the_example_runs_with_no_accessory_attached(monkeypatch, registry):
    """A plugin author's first day should not need the hardware either — the
    same claim SimArm and SimShutter make one layer down."""
    import rebot_plugin_turntable as plugin

    provider = plugin.TurntableProvider()
    monkeypatch.setattr(provider, "_port", "sim")
    registry.register(provider, replace=True)

    events: list[tuple[str, dict]] = []
    ctx = ActionContext(
        routine_id="r",
        routine_name="r",
        waypoint_index=0,
        waypoint_note="",
        emit=lambda name, data: events.append((name, data)),
    )

    provider.probe()
    provider.run(plugin.TurntableParams(degrees=90), ctx)

    assert events == [("turntable.rotated", {"degrees": 90.0})]


def test_a_missing_table_is_unavailable_rather_than_an_unhandled_error(registry):
    """The ordinary case of an accessory nobody plugged in. It has to arrive as
    a reason on the plugin list, not as a traceback out of the pre-flight."""
    import rebot_plugin_turntable as plugin

    provider = plugin.TurntableProvider()
    provider._port = "/dev/definitely-not-a-turntable"
    registry.register(provider, replace=True)

    status = registry.probe("turntable")

    assert status.available is False
    assert "unreachable" in (status.reason or "")
