"""Motion endpoints and the state websocket.

Everything here that moves the arm carries the motion gate. Capture does not:
it reads the arm's current pose and writes a record, which is safe -- and
useful -- while the stop is engaged.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field

from ..core import Broadcaster, Controller
from ..routines import Routine, RoutineNotFound, RoutineStore, Waypoint
from ..safety.kinematics import validate_pose, validate_sequence
from .gate import require_arm_available

log = logging.getLogger(__name__)

router = APIRouter(tags=["control"])


def _controller(request: Request) -> Controller:
    return request.app.state.controller


def _store(request: Request) -> RoutineStore:
    return request.app.state.routine_store


def _load(request: Request, rid: str) -> Routine:
    try:
        return _store(request).get(rid)
    except RoutineNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no routine {rid!r}") from None


class PlaybackState(BaseModel):
    mode: str
    playing: bool
    teaching: bool
    rate_hz: float
    playback: dict | None = None


def _state(controller: Controller) -> PlaybackState:
    from ..core.controller import _progress_payload

    executor = controller.executor
    return PlaybackState(
        mode=controller.mode,
        playing=controller.is_playing,
        teaching=controller.is_teaching,
        rate_hz=controller.rate_hz,
        playback=_progress_payload(executor.progress()) if executor else None,
    )


@router.get("/api/control", response_model=PlaybackState)
def get_control_state(request: Request) -> PlaybackState:
    return _state(_controller(request))


# ── playback ─────────────────────────────────────────────────────────────────


@router.post(
    "/api/routines/{rid}/play",
    response_model=PlaybackState,
    dependencies=[Depends(require_arm_available)],
)
def play_routine(rid: str, request: Request) -> PlaybackState:
    routine = _load(request, rid)
    if not routine.waypoints:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "routine has no waypoints")

    # Pre-flight the whole sequence, including the straight lines between
    # consecutive poses. Two legal waypoints can have an illegal path between
    # them, and discovering that by watching the arm reach it is the expensive
    # way to find out. Nothing has moved yet at this point.
    unsafe = validate_sequence([w.joints for w in routine.waypoints])
    if unsafe:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, {"error": "unsafe_routine", "reasons": unsafe}
        )

    try:
        _controller(request).play(routine)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    return _state(_controller(request))


@router.post("/api/playback/stop", response_model=PlaybackState)
def stop_playback(request: Request) -> PlaybackState:
    """Stop the routine. Not gated: stopping must work while stopped."""
    _controller(request).stop_playback()
    return _state(_controller(request))


# ── teaching ─────────────────────────────────────────────────────────────────


class TeachRequest(BaseModel):
    enabled: bool


@router.post(
    "/api/teach", response_model=PlaybackState, dependencies=[Depends(require_arm_available)]
)
def set_teaching(body: TeachRequest, request: Request) -> PlaybackState:
    try:
        _controller(request).set_teaching(body.enabled)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    return _state(_controller(request))


class CaptureRequest(BaseModel):
    settle_ms: int | None = None
    duration_s: float | None = None
    note: str = ""
    index: int | None = Field(default=None, description="Insert position; appends when omitted.")


@router.post("/api/routines/{rid}/waypoints/capture", response_model=Routine, status_code=201)
def capture_waypoint(rid: str, request: Request, body: CaptureRequest | None = None) -> Routine:
    """Record the arm's current pose as a waypoint.

    This is the "press the button" half of drag teaching: the operator has
    already positioned the arm by hand and let go.

    Not behind the motion gate. It reads a pose and writes a record; doing that
    while the stop is engaged is harmless, and an operator who has just stopped
    the arm may well want the pose it stopped at.
    """
    body = body or CaptureRequest()
    routine = _load(request, rid)
    controller = _controller(request)

    fields = body.model_dump(exclude_none=True, exclude={"index"})
    pose = dict(controller.arm.read_state().positions)
    waypoint = Waypoint(joints=pose, **fields)

    # Deliberately a warning, not a rejection. The arm is physically at this
    # pose; refusing to record where it actually is would be absurd. But if the
    # model calls it unsafe, the model and reality disagree -- a bad URDF, a
    # miscalibrated zero -- and that is worth saying out loud before playback
    # trusts the same model to pre-flight the routine.
    unsafe = validate_pose(pose)
    if unsafe:
        log.warning("captured a pose the model considers unsafe: %s", "; ".join(unsafe))

    if body.index is None:
        routine.waypoints.append(waypoint)
    elif 0 <= body.index <= len(routine.waypoints):
        routine.waypoints.insert(body.index, waypoint)
    else:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"insert index {body.index} out of range")

    routine.touch()
    return _store(request).save(routine)


# ── websocket ────────────────────────────────────────────────────────────────


@router.websocket("/ws")
async def state_socket(websocket: WebSocket) -> None:
    """Stream control-loop state.

    Read-only. Commands go over REST so that the motion gate applies to them --
    a websocket message would bypass it.
    """
    broadcaster: Broadcaster = websocket.app.state.broadcaster
    await websocket.accept()

    sub = broadcaster.subscribe(asyncio.get_running_loop())
    try:
        while True:
            await websocket.send_json(await sub.get())
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("websocket stream failed")
    finally:
        broadcaster.unsubscribe(sub)
