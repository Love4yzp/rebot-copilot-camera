"""Routine and waypoint CRUD.

None of these move the arm, so none carry the motion gate: editing a routine
while the emergency stop is engaged is not just harmless, it is often what the
operator is doing *because* the stop is engaged. They are declared as
non-motion in ``tests/test_motion_gate.py``, which is the deliberate choice the
coverage test forces.

Waypoints are addressed by list index rather than by id. The editor is a
reorderable list, and index is what the operator is looking at.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, ValidationError

from ..routines import Action, Routine, RoutineNotFound, RoutineStore, RoutineSummary, Waypoint

router = APIRouter(prefix="/api/routines", tags=["routines"])


def _validated_waypoint(data: dict) -> Waypoint:
    """Build a Waypoint from request data, reporting failures as 422.

    The request models keep their fields optional so a PATCH can omit them,
    which means the real constraints (positive duration, non-negative settle,
    finite angles) only bite here. Without this the caller would get a 500 for
    what is plainly a bad request.
    """
    try:
        return Waypoint.model_validate(data)
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            exc.errors(include_url=False),
        ) from None


def _store(request: Request) -> RoutineStore:
    return request.app.state.routine_store


def _load(request: Request, rid: str) -> Routine:
    try:
        return _store(request).get(rid)
    except RoutineNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no routine {rid!r}") from None


def _waypoint_at(routine: Routine, index: int) -> Waypoint:
    if not 0 <= index < len(routine.waypoints):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"waypoint index {index} out of range (routine has {len(routine.waypoints)})",
        )
    return routine.waypoints[index]


# ── routines ─────────────────────────────────────────────────────────────────


class CreateRoutine(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class RenameRoutine(BaseModel):
    name: str = Field(min_length=1, max_length=200)


@router.get("", response_model=list[RoutineSummary])
def list_routines(request: Request) -> list[RoutineSummary]:
    return _store(request).list()


@router.post("", response_model=Routine, status_code=status.HTTP_201_CREATED)
def create_routine(body: CreateRoutine, request: Request) -> Routine:
    return _store(request).save(Routine(name=body.name))


@router.get("/{rid}", response_model=Routine)
def get_routine(rid: str, request: Request) -> Routine:
    return _load(request, rid)


@router.patch("/{rid}", response_model=Routine)
def rename_routine(rid: str, body: RenameRoutine, request: Request) -> Routine:
    routine = _load(request, rid)
    routine.name = body.name
    routine.touch()
    return _store(request).save(routine)


@router.delete("/{rid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_routine(rid: str, request: Request) -> None:
    try:
        _store(request).delete(rid)
    except RoutineNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no routine {rid!r}") from None


# ── waypoints ────────────────────────────────────────────────────────────────


class AddWaypoint(BaseModel):
    joints: dict[str, float]
    duration_s: float | None = None
    settle_ms: int | None = None
    actions: list[Action] | None = None
    note: str | None = None
    #: Where to insert. ``None`` appends, which is what teaching does.
    index: int | None = None


class UpdateWaypoint(BaseModel):
    """Every field optional — omitted means unchanged.

    ``actions`` is replaced wholesale rather than merged. Merging a list has no
    single obvious meaning, and the editor always holds the full list anyway.
    """

    duration_s: float | None = None
    settle_ms: int | None = None
    actions: list[Action] | None = None
    note: str | None = None
    joints: dict[str, float] | None = None


class ReorderWaypoints(BaseModel):
    #: The new ordering, as current indices. Must be a permutation of them all.
    order: list[int]


@router.post("/{rid}/waypoints", response_model=Routine, status_code=status.HTTP_201_CREATED)
def add_waypoint(rid: str, body: AddWaypoint, request: Request) -> Routine:
    routine = _load(request, rid)

    waypoint = _validated_waypoint(body.model_dump(exclude_none=True, exclude={"index"}))

    if body.index is None:
        routine.waypoints.append(waypoint)
    elif 0 <= body.index <= len(routine.waypoints):
        routine.waypoints.insert(body.index, waypoint)
    else:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"insert index {body.index} out of range (routine has {len(routine.waypoints)})",
        )

    routine.touch()
    return _store(request).save(routine)


@router.patch("/{rid}/waypoints/{index}", response_model=Routine)
def update_waypoint(rid: str, index: int, body: UpdateWaypoint, request: Request) -> Routine:
    routine = _load(request, rid)
    waypoint = _waypoint_at(routine, index)

    # Merge as plain data, then validate. model_copy(update=...) would skip
    # validators entirely and write a bad settle_ms or a NaN joint angle
    # straight to disk, and it would leave raw dicts where typed actions belong.
    merged = waypoint.model_dump()
    merged.update(body.model_dump(exclude_none=True))
    routine.waypoints[index] = _validated_waypoint(merged)

    routine.touch()
    return _store(request).save(routine)


@router.delete("/{rid}/waypoints/{index}", response_model=Routine)
def delete_waypoint(rid: str, index: int, request: Request) -> Routine:
    routine = _load(request, rid)
    _waypoint_at(routine, index)
    routine.waypoints.pop(index)
    routine.touch()
    return _store(request).save(routine)


@router.post("/{rid}/waypoints/reorder", response_model=Routine)
def reorder_waypoints(rid: str, body: ReorderWaypoints, request: Request) -> Routine:
    routine = _load(request, rid)

    # A permutation, not an arbitrary index list: anything else would silently
    # drop or duplicate a waypoint, and the operator would find out mid-shoot.
    if sorted(body.order) != list(range(len(routine.waypoints))):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"order must be a permutation of 0..{len(routine.waypoints) - 1}, got {body.order}",
        )

    routine.waypoints = [routine.waypoints[i] for i in body.order]
    routine.touch()
    return _store(request).save(routine)
