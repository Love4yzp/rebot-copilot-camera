"""The motion gate: one dependency that every arm-moving endpoint must carry.

While the emergency stop is latched, nothing new may be commanded. Enforcing
that in the control loop alone is not enough -- a request that returns 200 while
the arm refuses to move is a lie the operator will act on. So the refusal
happens at the edge, with the reason attached.

Attach with::

    @router.post("/api/…", dependencies=[Depends(require_arm_available)])

``tests/test_motion_gate.py`` walks the route table and fails if any mutating
endpoint neither carries this dependency nor is explicitly declared non-motion,
so forgetting it on a new endpoint is a test failure rather than a silent hole.
"""

from __future__ import annotations

from fastapi import HTTPException, Request, status

from ..safety import SafetyLatch


def get_latch(request: Request) -> SafetyLatch:
    """The application's single SafetyLatch."""
    return request.app.state.latch


def require_arm_available(request: Request) -> None:
    """Reject the request with 409 while the emergency stop is latched.

    409 rather than 503: the arm is not unavailable, it is in a state the
    caller must explicitly resolve by clearing the stop.
    """
    latch: SafetyLatch = request.app.state.latch
    snap = latch.snapshot()
    if snap.latched:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "estop_latched",
                "message": "Emergency stop is engaged; clear it before commanding motion.",
                "reason": snap.reason,
                "source": snap.source.value if snap.source else None,
                "engaged_at": snap.engaged_at,
            },
        )
