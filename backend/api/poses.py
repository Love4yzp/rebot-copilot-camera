"""The pose library: named poses, capture from the arm, and single-pose goto.

Poses are pure data — creating, renaming and deleting them moves nothing, so
those routes are not gated (they are declared non-motion in
``tests/test_motion_gate.py``, the deliberate choice the coverage test
forces). Capture is not gated either: it reads the arm's current pose and
writes a record, which is safe — and useful — while the stop is engaged.

Goto is the one that moves the arm, and it carries the gate.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ValidationError

from ..core import Controller, events
from ..safety.kinematics import validate_pose, validate_sequence
from ..sequences import Pose, PoseNotFound, PoseStore, SequenceStore
from .control import PlaybackState, TriggerRequest, playback_state
from .gate import require_arm_available

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/poses", tags=["poses"])


def _store(request: Request) -> PoseStore:
    return request.app.state.pose_store


def _sequences(request: Request) -> SequenceStore:
    return request.app.state.sequence_store


def _controller(request: Request) -> Controller:
    return request.app.state.controller


def _load(request: Request, pose_id: str) -> Pose:
    try:
        return _store(request).get(pose_id)
    except PoseNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no pose {pose_id!r}") from None


def _checked_joints(joints: dict[str, float]) -> dict[str, float]:
    """Shape first (422), then this arm's limits and collision model (400)."""
    unsafe = validate_pose(joints)
    if unsafe:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, {"error": "unsafe_pose", "reasons": unsafe})
    return joints


class CreatePose(BaseModel):
    name: str = ""
    joints: dict[str, float] | None = None


class CapturePose(BaseModel):
    name: str = ""


class PatchPose(BaseModel):
    """Every field optional — omitted means unchanged."""

    name: str | None = None
    joints: dict[str, float] | None = None


@router.get("", response_model=list[Pose])
def list_poses(request: Request) -> list[Pose]:
    return _store(request).list()


@router.post("", response_model=Pose, status_code=status.HTTP_201_CREATED)
def create_pose(body: CreatePose, request: Request) -> Pose:
    name = body.name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "name must be at least 1 character")
    if body.joints is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "a pose needs joint angles")
    try:
        pose = Pose(name=name, joints=body.joints)
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, exc.errors(include_url=False)
        ) from None
    _checked_joints(pose.joints)
    return _store(request).save(pose)


@router.post("/capture", response_model=Pose, status_code=status.HTTP_201_CREATED)
def capture_pose(request: Request, body: CapturePose | None = None) -> Pose:
    """Record wherever the arm is standing right now, under a name.

    This is the "press the button" half of drag teaching: the operator has
    already positioned the arm by hand and let go. Not behind the motion gate —
    it reads a pose and writes a record, and an operator who has just stopped
    the arm may well want the pose it stopped at.
    """
    body = body or CapturePose()
    name = body.name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "name must be at least 1 character")
    controller = _controller(request)
    joints = dict(controller.arm.read_state().positions)

    # Deliberately a warning, not a rejection. The arm is physically at this
    # pose; refusing to record where it actually is would be absurd. But if the
    # model calls it unsafe, the model and reality disagree — a bad URDF, a
    # miscalibrated zero — and that is worth saying out loud before playback
    # trusts the same model to pre-flight the sequence.
    unsafe = validate_pose(joints)
    if unsafe:
        log.warning("captured a pose the model considers unsafe: %s", "; ".join(unsafe))

    pose = _store(request).save(Pose(name=name, joints=joints))
    controller.emit_event(events.TEACH_CAPTURED, {"pose_id": pose.id, "pose_name": pose.name})
    return pose


@router.patch("/{pose_id}", response_model=Pose)
def patch_pose(pose_id: str, body: PatchPose, request: Request) -> Pose:
    pose = _load(request, pose_id)
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "name must be at least 1 character")
        pose.name = name
    if body.joints is not None:
        # Re-validate through the model, then the arm's limits — a PATCH must
        # not write a NaN angle or an unreachable pose straight to disk.
        try:
            pose.joints = Pose.model_validate(
                {**pose.model_dump(), "joints": body.joints}
            ).joints
        except ValidationError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, exc.errors(include_url=False)
            ) from None
        _checked_joints(pose.joints)
    pose.touch()
    return _store(request).save(pose)


@router.delete("/{pose_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_pose(pose_id: str, request: Request) -> None:
    """Delete directly: telling the operator what this pose feeds first is the
    UI's job (it asked GET links before offering the button)."""
    try:
        _store(request).delete(pose_id)
    except PoseNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no pose {pose_id!r}") from None


@router.get("/{pose_id}/links")
def pose_links(pose_id: str, request: Request) -> dict:
    """Which sequences link this pose, reported before delete/overwrite —
    silently rewriting the physical path of N sequences is the "a whole round
    of empty frames" class of failure."""
    pose = _load(request, pose_id)
    links = [
        {
            "sequence_id": sequence.id,
            "sequence_name": sequence.name,
            "block_count": sum(
                1 for b in sequence.blocks if b.type == "hold" and b.pose_id == pose.id
            ),
        }
        for sequence in _sequences(request).list_full()
    ]
    links = [link for link in links if link["block_count"] > 0]
    return {"pose_id": pose.id, "count": len(links), "links": links}


@router.post(
    "/{pose_id}/goto",
    response_model=PlaybackState,
    dependencies=[Depends(require_arm_available)],
)
def goto_pose(
    pose_id: str, request: Request, body: TriggerRequest | None = None
) -> PlaybackState:
    """Move to one pose and stay there — the library card's "去这里"."""
    pose = _load(request, pose_id)
    controller = _controller(request)

    # Pre-flight the path from wherever the arm is now. Two legal poses can
    # have an illegal line between them; nothing has moved yet at this point.
    current = dict(controller.arm.read_state().positions)
    unsafe = validate_sequence([current, pose.joints])
    if unsafe:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, {"error": "unsafe_path", "reasons": unsafe}
        )

    try:
        controller.goto(pose, source=(body or TriggerRequest()).source)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    return playback_state(controller)
