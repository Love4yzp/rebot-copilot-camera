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

from ..core import Controller
from ..sequences import (
    Block,
    Sequence,
    SequenceNotFound,
    SequenceStore,
    SequenceSummary,
    normalize,
)
from .control import PlaybackState, TriggerRequest, playback_state
from .gate import require_arm_available
from .preflight import preflight_play, resolve_poses

router = APIRouter(prefix="/api/sequences", tags=["sequences"])


def _store(request: Request) -> SequenceStore:
    return request.app.state.sequence_store


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
        bad = _controller(request).preflight_marker_params(blocks, request.app.state.plugins)
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

    poses = resolve_poses(request, sequence)
    controller = _controller(request)

    # Pre-flight the whole sequence, including the approach from wherever the
    # arm is now — limits, self-collision along the straight-line paths, and
    # every marker's provider. Nothing has moved yet at this point.
    problems = preflight_play(request, sequence, poses)
    if problems["unsafe"]:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, {"error": "unsafe_sequence", "reasons": problems["unsafe"]}
        )
    if problems["missing"]:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, {"error": "missing_providers", "reasons": problems["missing"]}
        )

    try:
        controller.play(sequence, poses, source=(body or TriggerRequest()).source)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    return playback_state(controller)
