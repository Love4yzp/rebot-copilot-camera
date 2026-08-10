"""What actions this deployment can perform.

The front end draws the trigger half of the anchor edit sheet from this, rather
than hard-coding the shutter's controls. Fields are described, not drawn: the
host owns the widgets, so every provider inherits the touch targets, focus
rings and reduced-motion behaviour that were settled once.

Providers that failed to load or failed their self-test appear here too, with
``available: false`` and the reason. A plugin that quietly vanished from the
list would read to an operator as "I configured it wrong".
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ..actions import ActionRegistry

router = APIRouter(prefix="/api/plugins", tags=["plugins"])


class ProviderField(BaseModel):
    key: str
    kind: str
    label: str
    default: object = None
    min: int | None = None
    max: int | None = None
    values: list[float] | None = None
    unit: str | None = None
    #: Show only once another field reaches a threshold, e.g.
    #: ``{"key": "count", "min": 2}``.
    when: dict | None = None


class ProviderInfo(BaseModel):
    id: str
    label: str
    #: Whether the host actually holds this provider. False for a package that
    #: failed to load or was refused: it is listed so it does not read as the
    #: operator's mistake, but nothing can be configured against it, because the
    #: host has no params model to check what would be stored.
    installed: bool = True
    available: bool
    reason: str | None = None
    retryable: bool = True
    fields: list[ProviderField] = []


def _registry(request: Request) -> ActionRegistry:
    return request.app.state.plugins


@router.get("", response_model=list[ProviderInfo])
def list_plugins(request: Request) -> list[dict]:
    """Every installed provider, working or not."""
    return _registry(request).manifest()


@router.post("/probe", response_model=list[ProviderInfo])
def probe_plugins(request: Request) -> list[dict]:
    """Re-run every provider's self-test.

    Not behind the motion gate: a probe is the shutter's ``ping``, not its
    ``shoot`` — it moves no joints and burns no frame, and checking why an
    accessory is dark is a reasonable thing to do while the arm is stopped.
    """
    registry = _registry(request)
    registry.probe_all()
    return registry.manifest()
