"""Routine model and store round-trips."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.routines import (
    SCHEMA_VERSION,
    FailurePolicy,
    Routine,
    RoutineNotFound,
    RoutineStore,
    ShutterAction,
    SleepAction,
    Waypoint,
)


@pytest.fixture
def store(tmp_path: Path) -> RoutineStore:
    return RoutineStore(tmp_path / "routines")


def a_waypoint(**kwargs) -> Waypoint:
    return Waypoint(joints={"joint1": 0.1, "joint2": 0.2}, **kwargs)


# ── model ────────────────────────────────────────────────────────────────────


def test_waypoint_requires_at_least_one_joint():
    with pytest.raises(ValidationError):
        Waypoint(joints={})


def test_waypoint_rejects_non_finite_angles():
    """NaN survives JSON round-trips in some encoders and then commands a move."""
    with pytest.raises(ValidationError, match="finite"):
        Waypoint(joints={"joint1": float("nan")})


def test_shutter_defaults_to_aborting_the_routine():
    """A silently missed frame is not noticed until the whole set is reviewed."""
    assert ShutterAction().on_failure is FailurePolicy.ABORT


def test_routine_carries_a_schema_version_from_the_first_release():
    assert Routine(name="x").schema_version == SCHEMA_VERSION


def test_actions_round_trip_to_their_concrete_types():
    """The discriminated union is what keeps a reloaded routine executable."""
    original = Routine(
        name="multi-angle",
        waypoints=[a_waypoint(actions=[SleepAction(duration_s=1.0), ShutterAction()])],
    )
    restored = Routine.model_validate_json(original.model_dump_json())

    kinds = [type(a) for a in restored.waypoints[0].actions]
    assert kinds == [SleepAction, ShutterAction]
    assert restored.waypoints[0].actions[0].duration_s == 1.0


def test_unknown_action_type_is_rejected():
    payload = {
        "name": "x",
        "waypoints": [{"joints": {"joint1": 0.0}, "actions": [{"type": "launch_missile"}]}],
    }
    with pytest.raises(ValidationError):
        Routine.model_validate(payload)


def test_summary_counts_shutters_across_waypoints():
    from backend.routines import RoutineSummary

    routine = Routine(
        name="x",
        waypoints=[
            a_waypoint(actions=[ShutterAction()]),
            a_waypoint(actions=[SleepAction(duration_s=1), ShutterAction()]),
            a_waypoint(),
        ],
    )
    summary = RoutineSummary.of(routine)
    assert summary.waypoint_count == 3
    assert summary.shutter_count == 2


# ── store ────────────────────────────────────────────────────────────────────


def test_save_and_get_round_trip(store: RoutineStore):
    routine = Routine(name="round the subject", waypoints=[a_waypoint(settle_ms=800)])
    store.save(routine)

    loaded = store.get(routine.id)
    assert loaded.name == "round the subject"
    assert loaded.waypoints[0].settle_ms == 800
    assert loaded.waypoints[0].joints == {"joint1": 0.1, "joint2": 0.2}


def test_get_missing_raises(store: RoutineStore):
    with pytest.raises(RoutineNotFound):
        store.get("nope")


def test_delete_removes_and_then_raises(store: RoutineStore):
    routine = store.save(Routine(name="x"))
    store.delete(routine.id)

    assert store.exists(routine.id) is False
    with pytest.raises(RoutineNotFound):
        store.delete(routine.id)


def test_list_is_newest_updated_first(store: RoutineStore):
    old = Routine(name="old", updated_at=100.0)
    new = Routine(name="new", updated_at=200.0)
    store.save(old)
    store.save(new)

    assert [s.name for s in store.list()] == ["new", "old"]


def test_list_skips_a_corrupt_file_instead_of_failing(store: RoutineStore):
    """One bad file must not make the whole library unreachable — that is
    exactly when the operator needs the other routines."""
    good = store.save(Routine(name="good"))
    (store.root / "broken.json").write_text("{ this is not json", encoding="utf-8")
    (store.root / "wrong-shape.json").write_text(json.dumps({"nope": 1}), encoding="utf-8")

    names = [s.name for s in store.list()]
    assert names == ["good"]
    assert store.get(good.id).name == "good"


def test_save_leaves_no_temp_files_behind(store: RoutineStore):
    store.save(Routine(name="x"))
    assert [p.name for p in store.root.iterdir() if p.suffix == ".tmp"] == []


def test_a_failed_write_leaves_the_previous_version_intact(store: RoutineStore, monkeypatch):
    """The point of the temp-file dance: never a half-written routine."""
    routine = store.save(Routine(name="original", waypoints=[a_waypoint()]))

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("backend.routines.store.os.replace", boom)
    routine.name = "updated"
    with pytest.raises(OSError):
        store.save(routine)

    assert store.get(routine.id).name == "original"
    assert [p.name for p in store.root.iterdir() if p.suffix == ".tmp"] == []


@pytest.mark.parametrize("rid", ["../escape", "a/b", "", "x" * 65, "has space"])
def test_ids_that_could_escape_the_store_are_rejected(store: RoutineStore, rid: str):
    with pytest.raises(RoutineNotFound):
        store.get(rid)


def test_store_creates_its_directory(tmp_path: Path):
    store = RoutineStore(tmp_path / "deep" / "nested")
    assert store.root.is_dir()
