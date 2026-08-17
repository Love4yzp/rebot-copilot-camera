"""Normalization and plan-ruler time: the pure logic of the block/marker world.

A rule-for-rule Python port of ``frontend/src/timeline/model.ts``, which is the
authoritative implementation — the React UI and the dev mock share it, and this
module exists so the backend applies the *same* physical rules when a sequence
is stored. Everything here is a pure function.

This is how "transitions are automatic and undeletable" is implemented: not as
an editing restriction but as a normalization that runs after every change (in
the UI before PATCH, and again here on write).
"""

from __future__ import annotations

from .models import (
    DEFAULT_EASING,
    DEFAULT_TRANSITION_S,
    Block,
    HoldBlock,
    TransitionBlock,
    _new_id,
)


def normalize(blocks: list[Block]) -> list[Block]:
    """Rebuild a block list so the physical rules hold after any edit.

    - holds keep their identity, order, duration and markers
    - between two adjacent holds of *different* poses there is exactly one
      transition — the arm must physically get there, that is not a setting
    - between two adjacent holds of the *same* pose there is none — that is
      "stop halfway and take one more frame", not a move
    - a recreated transition inherits the previous transition's parameters
      for the same pose pair when one exists (e.g. the hold between two
      stations was deleted and the two flanks now join directly)
    - transitions anywhere else (leading, trailing, orphaned) are dropped
    """
    # Pass 1: remember every existing transition by the pose pair it links, so
    # a rebuilt transition can inherit the old one's duration/easing/markers.
    memory: dict[str, TransitionBlock] = {}
    holds: list[HoldBlock] = []
    for i, block in enumerate(blocks):
        if isinstance(block, HoldBlock):
            holds.append(block)
            continue
        prev = nearest_hold(blocks, i, -1)
        next_ = nearest_hold(blocks, i, +1)
        if prev is not None and next_ is not None:
            key = pair_key(prev.pose_id, next_.pose_id)
            if key not in memory:
                memory[key] = block

    # Pass 2: lay holds down and fill the gaps from memory.
    out: list[Block] = []
    used: set[str] = set()
    for i, hold in enumerate(holds):
        out.append(hold)
        if i >= len(holds) - 1:
            continue
        a, b = holds[i], holds[i + 1]
        if a.pose_id == b.pose_id:
            continue  # same pose adjacent: no transition
        key = pair_key(a.pose_id, b.pose_id)
        remembered = memory.get(key)
        if remembered is None:
            out.append(
                TransitionBlock(
                    duration_s=DEFAULT_TRANSITION_S,
                    easing=DEFAULT_EASING,  # type: ignore[arg-type]
                )
            )
        elif key in used:
            # The same pose pair can occur more than once in one sequence
            # (A→B→A→B): every rebuilt block needs its own identity. The first
            # occurrence keeps the remembered ids — a no-op normalize must not
            # move the inspector's selection — but later ones must be fresh, or
            # two blocks (and their markers) share an id and the timeline
            # silently drops or duplicates children.
            fresh = remembered.model_copy(deep=True)
            fresh.id = _new_id()
            for marker in fresh.markers:
                marker.id = _new_id()
            out.append(fresh)
        else:
            used.add(key)
            out.append(remembered.model_copy(deep=True))
    return out


def pair_key(pose_a: str, pose_b: str) -> str:
    """Direction does not matter for inheriting duration/easing: the way back
    is the same road."""
    return f"{pose_a}|{pose_b}" if pose_a < pose_b else f"{pose_b}|{pose_a}"


def nearest_hold(blocks: list[Block], from_index: int, step: int) -> HoldBlock | None:
    i = from_index + step
    while 0 <= i < len(blocks):
        block = blocks[i]
        if isinstance(block, HoldBlock):
            return block
        i += step
    return None


def sequence_duration(blocks: list[Block]) -> float:
    """The plan-ruler length: the sum of *commanded* durations. Markers add
    nothing — their durations are estimates, and a wait marker is open-ended,
    so the UI always labels this number 预估."""
    return sum(block.duration_s for block in blocks)
