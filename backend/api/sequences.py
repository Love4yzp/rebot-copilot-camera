"""Sequence CRUD and execution.

Sequences are pure data until ``execute`` — creating, renaming, patching and
deleting move nothing, so those routes carry no motion gate. The exceptions
are structural: a run holds a claim on its sequence (TIMELINE rule 5 — the
executor is consuming the block list, so it cannot change underfoot), which is
a 409, not a gate.

Write-side normalization is the contract: transitions are automatic and
undeletable, so they are rebuilt here on every blocks PATCH rather than
trusted from the client — the same function the UI ran before sending.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from ..actions import validate_marker_params, validate_providers
from ..core import Controller
from ..safety.kinematics import validate_sequence
from ..sequences import (
    Block,
    HoldBlock,
    Pose,
    PoseNotFound,
    PoseStore,
    Sequence,
    SequenceNotFound,
    SequenceStore,
    SequenceSummary,
    normalize,
)
from .control import PlaybackState, TriggerRequest, playback_state
from .gate import require_arm_available

router = APIRouter(prefix="/api/sequences", tags=["sequences"])


def _store(request: Request) -> SequenceStore:
    return request.app.state.sequence_store


def _poses(request: Request) -> PoseStore:
    return request.app.state.pose_store


def _controller(request: Request) -> Controller:
    return request.app.state.controller


def _load(request: Request, sid: str) -> Sequence:
    try:
        return _store(request).get(sid)
    except SequenceNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no sequence {sid!r}") from None


def _is_executing(request: Request, sid: str) -> bool:
    """A live run holds a structural claim on its sequence — TIMELINE rule 5."""
    controller = _controller(request)
    return controller.is_playing and controller.playback_sequence_id == sid


def _resolve_poses(request: Request, sequence: Sequence) -> dict[str, Pose]:
    """Read every hold's pose out of the library, here at the API boundary —
    the executor never touches a store."""
    store = _poses(request)
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


class CreateSequence(BaseModel):
    name: str = ""


class PatchSequence(BaseModel):
    """Every field optional — omitted means unchanged. ``blocks`` is a
    whole-document replace; the server normalizes before storing."""

    name: str | None = None
    blocks: list[Block] | None = None


@router.get("", response_model=list[SequenceSummary])
def list_sequences(request: Request) -> list[SequenceSummary]:
    return _store(request).list()


@router.post("", response_model=Sequence, status_code=status.HTTP_201_CREATED)
def create_sequence(body: CreateSequence, request: Request) -> Sequence:
    name = body.name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "name must be at least 1 character")
    return _store(request).save(Sequence(name=name))


@router.get("/{sid}", response_model=Sequence)
def get_sequence(sid: str, request: Request) -> Sequence:
    return _load(request, sid)


@router.patch("/{sid}", response_model=Sequence)
def patch_sequence(sid: str, body: PatchSequence, request: Request) -> Sequence:
    sequence = _load(request, sid)
    if body.blocks is not None:
        # The executor is consuming this structure block by block — changing
        # it under a live run is the lockout the timeline overlay enforces.
        if _is_executing(request, sid):
            raise HTTPException(
                status.HTTP_409_CONFLICT, "sequence is executing; stop it before editing"
            )
        # Write-side normalization: transitions are automatic and undeletable,
        # so they are rebuilt here rather than trusted from the client.
        blocks = normalize(body.blocks)
        # And the same for marker params, against each provider's own model: a
        # bad param found now is a typo; found mid-run it is an aborted shoot.
        bad = validate_marker_params(blocks, request.app.state.plugins)
        if bad:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, {"error": "bad_marker_params", "reasons": bad}
            )
        sequence.blocks = blocks
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "name must be at least 1 character")
        sequence.name = name
    sequence.touch()
    return _store(request).save(sequence)


@router.delete("/{sid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_sequence(sid: str, request: Request) -> None:
    if _is_executing(request, sid):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "sequence is executing; stop it before deleting"
        )
    try:
        _store(request).delete(sid)
    except SequenceNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no sequence {sid!r}") from None


@router.post(
    "/{sid}/execute",
    response_model=PlaybackState,
    dependencies=[Depends(require_arm_available)],
)
def execute_sequence(
    sid: str, request: Request, body: TriggerRequest | None = None
) -> PlaybackState:
    """Run the sequence for real — the arm moves."""
    sequence = _load(request, sid)
    if not sequence.blocks:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "sequence has no blocks")

    poses = _resolve_poses(request, sequence)

    # Pre-flight the whole sequence, including the straight lines between
    # consecutive poses. Two legal poses can have an illegal path between
    # them, and discovering that by watching the arm reach it is the expensive
    # way to find out. Nothing has moved yet at this point.
    joints_in_order = [poses[b.pose_id].joints for b in sequence.blocks if isinstance(b, HoldBlock)]
    unsafe = validate_sequence(joints_in_order)
    if unsafe:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, {"error": "unsafe_sequence", "reasons": unsafe}
        )

    # And pre-flight the markers the same way. A sequence outlives the plugin
    # it was written against — packages get uninstalled, boards get unplugged —
    # and an unavailable provider means walking the whole set to deliver
    # nothing, which is the failure the abort-by-default policy exists for.
    missing = validate_providers(sequence.blocks, request.app.state.plugins)
    if missing:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, {"error": "missing_providers", "reasons": missing}
        )

    controller = _controller(request)
    try:
        controller.play(sequence, poses, source=(body or TriggerRequest()).source)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    return playback_state(controller)
