"""Control state, execution control, teaching, the state websocket, shutter.

Everything here that moves the arm carries the motion gate. ``execute/stop``
does not: stopping must work while stopped, and while the emergency stop is
engaged.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field

from ..core import Broadcaster, Controller, events
from ..core.controller import progress_payload
from ..shutter import (
    CAMERA_STATUS_DISCONNECTED,
    CAMERA_STATUS_UNPAIRED,
    PAIR_SMART_TIMEOUT_S,
    PAIR_TIMEOUT_S,
    ShutterError,
)
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
    """Stream semantic events: arrivals, actions, stops.

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
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("websocket stream failed")
    finally:
        broadcaster.unsubscribe(sub)


# ── shutter self-test ────────────────────────────────────────────────────────


class ShutterTestResult(BaseModel):
    ok: bool
    #: The USB link to the board. Says nothing about the camera.
    connected: bool
    #: The BLE link from the board to the camera — the one that decides whether
    #: a frame is actually taken. None when the board could not be asked.
    camera: bool | None = None
    fired: bool
    firmware_version: str | None = None
    error: str | None = None


@router.post("/api/shutter/test", response_model=ShutterTestResult)
def test_shutter(request: Request, focus: bool = False, shoot: bool = False) -> ShutterTestResult:
    """Check the host-to-ESP32-to-camera chain.

    Pings by default and only fires when asked, so it can be used to confirm
    the link without burning a frame. Run this when setting up on site: a dead
    BLE link is silent until the arm has walked a whole set with nothing
    landing on the card.

    **Both links are checked.** ``ping`` deliberately answers only for the USB
    cable — the firmware does not touch the camera for it, so that a sleeping
    camera stays distinguishable from a missing board. Checking only that would
    make this endpoint answer green on a machine with nothing paired, which is
    the failure it exists to catch.

    **Three states, not two.** A camera that was never paired needs a human
    with the camera's Bluetooth menu; one that is paired but disconnected
    (sleeping, just booted) resolves itself when the next frame tries to fire.
    The endpoint sends a ``FOCUS`` (half-press, no frame burned) to force a
    lazy BLE connect when it sees ``disconnected``, so the only case that
    reports red is genuinely unreachable.

    Not behind the motion gate — it moves no joints, and confirming the shutter
    while the arm is safely stopped is a reasonable thing to want.
    """
    shutter = _controller(request).shutter
    camera: bool | None = None

    try:
        shutter.ping()
        status = shutter.camera_status()

        if status == CAMERA_STATUS_UNPAIRED:
            camera = False
        elif status == CAMERA_STATUS_DISCONNECTED:
            # Force a lazy BLE connect. No frame is burned — the camera only
            # fires on SHOOT, and FOCUS is a half-press the firmware handles
            # without telling the camera to take a picture.
            shutter.focus()
            camera = shutter.camera_connected()
        else:
            camera = True

        if focus:
            shutter.focus()
        if shoot:
            shutter.shoot()
    except ShutterError as exc:
        return ShutterTestResult(
            ok=False,
            connected=shutter.is_connected,
            camera=camera,
            fired=False,
            firmware_version=getattr(shutter, "firmware_version", None),
            error=str(exc),
        )

    return ShutterTestResult(
        ok=bool(camera),
        connected=shutter.is_connected,
        camera=camera,
        fired=shoot,
        firmware_version=getattr(shutter, "firmware_version", None),
        error=None if camera else "no camera is paired — pair from the settings",
    )


@router.post("/api/shutter/pair", response_model=ShutterTestResult)
def pair_shutter(request: Request, timeout_s: float = PAIR_TIMEOUT_S) -> ShutterTestResult:
    """Put the board into BLE pairing mode and wait for the camera.

    The one operation here that needs a person: the camera has to be put into
    its own pairing mode by hand (**无线通信设置 > 蓝牙功能 > 遥控**), which is
    why the wait is thirty seconds rather than a few. Without this endpoint the
    only way to attach a camera was a serial terminal, so a machine whose board
    had reset — which drops the pairing — could not be recovered from the
    screen that was reporting the problem.

    Refused while a sequence is executing: the driver takes one command at a
    time, so a pairing scan would stall the frames behind it, and re-pairing
    mid-shoot is not a thing anyone means to do.

    Not behind the motion gate for the same reason the self-test is not: no
    joint moves, and this is exactly what an operator does while the arm is
    stopped.
    """
    controller = _controller(request)
    if controller.is_playing:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "cannot pair the camera while a sequence is executing"
        )

    shutter = controller.shutter
    try:
        shutter.pair(timeout_s)
    except ShutterError as exc:
        return ShutterTestResult(
            ok=False,
            connected=shutter.is_connected,
            camera=False,
            fired=False,
            firmware_version=getattr(shutter, "firmware_version", None),
            error=str(exc),
        )

    camera = shutter.camera_connected()
    return ShutterTestResult(
        ok=camera,
        connected=shutter.is_connected,
        camera=camera,
        fired=False,
        firmware_version=getattr(shutter, "firmware_version", None),
        error=None if camera else "pairing finished but the camera is not connected",
    )


@router.post("/api/shutter/pair_smart", response_model=ShutterTestResult)
def pair_shutter_smart(request: Request, timeout_s: float = PAIR_SMART_TIMEOUT_S) -> ShutterTestResult:
    """Put the board into smartphone-mode pairing.

    The camera must be in "connect to smartphone" mode (not "remote" mode).
    The user must confirm on the camera's screen within 60 s after the
    identification handshake.

    Refused while a sequence is executing, same as the BLE remote pair endpoint.
    """
    controller = _controller(request)
    if controller.is_playing:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "cannot pair the camera while a sequence is executing"
        )

    shutter = controller.shutter
    try:
        shutter.pair_smart(timeout_s)
    except ShutterError as exc:
        return ShutterTestResult(
            ok=False,
            connected=shutter.is_connected,
            camera=False,
            fired=False,
            firmware_version=getattr(shutter, "firmware_version", None),
            error=str(exc),
        )

    camera = shutter.camera_connected()
    return ShutterTestResult(
        ok=camera,
        connected=shutter.is_connected,
        camera=camera,
        fired=False,
        firmware_version=getattr(shutter, "firmware_version", None),
        error=None if camera else "smart pairing finished but the camera is not connected",
    )
