"""Bounded external force injection into the selected virtual plant only."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from .gate import require_arm_available

router = APIRouter(prefix="/api/sim", tags=["simulation"])


class Perturbation(BaseModel):
    joint: str
    torque: float = Field(allow_inf_nan=False)
    duration_s: float = Field(default=0.15, gt=0, le=0.2, allow_inf_nan=False)


class SimulationState(BaseModel):
    payload: str
    inertia_source: str
    model_sha256: str
    model_source: str
    wall_lag_s: float
    t: float | None = None
    q: list[float] | None = None
    v: list[float] | None = None
    tau: list[float] | None = None
    error: list[float] | None = None
    saturated: list[bool] | None = None
    ncon: int | None = None


@router.post("/perturb", dependencies=[Depends(require_arm_available)])
def perturb(body: Perturbation, request: Request) -> dict:
    try:
        request.app.state.controller.perturb(body.joint, body.torque, body.duration_s)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from None
    return {"accepted": True}


@router.get("/state", response_model=SimulationState)
def state(request: Request) -> dict:
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None or runtime.physics is None:
        raise HTTPException(404, "this instance is not a physical simulation")
    return {
        "payload": runtime.payload_name,
        "inertia_source": runtime.inertia_source,
        "model_sha256": runtime.model_digest,
        **runtime.physics.diagnostics(),
    }
