"""normalize(): the Python port of frontend/src/timeline/model.ts.

The cases here mirror the TS implementation's rules one by one — holds keep
identity, transitions are rebuilt between different poses, parameters are
inherited across a delete, orphans drop. If this module and model.ts ever
disagree, the UI normalizes one way and the backend stores another.
"""

from backend.sequences import (
    DEFAULT_TRANSITION_S,
    EventMarker,
    HoldBlock,
    Sequence,
    TransitionBlock,
    normalize,
    sequence_duration,
)


def hold(pose_id: str, duration_s: float = 3.0, markers=()) -> HoldBlock:
    return HoldBlock(pose_id=pose_id, duration_s=duration_s, markers=list(markers))


def trans(duration_s: float = 2.0, easing: str = "ease_in_out", markers=()) -> TransitionBlock:
    return TransitionBlock(duration_s=duration_s, easing=easing, markers=list(markers))


def kinds(blocks) -> list[str]:
    return [b.type for b in blocks]


def test_empty_and_single_hold_pass_through():
    assert normalize([]) == []
    only = hold("a")
    assert normalize([only]) == [only]


def test_a_transition_is_generated_between_two_different_poses():
    out = normalize([hold("a"), hold("b")])
    assert kinds(out) == ["hold", "transition", "hold"]
    generated = out[1]
    assert generated.duration_s == DEFAULT_TRANSITION_S
    assert generated.easing == "ease_in_out"


def test_no_transition_between_two_holds_of_the_same_pose():
    """Stopping halfway to take one more frame is not a move."""
    out = normalize([hold("a"), hold("a")])
    assert kinds(out) == ["hold", "hold"]


def test_holds_keep_identity_order_duration_and_markers():
    marker = EventMarker(kind="shutter", params={"count": 1}, at=2.0)
    a = hold("a", 5.0, [marker])
    b = hold("b", 1.0)
    out = normalize([a, trans(9.9), b])
    assert out[0] is a
    assert out[2] is b
    assert out[0].markers[0].id == marker.id


def test_an_existing_transitions_parameters_survive_a_noop_normalize():
    marker = EventMarker(kind="fill_light", params={}, at=0.4)
    out = normalize([hold("a"), trans(4.5, "linear", [marker]), hold("b")])
    kept = out[1]
    assert kept.duration_s == 4.5
    assert kept.easing == "linear"
    assert [m.id for m in kept.markers] == [marker.id]


def test_deleting_the_middle_hold_reconnects_the_flanks_with_the_old_parameters():
    """The hold between two stations is deleted and the flanks join directly —
    the recreated transition inherits the parameters of the one that used to
    link the same pair."""
    # Deleting the hold leaves its flanking transitions in the list; the pair
    # memory is built from them before the rebuild.
    before = [hold("a"), trans(7.0, "ease_in"), hold("b"), trans(), hold("c")]
    del before[2]
    out = normalize(before)
    assert kinds(out) == ["hold", "transition", "hold"]
    assert out[1].duration_s == 7.0
    assert out[1].easing == "ease_in"

    # With no transition left between the pair, the fresh one is the default.
    before = normalize([hold("a"), trans(7.0, "ease_in"), hold("b")])
    out = normalize([before[0], before[2]])
    assert out[1].duration_s == DEFAULT_TRANSITION_S


def test_the_way_back_inherits_the_same_parameters():
    """pairKey is direction-blind: a→b and b→a are the same road."""
    out = normalize([hold("a"), trans(6.0, "ease_out"), hold("b"), hold("a")])
    assert out[3].duration_s == 6.0
    assert out[3].easing == "ease_out"


def test_leading_trailing_and_orphaned_transitions_are_dropped():
    out = normalize([trans(), hold("a"), trans()])
    assert kinds(out) == ["hold"]


def test_stacked_transitions_between_two_holds_collapse_to_one():
    out = normalize([hold("a"), trans(1.0), trans(5.0), hold("b")])
    assert kinds(out) == ["hold", "transition", "hold"]
    assert out[1].duration_s == 1.0, "the first transition seen for a pair wins"


def test_inherited_markers_are_copies_not_aliases():
    marker = EventMarker(kind="fill_light", params={}, at=0.5)
    out = normalize([hold("a"), trans(2.0, markers=[marker]), hold("b"), hold("a")])
    inherited = out[3].markers[0]
    assert inherited.id == marker.id
    assert inherited is not marker


def test_sequence_duration_is_the_plan_ruler():
    blocks = normalize([
        hold("a", 3.0, [EventMarker(kind="wait", at=1.0, estimate_s=0.0)]),
        hold("b", 5.0),
    ])
    assert sequence_duration(blocks) == 10.0
    assert sequence_duration([]) == 0.0


def test_normalize_is_what_the_summary_counts():
    sequence = Sequence(name="x", blocks=normalize([hold("a", 2.0), hold("b", 4.0)]))
    from backend.sequences import SequenceSummary

    summary = SequenceSummary.of(sequence)
    assert summary.station_count == 2
    assert summary.duration_s == 2.0 + DEFAULT_TRANSITION_S + 4.0
