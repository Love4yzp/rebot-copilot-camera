"""A turntable on a serial port, as an action provider.

This is the worked example from ``docs/PLUGINS.md``, and it is a real installed
package rather than a listing in a document: it is in the dev environment, so
``tests/test_plugin_packaging.py`` discovers it through the same
``importlib.metadata`` scan the service uses. Packaging metadata that is only
ever quoted in prose is metadata nobody has run — a misspelled entry point group
or a factory that needs an argument would be found by whoever first tried to
follow the guide, on a device, with the arm already mounted.

Everything here is deliberately ordinary. There is no host API beyond four
attributes and three methods, no registration call, and no configuration
handshake: the plugin reads its own environment, because a host that owned
plugin configuration would need a config schema per plugin, and that has no end.

Run it without hardware::

    TURNTABLE_PORT=sim uv run -m backend.actions.check turntable --probe
    TURNTABLE_PORT=sim uv run -m backend.actions.check turntable --run '{"degrees": 90}'
"""

from __future__ import annotations

import os

from pydantic import BaseModel, Field

from backend.actions import ActionContext, ActionError, ActionUnavailable, FieldSpec

#: Set to ``sim`` for an in-process table. The same idea as SimArm and
#: SimShutter: the no-hardware loop is infrastructure, not a convenience, and a
#: plugin author's first day should not need the accessory either.
PORT = os.environ.get("TURNTABLE_PORT", "/dev/rebot-turntable")

#: Long enough for half a turn on a slow table. The host times this out on its
#: own thread — blocking here costs the control loop nothing.
REPLY_TIMEOUT_S = 10.0


class TurntableParams(BaseModel):
    """What an operator can change about one rotation."""

    degrees: float = Field(default=45.0, ge=-180, le=180)


class TurntableProvider:
    """Turns the table, then returns. One rotation per call."""

    #: Stored in routine JSON. Renaming it orphans every anchor that used it.
    id = "turntable"
    #: Shown in the edit sheet. Free to be localised; ``id`` is not.
    label = "转台"
    params_model = TurntableParams
    #: A rotation is relative, so a retry after a failure turns it *again* —
    #: which is a different pose, not the same one retried. Saying so makes the
    #: host downgrade a retry policy to abort instead of guessing.
    retryable = False

    def __init__(self) -> None:
        self._port = PORT
        self._link: _Link | None = None

    def fields(self) -> list[FieldSpec]:
        """Described, not drawn. The host owns the widgets, so this inherits the
        touch targets and focus behaviour the sheet already had."""
        return [
            FieldSpec(
                key="degrees",
                kind="tiers",
                label="转角",
                default=45,
                values=[15, 30, 45, 90],
                unit="°",
            ),
        ]

    def probe(self) -> None:
        """Self-test: is the table there? Cheap, and it does not move anything.

        Runs at startup and on every plugin refresh, so it is the equivalent of
        the shutter's ``ping`` rather than its ``shoot``.
        """
        reply = self._exchange("PING")
        if not reply.startswith("OK"):
            raise ActionUnavailable(f"turntable on {self._port} answered {reply!r}")

    def run(self, params: TurntableParams, ctx: ActionContext) -> None:
        """Rotate, and wait for the table to say it got there.

        Blocking is fine and expected: this is the provider's own worker thread,
        never the control loop.
        """
        reply = self._exchange(f"ROT {params.degrees:g}")
        if not reply.startswith("OK"):
            raise ActionError(f"turntable refused: {reply!r}")
        ctx.emit("turntable.rotated", {"degrees": params.degrees})

    # ── the link ─────────────────────────────────────────────────────────────

    def _exchange(self, command: str) -> str:
        try:
            # Opening is inside the guard too: pyserial raises when the port is
            # not there, and that is the ordinary case of a table nobody plugged
            # in. ActionUnavailable rather than a bare OSError, because the host
            # reads that as "every retry fails the same way" instead of retrying
            # a missing device once per anchor.
            if self._link is None:
                self._link = _open(self._port)
            return self._link.exchange(command)
        except OSError as exc:
            # Drop the handle so the next call reconnects rather than talking to
            # a port that went away when someone unplugged the table.
            self._link = None
            raise ActionUnavailable(f"turntable unreachable on {self._port}: {exc}") from exc


class _Link:
    """A line protocol over pyserial. One request in flight, like the shutter."""

    def __init__(self, port: str) -> None:
        import serial  # imported here so `sim` needs no pyserial at all

        self._serial = serial.Serial(port, 115200, timeout=REPLY_TIMEOUT_S)

    def exchange(self, command: str) -> str:
        self._serial.write(f"{command}\n".encode())
        return self._serial.readline().decode(errors="replace").strip()


class _SimLink:
    """A table that answers instantly and remembers where it is."""

    def __init__(self) -> None:
        self.angle = 0.0

    def exchange(self, command: str) -> str:
        if command == "PING":
            return "OK"
        if command.startswith("ROT "):
            self.angle = (self.angle + float(command[4:])) % 360
            return f"OK {self.angle:g}"
        return f"ERR unknown {command!r}"


def _open(port: str) -> _Link | _SimLink:
    return _SimLink() if port == "sim" else _Link(port)
