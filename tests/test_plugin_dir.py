"""The drop-in route: a folder in ``plugins/`` with a ``plugin.json``.

Entry-point plugins prove the packaging metadata works
(``test_plugin_packaging.py``); these prove the other install route behaves
the same way at the registry — loads, fails loudly alone, disables cleanly.
Each test uses fresh module names because imported modules stay in
``sys.modules`` for the rest of the session.
"""

import json
from pathlib import Path

import pytest

from backend.actions import ActionContext, ActionRegistry, ThreadedRunner

BODY = '''
from pydantic import BaseModel

class Params(BaseModel):
    pass

class Provider:
    id = {pid!r}
    label = {label!r}
    params_model = Params
    retryable = True

    def fields(self):
        return []

    def probe(self):
        return None

    def run(self, params, ctx):
        ctx.emit({pid!r} + ".ran", {{}})
'''


def make_plugin(
    root: Path,
    dirname: str,
    module: str,
    *,
    pid: str,
    label: str = " dropped",
    enabled: bool = True,
    body: str | None = None,
    raw_manifest: str | None = None,
) -> Path:
    d = root / dirname
    d.mkdir()
    (d / f"{module}.py").write_text(body or BODY.format(pid=pid, label=label))
    if raw_manifest is not None:
        (d / "plugin.json").write_text(raw_manifest)
    else:
        (d / "plugin.json").write_text(json.dumps(
            {"module": module, "provider": "Provider", "enabled": enabled}))
    return d


@pytest.fixture
def registry():
    runner = ThreadedRunner()
    registry = ActionRegistry(runner)
    yield registry
    runner.close()


def manifest_entry(registry: ActionRegistry, pid: str) -> dict:
    return next(e for e in registry.manifest() if e["id"] == pid)


def test_a_dropped_folder_loads_and_runs(registry, tmp_path):
    """Installing is copying a folder — no pip, no lockfile, nothing for
    ``uv sync`` to prune back out."""
    make_plugin(tmp_path, "myrelay", "myrelay_mod", pid="myrelay")

    registry.discover_dir(tmp_path)

    entry = manifest_entry(registry, "myrelay")
    assert entry["installed"] is True
    events = []
    ctx = ActionContext(routine_id="r", routine_name="r", waypoint_index=0,
                        waypoint_note="", emit=lambda n, d: events.append(n))
    job = registry._runner.submit("myrelay", registry.provider("myrelay").params_model(),
                                  ctx, timeout_s=5)
    job.wait(5)
    assert job.error is None
    assert events == ["myrelay.ran"]


def test_a_disabled_plugin_is_listed_as_off_not_missing(registry, tmp_path):
    """The off switch is a line in its own plugin.json. Greyed with the reason,
    so a deliberately-off accessory never reads as 'I configured it wrong'."""
    make_plugin(tmp_path, "offlamp", "offlamp_mod", pid="offlamp", enabled=False)

    registry.discover_dir(tmp_path)

    entry = manifest_entry(registry, "offlamp")
    assert entry["installed"] is False
    assert "disabled" in entry["reason"]
    assert registry.provider("offlamp") is None


def test_an_unreadable_manifest_costs_only_itself(registry, tmp_path):
    make_plugin(tmp_path, "garbled", "garbled_mod", pid="garbled",
                raw_manifest="{not json")
    make_plugin(tmp_path, "fine", "fine_mod", pid="fine")

    registry.discover_dir(tmp_path)  # must not raise

    entry = manifest_entry(registry, "garbled")
    assert entry["installed"] is False
    assert "plugin.json" in entry["reason"]
    assert manifest_entry(registry, "fine")["installed"] is True


def test_a_module_that_raises_on_import_costs_only_itself(registry, tmp_path):
    make_plugin(tmp_path, "exploder", "exploder_mod", pid="exploder",
                body="raise ImportError('no module named pyserial_but_misspelled')")

    registry.discover_dir(tmp_path)  # must not raise

    entry = manifest_entry(registry, "exploder")
    assert entry["installed"] is False
    assert "misspelled" in entry["reason"]
    assert registry.provider("exploder") is None


def test_a_shapeless_provider_is_refused_at_the_same_gate(registry, tmp_path):
    """Dropped-in code goes through check_shape like an entry-point plugin —
    the route is different, the contract is not."""
    body = "class Provider:\n    label = '无名'\n"
    make_plugin(tmp_path, "shapeless", "shapeless_mod", pid="shapeless", body=body)

    registry.discover_dir(tmp_path)

    entry = manifest_entry(registry, "shapeless")
    assert entry["installed"] is False
    assert "id" in entry["reason"]


def test_a_dropped_plugin_cannot_take_an_id_that_is_taken(registry, tmp_path):
    """Same guard as entry points: an id names a provider in stored sequences,
    so two cannot share one — whoever loads second is refused loudly."""
    make_plugin(tmp_path, "first", "first_mod", pid="shared")
    make_plugin(tmp_path, "second", "second_mod", pid="shared")

    registry.discover_dir(tmp_path)

    assert manifest_entry(registry, "shared")["installed"] is True
    second = manifest_entry(registry, "second")
    assert second["installed"] is False
    assert "already registered" in second["reason"]


def test_a_module_name_collision_resolves_to_the_first_loader(registry, tmp_path):
    """import_module caches by name, so a second folder reusing a module name
    gets the first one's code — and its duplicate id is then refused at the
    gate rather than silently loading the wrong plugin over the right one."""
    make_plugin(tmp_path, "alpha", "colliding_mod", pid="alpha")
    make_plugin(tmp_path, "beta", "colliding_mod", pid="beta")

    registry.discover_dir(tmp_path)

    assert manifest_entry(registry, "alpha")["installed"] is True
    beta = manifest_entry(registry, "beta")
    assert beta["installed"] is False
    assert "already registered" in beta["reason"]
    assert registry.provider("beta") is None


def test_a_folder_without_a_manifest_is_ignored(registry, tmp_path):
    """Notes, scratch, a half-downloaded copy — not the host's business."""
    (tmp_path / "readme-stuff").mkdir()
    (tmp_path / "readme-stuff" / "notes.txt").write_text("someday")

    registry.discover_dir(tmp_path)  # must not raise

    assert registry.provider_ids == []


def test_a_missing_plugins_dir_is_not_an_error(registry, tmp_path):
    registry.discover_dir(tmp_path / "never-created")
    assert registry.provider_ids == []
