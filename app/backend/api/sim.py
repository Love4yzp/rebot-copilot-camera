"""Simulator-only drag push: the operator's hand, over HTTP.

The drag is a hand, not a command -- it deliberately bypasses the activity
table, and the motion gate is the only thing that outranks it. There is no
real-arm route for the same reason the walls of this room do not have a
passenger door on them: the real arm already has a hand attached.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, confloat

from ..core import Controller
from .gate import require_arm_available

router = APIRouter(tags=["sim"])


def _controller(request: Request) -> Controller:
    return request.app.state.controller


class DragRequest(BaseModel):
    deltas: dict[str, confloat(ge=-0.5, le=0.5, allow_inf_nan=False)]


class DragResponse(BaseModel):
    positions: dict[str, float]


@router.post(
    "/api/sim/drag",
    response_model=DragResponse,
    dependencies=[Depends(require_arm_available)],
)
def drag_sim(request: Request, body: DragRequest) -> DragResponse:
    """Push the simulated arm by per-joint deltas in radians.

    Gated like any motion endpoint: an engaged stop must outrank every hand,
    simulated or not. Unknown joints are a 400; reaching past the simulator
    is a 409.
    """
    try:
        positions = _controller(request).sim_drag(body.deltas)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    return DragResponse(positions=positions)