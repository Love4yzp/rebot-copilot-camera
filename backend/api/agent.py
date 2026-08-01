"""Agent control endpoints.

The OpenAPI schema at /openapi.json imports directly as a tool definition, so
the descriptions here are written for a model reading them, not only for a
person browsing /docs.

Every motion endpoint carries the same emergency-stop gate as the UI. A latched
stop refuses an agent exactly as it refuses a person; holding the lease grants
control, not permission to move a stopped arm.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..agent import AgentLease, LeaseInfo
from ..routines import RoutineNotFound, RoutineStore
from ..safety.kinematics import validate_pose, validate_sequence
from .gate import require_arm_available

router = APIRouter(prefix="/api/agent", tags=["agent"])

#: Bounds on a single agent move. Not safety limits — those come from the URDF
#: — but a guard against a model emitting a plausible-looking number that would
#: swing the arm across the workspace in a tenth of a second.
MIN_DURATION_S = 0.2
MAX_DURATION_S = 30.0
MAX_DELTA_RAD = 1.5


def _lease(request: Request) -> AgentLease:
    return request.app.state.agent_lease


def _store(request: Request) -> RoutineStore:
    return request.app.state.routine_store


def require_lease(
    request: Request,
    x_agent_token: str | None = Header(default=None, alias="X-Agent-Token"),
) -> None:
    try:
        _lease(request).check(x_agent_token)
    except PermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from None


# ── lease ────────────────────────────────────────────────────────────────────


class AcquireRequest(BaseModel):
    owner: str = Field(
        default="agent",
        min_length=1,
        max_length=64,
        description="Who is taking control. Shown in the UI so a person can see who has the arm.",
    )


class AcquireResponse(BaseModel):
    token: str = Field(description="Send as the X-Agent-Token header on every command.")
    idle_timeout_s: float = Field(description="Lease lapses after this long with no commands.")


@router.get("", response_model=LeaseInfo)
def get_lease(request: Request) -> LeaseInfo:
    """Who holds control, and how long they have left."""
    return _lease(request).info()


@router.post("/acquire", response_model=AcquireResponse)
def acquire(body: AcquireRequest | None, request: Request) -> AcquireResponse:
    """Take exclusive control of the arm.

    409 if someone already holds it — this is not a queue. Two callers
    interleaving commands on one arm produces motion neither asked for.
    """
    lease = _lease(request)
    try:
        token = lease.acquire((body or AcquireRequest()).owner)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    return AcquireResponse(token=token, idle_timeout_s=lease._idle_ttl)


@router.post("/release", response_model=LeaseInfo)
def release(
    request: Request,
    force: bool = False,
    x_agent_token: str | None = Header(default=None, alias="X-Agent-Token"),
) -> LeaseInfo:
    """Give control back.

    ``force=true`` skips the token check, for the web UI to take the arm back.
    The person standing next to it outranks the process controlling it, and
    they will not have its token.
    """
    try:
        _lease(request).release(x_agent_token, force=force)
    except PermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from None
    return _lease(request).info()


# ── motion ───────────────────────────────────────────────────────────────────


class JointCommand(BaseModel):
    joints: dict[str, float] = Field(
        description="Target angles in radians, keyed by joint name (joint1..joint6, gripper).",
    )
    duration_s: float = Field(
        default=2.0,
        ge=MIN_DURATION_S,
        le=MAX_DURATION_S,
        description="How long the move should take.",
    )


@router.post(
    "/control/joints",
    dependencies=[Depends(require_arm_available), Depends(require_lease)],
)
def command_joints(body: JointCommand, request: Request) -> dict:
    """Move the arm to a joint configuration.

    Rejected with 400 if the pose violates the arm's joint limits, would put it
    in self-collision, or asks any joint to move more than 1.5 rad in one call.
    """
    controller = request.app.state.controller

    unsafe = validate_pose(body.joints)
    if unsafe:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, {"error": "unsafe_pose", "reasons": unsafe}
        )

    # A single enormous jump is almost always a model mistake rather than an
    # intention, and it is the one that hurts.
    current = controller.arm.read_state().positions
    excessive = {
        name: round(abs(current.get(name, target) - target), 3)
        for name, target in body.joints.items()
        if abs(current.get(name, target) - target) > MAX_DELTA_RAD
    }
    if excessive:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            {
                "error": "move_too_large",
                "message": f"no joint may move more than {MAX_DELTA_RAD} rad in one call",
                "joints": excessive,
            },
        )

    if controller.is_playing or controller.is_teaching:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"arm is busy: {controller.mode}"
        )

    controller.arm.move_to(body.joints, body.duration_s)
    return {"ok": True, "mode": controller.mode}


@router.post(
    "/control/play/{rid}",
    dependencies=[Depends(require_arm_available), Depends(require_lease)],
)
def play_routine(rid: str, request: Request) -> dict:
    """Play a stored routine, shutter actions included."""
    controller = request.app.state.controller

    try:
        routine = _store(request).get(rid)
    except RoutineNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no routine {rid!r}") from None

    if not routine.waypoints:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "routine has no waypoints")

    unsafe = validate_sequence([w.joints for w in routine.waypoints])
    if unsafe:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, {"error": "unsafe_routine", "reasons": unsafe}
        )

    try:
        controller.play(routine)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    return {"ok": True, "waypoints": len(routine.waypoints)}


@router.post("/control/stop", dependencies=[Depends(require_lease)])
def stop(request: Request) -> dict:
    """Stop whatever is playing. Not gated — stopping must work while stopped."""
    stopped = request.app.state.controller.stop_playback("stopped by agent")
    return {"ok": True, "was_playing": stopped}
