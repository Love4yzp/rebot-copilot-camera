"""The three v2 stores: model and persistence round-trips."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.sequences import (
    SCHEMA_VERSION,
    EventMarker,
    HoldBlock,
    Pose,
    PoseNotFound,
    PoseStore,
    SeqTemplate,
    Sequence,
    SequenceNotFound,
    SequenceStore,
    SequenceSummary,
    TemplateNotFound,
    TemplateStore,
    TransitionBlock,
)


@pytest.fixture
def stores(tmp_path: Path):
    return (
        PoseStore(tmp_path / "poses"),
        SequenceStore(tmp_path / "sequences"),
        TemplateStore(tmp_path / "templates"),
    )


def a_pose(**kwargs) -> Pose:
    kwargs.setdefault("name", "正面")
    kwargs.setdefault("joints", {"joint1": 0.1, "joint2": 0.2})
    return Pose(**kwargs)


def a_sequence(**kwargs) -> Sequence:
    kwargs.setdefault("name", "round the subject")
    return Sequence(**kwargs)


# ── model ────────────────────────────────────────────────────────────────────


def test_pose_requires_at_least_one_joint():
    with pytest.raises(ValidationError):
        Pose(name="x", joints={})


def test_pose_rejects_non_finite_angles():
    """NaN survives JSON round-trips in some encoders and then commands a move."""
    with pytest.raises(ValidationError, match="finite"):
        Pose(name="x", joints={"joint1": float("nan")})


def test_sequence_carries_schema_version_2():
    assert Sequence(name="x").schema_version == 2 == SCHEMA_VERSION


def test_blocks_round_trip_to_their_concrete_types():
    """The discriminated union is what keeps a reloaded sequence executable."""
    original = Sequence(
        name="x",
        blocks=[
            HoldBlock(pose_id="abc", duration_s=2.0,
                      markers=[EventMarker(kind="wait", at=1.0, estimate_s=0.0)]),
            TransitionBlock(duration_s=1.5, easing="linear"),
        ],
    )
    restored = Sequence.model_validate_json(original.model_dump_json())

    assert [type(b) for b in restored.blocks] == [HoldBlock, TransitionBlock]
    assert restored.blocks[0].markers[0].kind == "wait"
    assert restored.blocks[1].easing == "linear"


def test_an_unknown_block_type_is_rejected():
    payload = {"name": "x", "blocks": [{"type": "loop", "duration_s": 1}]}
    with pytest.raises(ValidationError):
        Sequence.model_validate(payload)


def test_summary_counts_stations_and_plan_ruler_duration():
    sequence = Sequence(
        name="x",
        blocks=[
            HoldBlock(pose_id="a", duration_s=3.0),
            TransitionBlock(duration_s=2.0),
            HoldBlock(pose_id="b", duration_s=5.0,
                      markers=[EventMarker(kind="wait", at=1.0, estimate_s=0.0)]),
        ],
    )
    summary = SequenceSummary.of(sequence)
    assert summary.station_count == 2
    assert summary.duration_s == 10.0, "markers add nothing — a wait is open-ended"


# ── pose store ───────────────────────────────────────────────────────────────


def test_pose_save_and_get_round_trip(stores):
    poses, _, _ = stores
    pose = poses.save(a_pose())
    loaded = poses.get(pose.id)
    assert loaded.name == "正面"
    assert loaded.joints == {"joint1": 0.1, "joint2": 0.2}


def test_pose_get_missing_raises(stores):
    poses, _, _ = stores
    with pytest.raises(PoseNotFound):
        poses.get("nope")


def test_pose_list_is_in_creation_order(stores):
    """The mock serves insertion order and the library tab renders it."""
    poses, _, _ = stores
    poses.save(a_pose(name="first", created_at=100.0))
    poses.save(a_pose(name="second", created_at=200.0))
    assert [p.name for p in poses.list()] == ["first", "second"]


def test_pose_delete_removes_and_then_raises(stores):
    poses, _, _ = stores
    pose = poses.save(a_pose())
    poses.delete(pose.id)
    assert poses.exists(pose.id) is False
    with pytest.raises(PoseNotFound):
        poses.delete(pose.id)


# ── sequence store ───────────────────────────────────────────────────────────


def test_sequence_round_trip_keeps_blocks(stores):
    _, sequences, _ = stores
    sequence = sequences.save(a_sequence(blocks=[
        HoldBlock(pose_id="abc", duration_s=1.0),
        TransitionBlock(duration_s=2.0, easing="ease_out"),
    ]))
    loaded = sequences.get(sequence.id)
    assert [b.type for b in loaded.blocks] == ["hold", "transition"]
    assert loaded.blocks[1].easing == "ease_out"


def test_sequence_list_returns_summaries_in_creation_order(stores):
    _, sequences, _ = stores
    sequences.save(a_sequence(name="old", created_at=100.0))
    sequences.save(a_sequence(name="new", created_at=200.0,
                              blocks=[HoldBlock(pose_id="a", duration_s=3.0)]))
    summaries = sequences.list()
    assert [s.name for s in summaries] == ["old", "new"]
    assert summaries[1].station_count == 1
    assert summaries[1].duration_s == 3.0


def test_list_skips_a_corrupt_file_instead_of_failing(stores):
    """One bad file must not make the whole library unreachable — that is
    exactly when the operator needs the other sequences."""
    _, sequences, _ = stores
    good = sequences.save(a_sequence(name="good"))
    (sequences.root / "broken.json").write_text("{ this is not json", encoding="utf-8")
    (sequences.root / "wrong-shape.json").write_text(json.dumps({"nope": 1}), encoding="utf-8")

    names = [s.name for s in sequences.list()]
    assert names == ["good"]
    assert sequences.get(good.id).name == "good"


def test_get_missing_raises_sequence_not_found(stores):
    _, sequences, _ = stores
    with pytest.raises(SequenceNotFound):
        sequences.get("nope")


def test_save_leaves_no_temp_files_behind(stores):
    _, sequences, _ = stores
    sequences.save(a_sequence())
    assert [p.name for p in sequences.root.iterdir() if p.suffix == ".tmp"] == []


def test_a_failed_write_leaves_the_previous_version_intact(stores, monkeypatch):
    """The point of the temp-file dance: never a half-written sequence."""
    _, sequences, _ = stores
    sequence = sequences.save(a_sequence(name="original"))

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("backend.sequences.store.os.replace", boom)
    sequence.name = "updated"
    with pytest.raises(OSError):
        sequences.save(sequence)

    assert sequences.get(sequence.id).name == "original"
    assert [p.name for p in sequences.root.iterdir() if p.suffix == ".tmp"] == []


@pytest.mark.parametrize("sid", ["../escape", "a/b", "", "x" * 65, "has space"])
def test_ids_that_could_escape_the_store_are_rejected(stores, sid: str):
    _, sequences, _ = stores
    with pytest.raises(SequenceNotFound):
        sequences.get(sid)


def test_store_creates_its_directory(tmp_path: Path):
    store = SequenceStore(tmp_path / "deep" / "nested")
    assert store.root.is_dir()


# ── template store ───────────────────────────────────────────────────────────


def test_template_round_trip(stores):
    _, _, templates = stores
    template = templates.save(SeqTemplate(
        name="四方位",
        station_count=2,
        recipe=[
            HoldBlock(pose_id="slot:1", duration_s=3.0),
            TransitionBlock(duration_s=2.0),
            HoldBlock(pose_id="slot:2", duration_s=3.0),
        ],
    ))
    loaded = templates.get(template.id)
    assert loaded.station_count == 2
    assert loaded.recipe[0].pose_id == "slot:1"


def test_template_get_missing_raises(stores):
    _, _, templates = stores
    with pytest.raises(TemplateNotFound):
        templates.get("nope")


def test_each_store_has_its_own_not_found(stores):
    """A pose id asked of the sequence store must not read as a missing pose."""
    poses, sequences, _ = stores
    pose = poses.save(a_pose())
    with pytest.raises(SequenceNotFound):
        sequences.get(pose.id)
    with pytest.raises(PoseNotFound):
        poses.get("zzz")
