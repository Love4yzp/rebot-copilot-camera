"""v1 → v2 migration: fixture routine JSON in, poses and sequences out."""

import json
from pathlib import Path

import pytest

from backend.sequences import (
    PoseStore,
    SequenceStore,
    TransitionBlock,
    maybe_migrate,
    migrate_routines,
)


@pytest.fixture
def stores(tmp_path: Path):
    return PoseStore(tmp_path / "poses"), SequenceStore(tmp_path / "sequences")


@pytest.fixture
def routines_dir(tmp_path: Path) -> Path:
    return tmp_path / "routines"


def write_routine(routines_dir: Path, rid: str, waypoints: list[dict], name: str = "旧序列") -> None:
    routines_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "id": rid,
        "name": name,
        "created_at": 1000.0,
        "updated_at": 2000.0,
        "waypoints": waypoints,
    }
    (routines_dir / f"{rid}.json").write_text(json.dumps(payload), encoding="utf-8")


def wp(j1: float, **kwargs) -> dict:
    return {"joints": {"joint1": j1, "joint2": 0.0}, **kwargs}


def test_a_routine_becomes_poses_and_a_block_sequence(stores, routines_dir):
    poses, sequences = stores
    write_routine(routines_dir, "aaa111", [
        wp(0.2, note="正面", settle_ms=500, duration_s=2.0,
           actions=[{"type": "shutter", "count": 2, "interval_s": 1.0, "focus_first": True}]),
        wp(0.5, note="", settle_ms=300, duration_s=4.0, actions=[]),
    ])

    report = migrate_routines(routines_dir, poses, sequences)

    assert report.routines_migrated == 1
    assert report.poses_created == 2

    # Every waypoint became a pose, named by its note or 位姿 N.
    library = poses.list()
    assert [p.name for p in library] == ["正面", "位姿 2"]
    assert library[0].joints == {"joint1": 0.2, "joint2": 0.0}

    sequence = sequences.get("aaa111")
    assert sequence.name == "旧序列"
    assert sequence.created_at == 1000.0
    assert [b.type for b in sequence.blocks] == ["hold", "transition", "hold"]

    first, transition, last = sequence.blocks
    assert first.pose_id == library[0].id
    # settle 0.5s + burst estimate (2 frames × 0.3 + 1 × 1.0 gap).
    assert first.duration_s == pytest.approx(0.5 + 2 * 0.3 + 1.0)
    marker = first.markers[0]
    assert marker.kind == "shutter"
    assert marker.at == pytest.approx(0.5), "pinned where the ACTING phase began"
    assert marker.params == {"count": 2, "interval_s": 1.0, "focus_first": True}

    # The transition takes the *next* waypoint's move duration.
    assert transition.duration_s == 4.0
    assert transition.easing == "ease_in_out"

    assert last.duration_s == pytest.approx(0.3)
    assert last.markers == []


def test_a_sleep_action_folds_into_the_hold_duration(stores, routines_dir):
    """A v1 sleep is pure time on the plan ruler, not an event — as a marker
    with no provider behind it, it would fail the run at execute time."""
    poses, sequences = stores
    write_routine(routines_dir, "bbb222", [
        wp(0.2, settle_ms=100, actions=[
            {"type": "shutter"},
            {"type": "sleep", "duration_s": 2.0},
            {"type": "shutter"},
        ]),
    ])

    report = migrate_routines(routines_dir, poses, sequences)
    sequence = sequences.get("bbb222")
    block = sequence.blocks[0]

    # settle 0.1 + shutter 0.3 + sleep 2.0 + shutter 0.3.
    assert block.duration_s == pytest.approx(0.1 + 0.3 + 2.0 + 0.3)
    assert [m.kind for m in block.markers] == ["shutter", "shutter"]
    assert block.markers[0].at == pytest.approx(0.1)
    assert block.markers[1].at == pytest.approx(0.1 + 0.3 + 2.0)
    assert report.actions_dropped == 0


def test_a_plugin_action_becomes_a_marker_with_a_conservative_estimate(stores, routines_dir):
    poses, sequences = stores
    write_routine(routines_dir, "ccc333", [
        wp(0.2, settle_ms=0, actions=[
            {"type": "plugin", "provider": "turntable", "params": {"degrees": 90}},
        ]),
    ])

    migrate_routines(routines_dir, poses, sequences)
    marker = sequences.get("ccc333").blocks[0].markers[0]

    assert marker.kind == "turntable"
    assert marker.params == {"degrees": 90}
    # The provider is not asked at migration time; 5.0 is v1's default timeout.
    assert marker.estimate_s == 5.0


def test_an_unparseable_file_is_skipped_not_fatal(stores, routines_dir):
    poses, sequences = stores
    routines_dir.mkdir(parents=True, exist_ok=True)
    (routines_dir / "broken.json").write_text("{ nope", encoding="utf-8")
    write_routine(routines_dir, "ddd444", [wp(0.1)])

    report = migrate_routines(routines_dir, poses, sequences)

    assert report.routines_migrated == 1
    assert report.files_skipped == ["broken.json"]


def test_the_originals_are_left_in_place(stores, routines_dir):
    poses, sequences = stores
    write_routine(routines_dir, "eee555", [wp(0.1)])

    migrate_routines(routines_dir, poses, sequences)

    assert (routines_dir / "eee555.json").exists(), "the v1 files are the backup"


def test_maybe_migrate_only_when_the_v2_library_is_empty(stores, routines_dir, tmp_path):
    poses, sequences = stores
    write_routine(routines_dir, "fff666", [wp(0.1)])
    sequences_dir = sequences.root

    assert maybe_migrate(routines_dir, sequences_dir, poses, sequences) is not None
    again = maybe_migrate(routines_dir, sequences_dir, poses, sequences)
    assert again is None, "a populated v2 library means a human is managing the files"

    # And nothing happens with no v1 data either.
    empty = tmp_path / "empty"
    empty.mkdir()
    fresh_poses = PoseStore(tmp_path / "p2")
    fresh_sequences = SequenceStore(tmp_path / "s2")
    assert maybe_migrate(empty, fresh_sequences.root, fresh_poses, fresh_sequences) is None


def test_a_migrated_sequence_is_normalized_and_executable_shape(stores, routines_dir):
    """Two waypoints at the same pose stay two stations; blocks alternate."""
    poses, sequences = stores
    write_routine(routines_dir, "ggg777", [wp(0.1), wp(0.1), wp(0.5)])

    migrate_routines(routines_dir, poses, sequences)
    blocks = sequences.get("ggg777").blocks

    assert [b.type for b in blocks] == [
        "hold", "transition", "hold", "transition", "hold"
    ]
    # Distinct pose entries with equal joints still get a transition between
    # them — normalize keys on pose id, and migration does not dedupe.
    assert isinstance(blocks[1], TransitionBlock)
