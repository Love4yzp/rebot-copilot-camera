"""Emergency stop endpoints.

Three entry points engage the stop: the web UI's button, this API, and the
watchdog. All three land on the same latch.

Note what is deliberately absent: there is no "clear and resume". By the time
an operator clears a stop the scene has usually changed, and resuming into a
changed scene is a collision. Clear leaves the arm holding in idle. Teaching
is an explicit intent (「+ 录位姿」), not a side effect of the escape hatch.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..safety import LatchSnapshot, LatchSource, SafetyLatch
from .gate import get_latch

router = APIRouter(prefix="/api/estop", tags=["estop"])


class EngageRequest(BaseModel):
    reason: str = Field(
        default="operator engaged emergency stop",
        min_length=1,
        description="Shown to whoever has to work out why the arm stopped.",
    )
    source: LatchSource = LatchSource.API


class EstopStatus(BaseModel):
    latched: bool
    reason: str | None = None
    source: LatchSource | None = None
    engaged_at: float | None = None
    freeze_pose: dict[str, float] | None = None
    #: True when this request is what changed the state, false when it was a no-op.
    changed: bool | None = None

    @classmethod
    def from_snapshot(cls, snap: LatchSnapshot, changed: bool | None = None) -> "EstopStatus":
        return cls(
            latched=snap.latched,
            reason=snap.reason,
            source=snap.source,
            engaged_at=snap.engaged_at,
            freeze_pose=dict(snap.freeze_pose) if snap.freeze_pose else None,
            changed=changed,
        )


@router.get("", response_model=EstopStatus)
def get_estop(latch: SafetyLatch = Depends(get_latch)) -> EstopStatus:
    return EstopStatus.from_snapshot(latch.snapshot())


@router.post("", response_model=EstopStatus)
def engage_estop(
    body: EngageRequest | None = None,
    latch: SafetyLatch = Depends(get_latch),
) -> EstopStatus:
    """Engage the stop.

    Always 200, never 409: an emergency stop that argues with you is a broken
    emergency stop. Re-engaging an already-latched stop is a no-op that keeps
    the original reason, reported via ``changed: false``.
    """
    body = body or EngageRequest()
    changed = latch.engage(body.reason, body.source)
    return EstopStatus.from_snapshot(latch.snapshot(), changed=changed)


@router.post("/clear", response_model=EstopStatus)
def clear_estop(latch: SafetyLatch = Depends(get_latch)) -> EstopStatus:
    """Release the stop. The arm stays holding; nothing resumes.

    Teaching is a separate intent. Auto-entering drag after a clear made
    uncalibrated gravity feedforward look like a safety recovery.

    Deliberately not behind the motion gate -- gating the escape hatch on the
    thing it escapes would wedge the system.
    """
    changed = latch.clear()
    return EstopStatus.from_snapshot(latch.snapshot(), changed=changed)
