"""Exclusive activity of the arm, and the intents that change it.

The interface is :func:`decide`. Callers (the controller, and the tests)
cross that seam. The table does not talk to the arm, the latch, or time.

The latch is not an activity — it is a freeze that outranks this table.
Callers check the latch first.

Adding a later behaviour (contact, disconnect, blended retarget) is a new
row, not a new flag on the controller. Effects name what the loop must do
to the hardware; they are not done here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum


class Activity(str, Enum):
    """Wire values match the existing ``mode`` field except ``rest`` / ``safelock``."""

    IDLE = "idle"
    TEACHING = "teach"
    PLAYING = "playback"
    RESTING = "rest"
    SAFELOCK = "safelock"


class Intent(str, Enum):
    TEACH_ON = "teach_on"
    TEACH_OFF = "teach_off"
    REST_ON = "rest_on"
    REST_OFF = "rest_off"
    PLAY = "play"
    GOTO = "goto"
    STOP = "stop"
    RESUME_WAIT = "resume_wait"
    FINISH = "finish"
    FAULT = "fault"
    UNLOCK = "unlock"


class Effect(str, Enum):
    NONE = "none"
    START_TEACH = "start_teach"
    STOP_TEACH = "stop_teach"
    START_REST = "start_rest"
    STOP_REST = "stop_rest"
    START_PLAY = "start_play"
    RETARGET = "retarget"
    ABORT_PLAY = "abort_play"
    RESUME_WAIT = "resume_wait"
    FINISH = "finish"
    LOCK = "lock"
    UNLOCK = "unlock"


@dataclass(frozen=True)
class Decision:
    ok: bool
    activity: Activity
    effect: Effect = Effect.NONE
    reason: str = ""


def _ok(activity: Activity, effect: Effect = Effect.NONE, reason: str = "") -> Decision:
    return Decision(True, activity, effect, reason)


def _deny(activity: Activity, reason: str) -> Decision:
    return Decision(False, activity, Effect.NONE, reason)


def decide(activity: Activity, intent: Intent) -> Decision:
    """The whole command policy. Unknown pairs refuse and stay put."""
    key = (activity, intent)
    row = _TABLE.get(key)
    if row is not None:
        return row(activity) if callable(row) else row
    return _deny(activity, _default_reason(activity, intent))


def _default_reason(activity: Activity, intent: Intent) -> str:
    if intent is Intent.TEACH_ON and activity is Activity.PLAYING:
        return "cannot teach while a sequence is executing"
    if intent in (Intent.PLAY, Intent.GOTO) and activity is Activity.TEACHING:
        return "cannot move while teaching" if intent is Intent.GOTO else "cannot execute while teaching"
    if intent is Intent.PLAY and activity is Activity.PLAYING:
        return "a sequence is already executing"
    if intent is Intent.FAULT and activity is Activity.IDLE:
        return "idle is not judged for contact"
    if activity is Activity.SAFELOCK and intent is not Intent.UNLOCK:
        return "safe lock is engaged"
    return f"cannot {intent.value} while {activity.value}"


def _rest_on(_a: Activity) -> Decision:
    return _ok(Activity.RESTING, Effect.START_REST)


def _rest_off(_a: Activity) -> Decision:
    return _ok(Activity.IDLE, Effect.STOP_REST)


def _teach_on(_a: Activity) -> Decision:
    return _ok(Activity.TEACHING, Effect.START_TEACH)


def _teach_off(_a: Activity) -> Decision:
    return _ok(Activity.IDLE, Effect.STOP_TEACH)


def _start_play(_a: Activity) -> Decision:
    return _ok(Activity.PLAYING, Effect.START_PLAY)


def _retarget(_a: Activity) -> Decision:
    return _ok(Activity.PLAYING, Effect.RETARGET)


def _stop_play(_a: Activity) -> Decision:
    return _ok(Activity.IDLE, Effect.ABORT_PLAY)


def _finish(_a: Activity) -> Decision:
    return _ok(Activity.IDLE, Effect.FINISH)


def _resume(_a: Activity) -> Decision:
    return _ok(Activity.PLAYING, Effect.RESUME_WAIT)


def _fault_lock(_a: Activity) -> Decision:
    return _ok(Activity.SAFELOCK, Effect.LOCK)


def _unlock(_a: Activity) -> Decision:
    return _ok(Activity.IDLE, Effect.UNLOCK)


def _idle_stop(_a: Activity) -> Decision:
    return _ok(Activity.IDLE, Effect.NONE)


def _deny_play_busy(a: Activity) -> Decision:
    return _deny(a, "a sequence is already executing")


def _deny_teach_busy(a: Activity) -> Decision:
    return _deny(a, "cannot teach while a sequence is executing")


def _deny_goto_teach(a: Activity) -> Decision:
    return _deny(a, "cannot move while teaching")


def _deny_play_teach(a: Activity) -> Decision:
    return _deny(a, "cannot execute while teaching")


def _deny_fault_idle(a: Activity) -> Decision:
    return _deny(a, "idle is not judged for contact")


def _deny_safelock(a: Activity) -> Decision:
    return _deny(a, "safe lock is engaged")


_TABLE: dict[tuple[Activity, Intent], Decision | Callable[[Activity], Decision]] = {
    (Activity.IDLE, Intent.STOP): _idle_stop,
    (Activity.IDLE, Intent.REST_ON): _rest_on,
    (Activity.IDLE, Intent.TEACH_ON): _teach_on,
    (Activity.IDLE, Intent.PLAY): _start_play,
    (Activity.IDLE, Intent.GOTO): _start_play,
    (Activity.IDLE, Intent.FAULT): _deny_fault_idle,
    (Activity.IDLE, Intent.FINISH): _idle_stop,
    (Activity.IDLE, Intent.TEACH_OFF): _idle_stop,
    (Activity.IDLE, Intent.REST_OFF): _idle_stop,
    (Activity.RESTING, Intent.REST_OFF): _rest_off,
    (Activity.RESTING, Intent.REST_ON): lambda a: _ok(Activity.RESTING, Effect.NONE),
    (Activity.RESTING, Intent.TEACH_ON): _teach_on,
    (Activity.RESTING, Intent.PLAY): _start_play,
    (Activity.RESTING, Intent.GOTO): _start_play,
    # Stop is "abort the tape", not wake. Resting has no tape.
    (Activity.RESTING, Intent.STOP): lambda a: _ok(Activity.RESTING, Effect.NONE),
    (Activity.TEACHING, Intent.TEACH_OFF): _teach_off,
    (Activity.TEACHING, Intent.TEACH_ON): lambda a: _ok(Activity.TEACHING, Effect.NONE),
    (Activity.TEACHING, Intent.GOTO): _deny_goto_teach,
    (Activity.TEACHING, Intent.PLAY): _deny_play_teach,
    (Activity.TEACHING, Intent.REST_ON): lambda a: _deny(a, "cannot rest while teaching"),
    (Activity.TEACHING, Intent.STOP): lambda a: _ok(Activity.TEACHING, Effect.NONE),
    (Activity.TEACHING, Intent.FAULT): _fault_lock,
    (Activity.PLAYING, Intent.PLAY): _deny_play_busy,
    (Activity.PLAYING, Intent.GOTO): _retarget,
    (Activity.PLAYING, Intent.TEACH_ON): _deny_teach_busy,
    (Activity.PLAYING, Intent.REST_ON): lambda a: _deny(a, "a sequence is executing"),
    (Activity.PLAYING, Intent.STOP): _stop_play,
    (Activity.PLAYING, Intent.FINISH): _finish,
    (Activity.PLAYING, Intent.RESUME_WAIT): _resume,
    (Activity.PLAYING, Intent.FAULT): _fault_lock,
    (Activity.SAFELOCK, Intent.UNLOCK): _unlock,
    (Activity.SAFELOCK, Intent.STOP): _unlock,
    (Activity.SAFELOCK, Intent.FAULT): lambda a: _ok(Activity.SAFELOCK, Effect.NONE),
    (Activity.SAFELOCK, Intent.PLAY): _deny_safelock,
    (Activity.SAFELOCK, Intent.GOTO): _deny_safelock,
    (Activity.SAFELOCK, Intent.TEACH_ON): _deny_safelock,
    (Activity.SAFELOCK, Intent.REST_ON): _deny_safelock,
}
