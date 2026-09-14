"""Resolve stored poses and route all motion validation through Controller."""

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


def preflight_play(
    request: Request, sequence: Sequence, poses: dict[str, Pose]
) -> dict[str, list[str]]:
    """Everything checked before playback starts; nothing has moved yet.

    Includes the approach from wherever the arm is now: two legal poses can
    have an illegal path between them, and discovering that by watching the
    arm reach it is the expensive way to find out.
    """
    controller = request.app.state.controller
    current = dict(controller.arm.read_state().positions)
    joints_in_order = [poses[b.pose_id].joints for b in sequence.blocks if isinstance(b, HoldBlock)]
    unsafe = controller.preflight_path([current, *joints_in_order])
    return {"unsafe": unsafe}
