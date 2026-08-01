"""Semantic events: what happened, for anyone who is not the UI.

The websocket at ``/ws`` streams *state* — joint angles at 20 Hz plus playback
progress — because that is what a screen needs. An integration does not. A
process that files photographs, drives a light board or logs a shoot wants to
know that a frame was taken, not to re-derive it from a position stream, and it
should not have to eat 20 Hz of joint angles over a studio LAN to find out.

So events are a second stream, and three rules keep them from becoming a
control path:

**One way, and never a veto.** An event is a notification. Nothing a subscriber
returns can change what the routine does next. A hook that could refuse would
put third-party code in the path that decides whether the arm moves, which is
what the motion gate exists to prevent.

**Bounded, and the oldest goes first.** Subscribers get the Broadcaster's
existing back-pressure-free queues. A slow subscriber loses messages rather
than stalling the control thread — a loop that stops because something stopped
reading is a loop that stops holding the arm up.

**Emitted where the fact is known.** Routine and action events come from the
executor, which knows what it decided; stop events come from the control loop,
which sees the latch. Drivers report success or failure and nothing else.
"""

from __future__ import annotations

#: Broadcaster message type. `/ws` does not subscribe to it — see the module
#: docstring — so adding an event costs the browser nothing.
TOPIC = "event"

# A routine, start to finish.
ROUTINE_STARTED = "routine.started"
ROUTINE_DONE = "routine.done"
ROUTINE_ABORTED = "routine.aborted"

#: The arm has reached a taught pose and is holding it. The one an integration
#: usually wants: it is the moment the scene is what the anchor said it would be.
ANCHOR_ARRIVED = "anchor.arrived"

ACTION_STARTED = "action.started"
ACTION_DONE = "action.done"
ACTION_FAILED = "action.failed"

ESTOP_ENGAGED = "estop.engaged"
ESTOP_CLEARED = "estop.cleared"

#: A pose was recorded by hand. Emitted by the capture endpoint, not the loop.
TEACH_CAPTURED = "teach.captured"


def envelope(name: str, data: dict, t: float) -> dict:
    """The frame a subscriber receives, minus the broadcaster's own wrapper."""
    return {"event": name, "t": t, "data": data}
