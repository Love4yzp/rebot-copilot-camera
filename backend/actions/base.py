"""What an action provider is.

An *action* is what the end effector does once the arm has arrived and settled:
fire a shutter, close a gripper, turn a stage. A *provider* implements one, and
providers are the extension point third parties write against — adding one must
not mean touching the control loop, the executor or the UI.

Three rules shape this interface. Each answers a way it goes wrong when someone
else's code runs inside a service that is holding a 48 V arm up.

**A provider may block.** :meth:`ActionProvider.run` is allowed to sit on a
serial exchange for seconds; ``Esp32Shutter.shoot`` already waits up to six for
a camera waking over BLE. Providers never run on the control loop -- see
:mod:`backend.actions.runner` -- so blocking costs the arm nothing. A provider
does not have to be written carefully to be safe here, which is the point: a
contract that relies on third-party discipline is not a contract.

**A provider cannot move the arm.** :class:`ActionContext` carries a read-only
pose and nothing else. There is no arm handle to reach for, so no plugin can
issue motion behind the emergency-stop gate. Same technique that keeps the
latch out of the executor: make the wrong thing unreachable, not merely
forbidden.

**A provider reports failure by raising.** A boolean return is exactly the shape
that gets dropped at a call site, and the executor has to tell "carry on" from
"stop the shoot" — the same argument backend/shutter/base.py makes about the
driver underneath this.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Mapping, Protocol, runtime_checkable

from pydantic import BaseModel


class ActionError(Exception):
    """The action did not do what was asked."""


class ActionUnavailable(ActionError):
    """The provider is not usable. Every retry fails the same way.

    Distinct from a plain failure for the same reason ``ShutterNotConnected`` is
    distinct from ``ShutterError``: a dead link means every remaining action
    will fail too, so retrying is only a way to waste the shoot slowly.
    """


class ActionTimeout(ActionError):
    """The provider did not return in time.

    Raised by the runner, never by a provider. The provider's thread may still
    be running — Python cannot kill a thread — so this says "we gave up", not
    "it stopped".
    """


#: Widget kinds a provider may ask the UI for. Deliberately only three: they are
#: the three the anchor edit sheet already implements, and those have been
#: through the touch-target, focus-visible and reduced-motion pass. Wanting a
#: fourth is a host change on purpose — a plugin that ships its own markup is a
#: plugin that ships its own colours, and on this machine colour is a status
#: channel, not decoration.
FieldKind = Literal["switch", "stepper", "tiers"]


@dataclass(frozen=True)
class FieldSpec:
    """One control in the anchor edit sheet, described rather than drawn."""

    key: str
    kind: FieldKind
    label: str
    default: Any
    #: ``stepper`` bounds.
    min: int | None = None
    max: int | None = None
    #: ``tiers`` choices, in display order.
    values: list[float] | None = None
    unit: str = ""
    #: Show only when another field is at least this large, e.g.
    #: ``{"key": "count", "min": 2}``. A dict rather than an expression string:
    #: a front end that evaluates arbitrary expressions is the start of a bad
    #: time, and every condition this UI has ever needed is a threshold.
    when: dict | None = None


@dataclass(frozen=True)
class ActionContext:
    """What a provider is allowed to know.

    Small on purpose. Note what is *not* here: the arm, the latch, the routine
    store. A provider that cannot reach them cannot be the reason any of them
    did something surprising.
    """

    routine_id: str
    routine_name: str
    waypoint_index: int
    waypoint_note: str
    #: Pose when the action started, radians by joint name. A snapshot, not a
    #: live view — the arm is holding, and a provider has no business watching.
    joints: Mapping[str, float] = field(default_factory=dict)
    #: Push a semantic event onto the event stream. Fire and forget: never
    #: blocks, never raises, and nothing it returns can change the routine.
    emit: Callable[[str, dict], None] = lambda name, data: None


@runtime_checkable
class ActionProvider(Protocol):
    """One kind of action. Registered by entry point; called by the runner."""

    #: Stable identifier, stored in routine JSON. Renaming one orphans anchors.
    id: str
    #: Shown in the UI. Free to be localised; ``id`` is not.
    label: str
    #: Validates the params half of a stored action.
    params_model: type[BaseModel]
    #: May the host re-run this after a failure? False for anything whose side
    #: effect is not repeatable — a strobe that needs to recycle, a dispenser.
    #: The host downgrades a retry policy to abort rather than guessing.
    retryable: bool

    def fields(self) -> list[FieldSpec]:
        """Controls for the anchor edit sheet. The host renders these."""
        ...

    def probe(self) -> None:
        """Self-test. Raises when the provider is not usable right now.

        Called at startup and whenever the plugin list is refreshed, so it has
        to be cheap and free of side effects: the shutter's ``ping``, not its
        ``shoot``.
        """
        ...

    def run(self, params: BaseModel, ctx: ActionContext) -> None:
        """Do it. May block — this runs on the provider's own worker thread."""
        ...
