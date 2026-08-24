"""The activity table is the command seam: exclusive states, named intents.

Tests speak Decision, not controller flags. A later feature (contact, disconnect,
goto-retarget blend) adds a row here; it does not grow a new boolean on Controller.
"""

from backend.core.activity import Activity, Decision, Effect, Intent, decide


def test_idle_by_default_is_the_table_start():
    d = decide(Activity.IDLE, Intent.STOP)
    assert d == Decision(ok=True, activity=Activity.IDLE, effect=Effect.NONE)


def test_rest_is_not_idle():
    d = decide(Activity.IDLE, Intent.REST_ON)
    assert d.ok
    assert d.activity is Activity.RESTING
    assert d.effect is Effect.START_REST


def test_wake_from_rest_returns_to_idle_hold():
    d = decide(Activity.RESTING, Intent.REST_OFF)
    assert d.ok
    assert d.activity is Activity.IDLE
    assert d.effect is Effect.STOP_REST


def test_teach_while_playing_is_refused():
    d = decide(Activity.PLAYING, Intent.TEACH_ON)
    assert not d.ok
    assert d.activity is Activity.PLAYING
    assert "teach" in d.reason


def test_play_while_playing_is_refused():
    """A stored sequence is a tape. Starting a second tape is a stop-then-play,
    not a silent swap — the operator names the stop."""
    d = decide(Activity.PLAYING, Intent.PLAY)
    assert not d.ok
    assert "already" in d.reason


def test_goto_while_playing_retargets():
    """Goto sets destination. A second card click replaces the destination;
    the motion algorithm (later) blends. The table already names the effect
    so Controller does not grow a special case."""
    d = decide(Activity.PLAYING, Intent.GOTO)
    assert d.ok
    assert d.activity is Activity.PLAYING
    assert d.effect is Effect.RETARGET


def test_goto_while_teaching_is_refused():
    d = decide(Activity.TEACHING, Intent.GOTO)
    assert not d.ok
    assert d.activity is Activity.TEACHING


def test_fault_from_playing_enters_safelock():
    d = decide(Activity.PLAYING, Intent.FAULT)
    assert d.ok
    assert d.activity is Activity.SAFELOCK
    assert d.effect is Effect.LOCK


def test_fault_from_idle_is_ignored():
    """Contact and disconnect do not judge an arm that is already still."""
    d = decide(Activity.IDLE, Intent.FAULT)
    assert not d.ok
    assert d.activity is Activity.IDLE


def test_unlock_from_safelock_returns_to_idle_without_resuming():
    d = decide(Activity.SAFELOCK, Intent.UNLOCK)
    assert d.ok
    assert d.activity is Activity.IDLE
    assert d.effect is Effect.UNLOCK


def test_play_from_safelock_is_refused():
    d = decide(Activity.SAFELOCK, Intent.PLAY)
    assert not d.ok
    assert d.activity is Activity.SAFELOCK


def test_finish_play_returns_to_idle():
    d = decide(Activity.PLAYING, Intent.FINISH)
    assert d.ok
    assert d.activity is Activity.IDLE
    assert d.effect is Effect.FINISH
