"""The Routine data model: an ordered list of taught poses, each with actions.

This replaces the previous generation's Recording, and the rename is load-bearing.
A Recording was a 30 Hz stream of frames replayed by interpolating on
``sample_dt`` -- the shape you get when a human continuously puppets a leader
arm. Teaching here is drag-and-release: the operator positions the arm, lets go,
and presses capture. What comes out is a handful of *discrete* poses, not a
trajectory, and calling it the old name would invite the old assumptions.

Actions hang off waypoints rather than living in an event system. The real
workflow is linear -- arrive, settle, shoot, move on -- and a trigger/condition
rule engine would add branching, event-source registration and debugging cost
against a requirement that has none of those. Adding an action *type* stays
cheap; that is the extension point that matters.
"""

from __future__ import annotations

import math
import time
import uuid
from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, field_validator

#: Bumped when a stored routine's shape changes incompatibly. Present from the
#: first version so there is never a file without one to disambiguate.
SCHEMA_VERSION = 1


class FailurePolicy(str, Enum):
    """What the executor does when an action fails."""

    #: Stop the routine. The default for anything that produces the deliverable.
    ABORT = "abort"
    #: Log and carry on to the next action.
    SKIP = "skip"
    #: Retry up to ``retries`` times, then abort.
    RETRY = "retry"


class ActionBase(BaseModel):
    """Fields the executor needs from every action, whatever its type."""

    timeout_s: float = Field(default=5.0, gt=0, le=120)
    on_failure: FailurePolicy = FailurePolicy.ABORT
    #: Only consulted when ``on_failure`` is ``RETRY``.
    retries: int = Field(default=0, ge=0, le=10)


class ShutterAction(ActionBase):
    """Fire the camera shutter over the ESP32 BLE link.

    Defaults to aborting on failure. In a photography run a silently missed
    frame is not discovered until the whole set is reviewed, by which point the
    subject and lighting are gone; stopping early costs one session, carrying
    on costs the shoot.
    """

    type: Literal["shutter"] = "shutter"
    #: Send a half-press first. Needed when the camera is on autofocus.
    focus_first: bool = True
    #: Frames per visit. One for a static product; more for a subject the
    #: photographer keeps moving between exposures.
    count: int = Field(default=1, ge=1, le=50)
    #: Pause between frames. Zero fires back to back.
    interval_s: float = Field(default=0.0, ge=0, le=60)


class SleepAction(ActionBase):
    """Wait. Distinct from a waypoint's ``settle_ms``, which is about vibration;
    this is for things like giving a flash time to recycle.

    Cannot fail, so ``on_failure`` and ``timeout_s`` are inert here. They stay
    on the base class so the executor can treat every action identically.
    """

    type: Literal["sleep"] = "sleep"
    duration_s: float = Field(gt=0, le=600)


#: Discriminated on ``type``, so a stored routine round-trips back to the right
#: class. Adding a type means adding it here and adding one executor handler.
Action = Annotated[Union[ShutterAction, SleepAction], Field(discriminator="type")]


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class Waypoint(BaseModel):
    """One taught pose, plus what to do on arrival.

    Joint *names* and joint *limits* are deliberately not validated here. Limits
    come from the URDF (plan commit #33) rather than from a hand-copied table,
    because a hand-copied table drifts from the hardware and is then trusted
    anyway. This model only guarantees the shape is sane.
    """

    id: str = Field(default_factory=_new_id)
    #: Joint angles in radians, keyed by hardware joint name.
    joints: dict[str, float]
    #: How long the move *to* this waypoint should take.
    duration_s: float = Field(default=2.0, gt=0, le=60)
    #: Stillness required after arriving, before actions run. The arm reaching
    #: its target and the arm being steady enough for a sharp frame are a few
    #: hundred milliseconds apart.
    settle_ms: int = Field(default=300, ge=0, le=10_000)
    actions: list[Action] = Field(default_factory=list)
    note: str = ""

    @field_validator("joints")
    @classmethod
    def _joints_must_be_finite_and_present(cls, v: dict[str, float]) -> dict[str, float]:
        if not v:
            raise ValueError("a waypoint needs at least one joint angle")
        bad = sorted(n for n, q in v.items() if not math.isfinite(q))
        if bad:
            raise ValueError(f"joint angles must be finite; got NaN/inf for {bad}")
        return v


class Routine(BaseModel):
    """An ordered sequence of waypoints — one shoot."""

    schema_version: int = SCHEMA_VERSION
    id: str = Field(default_factory=_new_id)
    name: str = Field(min_length=1, max_length=200)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    waypoints: list[Waypoint] = Field(default_factory=list)

    def touch(self, now: float | None = None) -> None:
        self.updated_at = time.time() if now is None else now


class RoutineSummary(BaseModel):
    """Enough to render a list without loading every waypoint."""

    id: str
    name: str
    created_at: float
    updated_at: float
    waypoint_count: int
    shutter_count: int

    @classmethod
    def of(cls, routine: Routine) -> "RoutineSummary":
        return cls(
            id=routine.id,
            name=routine.name,
            created_at=routine.created_at,
            updated_at=routine.updated_at,
            waypoint_count=len(routine.waypoints),
            shutter_count=sum(
                1 for w in routine.waypoints for a in w.actions if isinstance(a, ShutterAction)
            ),
        )
