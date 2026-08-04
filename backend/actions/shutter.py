"""The camera shutter, as an action provider.

The first provider, and the one the interface was drawn around. It is a thin
adapter over :class:`~backend.shutter.base.ShutterDriver`: the driver stays the
hardware link (``/api/shutter/test`` still talks to it directly, from a request
thread, to check the chain without involving a routine), and this is how a
routine uses that link.

Nothing about the ESP32 or BLE appears here. Swapping in an infrared trigger or
a wired release means another provider, not a change to the executor.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..shutter.base import ShutterDriver
from .base import ActionContext, FieldSpec


#: The id the built-in shutter is registered under. Stored actions name their
#: provider by id, and ``ShutterAction`` — which keeps its own concrete type —
#: is dispatched to this one. Spelled once: a literal in the executor is a
#: literal that silently stops matching if this ever changes.
SHUTTER_PROVIDER_ID = "shutter"


class ShutterParams(BaseModel):
    """What the anchor edit sheet lets an operator change about a trigger.

    Mirrors the fields of :class:`~backend.routines.models.ShutterAction` that
    are actually operator-facing; ``on_failure``, ``retries`` and ``timeout_s``
    are host policy and stay off this model.
    """

    focus_first: bool = True
    count: int = Field(default=1, ge=1, le=50)
    interval_s: float = Field(default=0.0, ge=0, le=60)

    @classmethod
    def from_action(cls, action) -> "ShutterParams":
        """Read the operator-facing half of a stored ``ShutterAction``.

        Lives here rather than in the executor so that the field names of this
        provider are known in one file. The executor's job is to submit an
        action, not to know what a shutter's settings are called.
        """
        return cls(
            focus_first=action.focus_first,
            count=action.count,
            interval_s=action.interval_s,
        )


class ShutterProvider:
    """Fires one frame per call."""

    id = SHUTTER_PROVIDER_ID
    label = "快门"
    params_model = ShutterParams
    #: A frame that failed can be taken again — that is what a retry means here.
    retryable = True

    def __init__(self, driver: ShutterDriver) -> None:
        self._driver = driver

    def fields(self) -> list[FieldSpec]:
        return [
            FieldSpec(key="count", kind="stepper", label="次数", default=1, min=1, max=10),
            FieldSpec(
                key="interval_s",
                kind="tiers",
                label="间隔",
                default=1.0,
                values=[0.5, 1, 2, 5],
                unit="秒",
                # A gap between frames only means something once there are two.
                when={"key": "count", "min": 2},
            ),
            FieldSpec(key="focus_first", kind="switch", label="先对焦", default=True),
        ]

    def probe(self) -> None:
        """Ping the board. Says nothing about the camera, and burns no frame."""
        self._driver.ping()

    def run(self, params: ShutterParams, ctx: ActionContext) -> None:
        """Fire once.

        One frame, not ``params.count`` of them: the host paces a burst so that
        an emergency stop lands *between* frames rather than being noticed only
        after the whole burst has been shot. Refocusing every frame is the same
        decision — between frames of a burst the subject has usually moved,
        which is why there is a burst at all.
        """
        if params.focus_first:
            self._driver.focus()
        self._driver.shoot()
        ctx.emit(
            "shutter.fired",
            {"waypoint_index": ctx.waypoint_index, "anchor": ctx.waypoint_note},
        )
