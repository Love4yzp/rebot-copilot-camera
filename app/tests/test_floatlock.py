"""Float/lock decision for drag teaching.

Velocity sequences are fed in directly. The thresholds will need retuning once
a camera hangs off the end effector, and these tests are what make that safe.
"""

import pytest

from backend.core.floatlock import FloatLock, FloatLockConfig

FAST = 0.20  # m/s — clearly dragging
SLOW = 0.001  # m/s — clearly still
DT = 0.01


@pytest.fixture
def lock() -> FloatLock:
    return FloatLock()


def feed(lock: FloatLock, speeds, start: float = 0.0, dt: float = DT) -> list[bool]:
    states = []
    now = start
    for speed in speeds:
        now += dt
        states.append(lock.update(speed, 0.0, now))
    return states


def test_starts_locked(lock: FloatLock):
    """An arm that goes free the instant teaching is enabled sags before anyone
    has hold of it."""
    assert lock.is_following is False


def test_a_clear_push_releases_immediately(lock: FloatLock):
    assert lock.update(FAST, 0.0, 0.0) is True


def test_angular_motion_alone_releases(lock: FloatLock):
    """Twisting the wrist in place barely moves the end effector linearly."""
    assert lock.update(0.0, 0.5, 0.0) is True


def test_staying_slow_never_releases(lock: FloatLock):
    assert not any(feed(lock, [SLOW] * 500))


def test_releasing_then_stopping_locks_after_the_still_time(lock: FloatLock):
    lock.update(FAST, 0.0, 0.0)

    states = feed(lock, [SLOW] * 100, start=0.0)

    assert states[0] is True, "does not lock on the first slow sample"
    assert states[-1] is False, "locks once the arm has actually stopped"


def test_locking_waits_the_configured_still_time(lock: FloatLock):
    lock.update(FAST, 0.0, 0.0)

    now = 0.0
    while lock.update(SLOW, 0.0, now):
        now += DT

    assert now >= FloatLockConfig().min_still_s


def test_a_direction_change_does_not_lock_mid_drag(lock: FloatLock):
    """Hand motion passes through zero velocity at every reversal. Locking
    there would stop the arm dead halfway through a move."""
    lock.update(FAST, 0.0, 0.0)

    # Fast, brief stall, fast again — a reversal.
    states = feed(lock, [FAST] * 20 + [SLOW] * 5 + [FAST] * 20)

    assert all(states), "reversal must not lock the arm"


def test_hovering_at_the_threshold_does_not_chatter(lock: FloatLock):
    """A hand resting on a stationary arm sits right at the boundary. Without
    hysteresis the arm alternates several times a second and feels like it is
    fighting back."""
    config = FloatLockConfig()
    borderline = config.linear_threshold * 0.8  # inside the hysteresis band

    lock.update(FAST, 0.0, 0.0)  # released
    states = feed(lock, [borderline] * 200)
    assert all(states), "must not lock while inside the band"

    lock.reset()
    states = feed(lock, [borderline] * 200)
    assert not any(states), "must not release while inside the band"


def test_the_band_has_a_real_width():
    config = FloatLockConfig()
    assert config.lock_factor < config.release_factor


def test_reset_returns_to_locked(lock: FloatLock):
    lock.update(FAST, 0.0, 0.0)
    assert lock.is_following is True

    lock.reset()
    assert lock.is_following is False


def test_thresholds_are_configurable_for_the_camera_payload():
    """Upstream's numbers are for an unloaded arm; a Canon body changes them."""
    heavy = FloatLock(FloatLockConfig(linear_threshold=0.15, angular_threshold=0.3))

    assert heavy.update(0.10, 0.0, 0.0) is False, "would have released unloaded"
    assert heavy.update(0.20, 0.0, 0.1) is True


def test_a_full_teach_gesture(lock: FloatLock):
    """Grab, drag with a wobble, hold still, let go."""
    gesture = [SLOW] * 10 + [FAST] * 50 + [SLOW] * 3 + [FAST] * 50 + [SLOW] * 100
    states = feed(lock, gesture)

    assert states[5] is False, "still locked before the grab"
    assert states[30] is True, "following during the drag"
    assert states[62] is True, "the wobble did not lock it"
    assert states[-1] is False, "locked once released"
