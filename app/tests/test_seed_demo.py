"""The first-boot demo must plant itself, and never overwrite operator data.

The demo's whole point is that a visitor can press play the moment the service
starts, so the planted data is held to the same validation the write API
applies — a demo that dodged validation would be the one document in the
library nobody checked.
"""

from pathlib import Path

from backend.safety.kinematics import validate_pose, validate_sequence
from backend.sequences import Pose, PoseStore, SequenceStore, TemplateStore
from backend.sequences.seed_demo import (
    SEED_MARKER,
    demo_poses,
    demo_sequence,
    seed_demo_if_empty,
)


def _stores(tmp_path: Path):
    return (
        PoseStore(tmp_path / "poses"),
        SequenceStore(tmp_path / "sequences"),
        TemplateStore(tmp_path / "templates"),
    )


def test_empty_stores_get_seeded_once(tmp_path):
    poses, sequences, templates = _stores(tmp_path)
    assert seed_demo_if_empty(poses, sequences, templates) is True
    assert len(poses.list()) == 4
    assert len(sequences.list()) == 1
    assert len(templates.list()) == 1
    assert (tmp_path / "poses" / SEED_MARKER).exists()

    # The marker makes it stick: emptying nothing, a second boot seeds nothing.
    assert seed_demo_if_empty(poses, sequences, templates) is False
    assert len(poses.list()) == 4


def test_any_existing_document_refuses_seeding(tmp_path):
    poses, sequences, templates = _stores(tmp_path)
    poses.save(Pose(name="mine", joints={"joint1": 0.0}))
    assert seed_demo_if_empty(poses, sequences, templates) is False
    # Partial emptiness never seeds either — the operator data wins everywhere.
    assert sequences.list() == [] and templates.list() == []
    assert not (tmp_path / "poses" / SEED_MARKER).exists()


def test_disabled_seed_plants_nothing(tmp_path):
    poses, sequences, templates = _stores(tmp_path)
    assert seed_demo_if_empty(poses, sequences, templates, enabled=False) is False
    assert poses.list() == [] and sequences.list() == [] and templates.list() == []


def test_demo_poses_pass_write_validation(tmp_path):
    # The same checks POST /api/poses runs, via the same functions.
    poses = demo_poses()
    for pose in poses:
        assert validate_pose(pose.joints) == [], f"{pose.name} is unsafe"
    # And the sequence's play order: poses plus straight-line paths.
    assert validate_sequence([p.joints for p in poses]) == []


def test_demo_markers_name_known_kinds(tmp_path):
    seq = demo_sequence(demo_poses())
    kinds = {m.kind for b in seq.blocks for m in b.markers}
    assert kinds <= {"wait", "shutter"}


def test_demo_sequence_is_normalized_and_idempotent(tmp_path):
    from backend.sequences.normalize import normalize

    seq = demo_sequence(demo_poses())
    assert normalize(seq.blocks) == seq.blocks
    # Four stations, three transitions: 20.5 s of commanded time, like the mock.
    assert seq.name == "四方位拍摄"


def test_pose_joints_follow_the_arm(tmp_path):
    # Gripper off the bus: a pose carrying it would fail at move time, so the
    # demo drops it. With the gripper present, the offset comes along.
    no_grip = demo_poses(["joint1", "joint2"])
    assert no_grip[0].joints == {"joint1": 0.0, "joint2": 0.35}
    with_grip = demo_poses(["joint1", "gripper"])
    assert with_grip[0].joints == {"joint1": 0.0, "gripper": 0.02}


def test_seeded_documents_round_trip(tmp_path):
    poses, sequences, templates = _stores(tmp_path)
    seed_demo_if_empty(poses, sequences, templates)
    # Re-read from disk, not from memory: what a restart serves.
    seq = sequences.get("demo-seq-four")
    assert seq.name == "四方位拍摄"
    tpl = templates.get("demo-tpl-four")
    assert tpl.station_count == 4 and tpl.name == "四方位"
