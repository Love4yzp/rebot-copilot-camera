"""Shared playback pre-flight: pose resolution and whole-run validation.

Both ``POST /api/sequences/{id}/execute`` and ``POST /api/agent/control/play``
run the same checks before the arm moves, so they share this instead of
drifting apart. The api layer's half of the layering rule lives here too: it
talks to the stores (reading) and the controller (every check), never to
safety/actions validators directly.
"""

from __future__ import annotations

from fastapi import HTTPException, Request, status

from ..sequences import HoldBlock, Pose, PoseNotFound, Sequence


def resolve_poses(request: Request, sequence: Sequence) -> dict[str, Pose]:
    """Read every hold's pose out of the library, here at the API boundary —
    the executor never touches a store."""
    store = request.app.state.pose_store
    poses: dict[str, Pose] = {}
    missing: list[str] = []
    for index, block in enumerate(sequence.blocks):
        if not isinstance(block, HoldBlock):
            continue
        try:
            poses[block.pose_id] = store.get(block.pose_id)
        except PoseNotFound:
            missing.append(f"block {index}: no pose {block.pose_id!r}")
    if missing:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, {"error": "missing_poses", "reasons": missing}
        )
    return poses


def preflight_play(request: Request, sequence: Sequence, poses: dict[str, Pose]) -> dict[str, list[str]]:
    """Everything checked before playback starts; nothing has moved yet.

    Includes the approach from wherever the arm is now: two legal poses can
    have an illegal path between them, and discovering that by watching the
    arm reach it is the expensive way to find out. Also checks that every
    marker's provider is installed and healthy — a sequence outlives the
    plugin it was written against, and walking a whole set to deliver nothing
    is the failure the abort-by-default policy exists for.
    """
    controller = request.app.state.controller
    current = dict(controller.arm.read_state().positions)
    joints_in_order = [poses[b.pose_id].joints for b in sequence.blocks if isinstance(b, HoldBlock)]
    unsafe = controller.preflight_path([current, *joints_in_order])
    missing = controller.preflight_providers(sequence.blocks, request.app.state.plugins)
    return {"unsafe": unsafe, "missing": missing}
