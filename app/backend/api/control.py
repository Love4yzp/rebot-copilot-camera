"""Control state, execution control, teaching and state/event websockets.

Everything here that moves the arm carries the motion gate. ``execute/stop``
does not: stopping must work while stopped, and while the emergency stop is
engaged.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from pydantic import BaseModel, Field

from ..core import Broadcaster, Controller, events
from ..core.controller import progress_payload
from .gate import require_arm_available

log = logging.getLogger(__name__)

router = APIRouter(tags=["control"])


def _controller(request: Request) -> Controller:
    return request.app.state.controller


class PlaybackState(BaseModel):
    mode: str
    #: Exclusive activity (idle/teach/playback/rest/safelock). ``mode`` is
    #: this, or ``estop`` when the latch is engaged.
    activity: str
    playing: bool
    teaching: bool
    rate_hz: float
    playback: dict | None = None
    #: Who asked for the running sequence. See Controller.play.
    source: str | None = None


class TriggerRequest(BaseModel):
    source: str = Field(
        default="ui",
        max_length=64,
        description=(
            "Who is triggering this: the UI, an agent, a foot switch, a shot-list "
            "script. Recorded and broadcast so that 'why did the arm move' has an "
            "answer; it grants nothing and changes no motion."
        ),
    )


def playback_state(controller: Controller) -> PlaybackState:
    """The PlaybackState the motion endpoints answer with."""
    executor = controller.executor
    return PlaybackState(
        mode=controller.mode,
        activity=controller.activity.value,
        playing=controller.is_playing,
        teaching=controller.is_teaching,
        rate_hz=controller.rate_hz,
        playback=progress_payload(executor.progress()) if executor else None,
        source=controller.playback_source if executor else None,
    )


@router.get("/api/control", response_model=PlaybackState)
def get_control_state(request: Request) -> PlaybackState:
    return playback_state(_controller(request))


# ── execution control ────────────────────────────────────────────────────────


@router.post("/api/execute/stop", response_model=PlaybackState)
def stop_execution(request: Request) -> PlaybackState:
    """Stop the run. Not gated: stopping must work while stopped."""
    _controller(request).stop_playback()
    return playback_state(_controller(request))


@router.post(
    "/api/execute/resume",
    response_model=PlaybackState,
    dependencies=[Depends(require_arm_available)],
)
def resume_execution(request: Request) -> PlaybackState:
    """Continue past the wait marker the run is suspended on.

    Gated: resuming is motion. A stop engaged during the wait already aborted
    the run, so by the time the gate passes there is usually nothing to resume.
    """
    if not _controller(request).resume():
        raise HTTPException(status.HTTP_409_CONFLICT, "no wait marker to resume from")
    return playback_state(_controller(request))


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
    return playback_state(_controller(request))


# ── rest ─────────────────────────────────────────────────────────────────────


class RestRequest(BaseModel):
    enabled: bool


@router.post(
    "/api/rest", response_model=PlaybackState, dependencies=[Depends(require_arm_available)]
)
def set_resting(body: RestRequest, request: Request) -> PlaybackState:
    """Rest: drop torque at the zero pose — the arm lies on its stops and the
    motors stop burning current. Gated: resting changes what the motors are
    commanded, and waking re-asserts a hold."""
    try:
        _controller(request).set_resting(body.enabled)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    return playback_state(_controller(request))


# ── websocket ────────────────────────────────────────────────────────────────


@router.websocket("/ws")
async def state_socket(websocket: WebSocket) -> None:
    """Stream control-loop state.

    Read-only. Commands go over REST so that the motion gate applies to them --
    a websocket message would bypass it.
    """
    await _stream(websocket, topics={"state", "playback"})


@router.websocket("/api/events")
async def event_socket(websocket: WebSocket) -> None:
    """Stream semantic events: arrivals, sequence lifecycle and stops.

    Separate from ``/ws`` because the two answer different questions. A screen
    wants joint angles at 20 Hz; a process that files photographs or drives a
    light board wants to be told a frame was taken, and should not have to eat
    a position stream over a studio LAN to find out.

    Read-only and non-negotiable, like ``/ws``: an event is a notification, not
    a hook. Nothing a subscriber sends back can change what the sequence does,
    because a subscriber that could refuse would be third-party code in the
    path that decides whether the arm moves.
    """
    await _stream(websocket, topics={events.TOPIC}, unwrap=True)


async def _stream(websocket: WebSocket, topics: set[str], unwrap: bool = False) -> None:
    broadcaster: Broadcaster = websocket.app.state.broadcaster
    await websocket.accept()

    sub = broadcaster.subscribe(asyncio.get_running_loop(), topics=topics)
    try:
        while True:
            message = await sub.get()
            # Event subscribers get the payload without the broadcaster's
            # envelope: they asked for one kind of message, so a "type" field
            # that is always the same is noise a third-party client must skip.
            await websocket.send_json(message["data"] if unwrap else message)
            controller = getattr(websocket.app.state, "controller", None)
            if controller is not None:
                controller.note_client()
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("websocket stream failed")
    finally:
        broadcaster.unsubscribe(sub)
