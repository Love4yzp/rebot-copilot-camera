"""Tuning panel endpoints: read, hot-apply, save, reset.

The safety model lives in ``Controller.apply_tuning`` — this layer only
validates shape (pydantic, 422) and maps rejections to 409. Two deliberate
absences:

- no websocket channel: tuning is request/response, and the panel refetches
  after every mutation;
- no motion gate: these endpoints command no motion themselves, and the
  torque-class changes are gated inside the controller on what the arm is
  actually doing (executing / floating). They are listed in
  ``tests/test_motion_gate.py``'s NON_MOTION_ROUTES with that reasoning.

The response always carries both the live config and the saved one, so the
panel's dirty markers need no second request.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from .. import assets
from ..tuning import TuningConfig, TuningRejected, TuningStore, merge_patch

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/config", tags=["config"])


class TuningState(BaseModel):
    current: TuningConfig
    saved: TuningConfig
    #: Sections whose live value differs from the saved file.
    dirty: list[str] = Field(default_factory=list)
    #: Whether the gripper motor is on the bus (hardware yaml). When it is,
    #: the payload profile is fixed to "gripper" — a motor cannot be hot-added.
    gripper_motor: bool
    payload_options: list[str]


def _store(request: Request) -> TuningStore | None:
    return getattr(request.app.state, "tuning_store", None)


def _state(request: Request) -> TuningState:
    controller = request.app.state.controller
    store = _store(request)
    saved = store.load() if store is not None else TuningConfig()
    current = controller.tuning
    gripper_motor = assets.has_gripper()
    return TuningState(
        current=current,
        saved=saved,
        dirty=current.dirty_sections(saved),
        gripper_motor=gripper_motor,
        payload_options=[p.value for p in _payload_options(gripper_motor)],
    )


def _payload_options(gripper_motor: bool):
    from ..tuning import PayloadProfile

    if gripper_motor:
        return [PayloadProfile.GRIPPER]
    return [PayloadProfile.BARE, PayloadProfile.CAMERA]


def _apply(request: Request, config: TuningConfig) -> TuningState:
    try:
        request.app.state.controller.apply_tuning(config)
    except TuningRejected as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return _state(request)


@router.get("/tuning", response_model=TuningState)
def get_tuning(request: Request) -> TuningState:
    return _state(request)


@router.put("/tuning", response_model=TuningState)
def put_tuning(request: Request, patch: dict[str, Any]) -> TuningState:
    """Hot-apply a partial patch. Validated as the *merged* config, so a
    camera mass and the camera profile may arrive in either order."""
    try:
        merged = merge_patch(request.app.state.controller.tuning, patch)
    except TuningRejected as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except ValidationError as e:
        raise HTTPException(
            status_code=422, detail=e.errors(include_context=False, include_url=False)
        ) from e
    return _apply(request, merged)


@router.post("/tuning/save", response_model=TuningState)
def save_tuning(request: Request) -> TuningState:
    """Persist the live config. Explicit on purpose: hot-applied values die
    with the process unless the operator says keep them."""
    store = _store(request)
    if store is None:
        raise HTTPException(status_code=503, detail="no tuning store configured")
    store.save(request.app.state.controller.tuning)
    log.info("tuning saved to %s", store.path)
    return _state(request)


@router.post("/tuning/reset", response_model=TuningState)
def reset_tuning(request: Request) -> TuningState:
    """Reload the saved file and apply it. This is the "give me back what I
    had before I started fiddling" button, not a factory reset."""
    store = _store(request)
    if store is None:
        raise HTTPException(status_code=503, detail="no tuning store configured")
    return _apply(request, store.load())
