"""Watchdog trip conditions.

Fake clock plus hand-fed observations — the point of keeping the watchdog free
of hardware is that all three conditions are reachable without any.
"""

import pytest

from backend.safety import LatchSource, SafetyLatch, Watchdog, WatchdogConfig

PERIOD = 0.01


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, dt: float) -> None:
        self.now += dt


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def latch(clock: FakeClock) -> SafetyLatch:
    return SafetyLatch(clock=clock)


@pytest.fixture
def dog(latch: SafetyLatch, clock: FakeClock) -> Watchdog:
    return Watchdog(latch, clock=clock, config=WatchdogConfig())


# ── late ticks ───────────────────────────────────────────────────────────────


def test_on_time_ticks_never_trip(dog: Watchdog, latch: SafetyLatch, clock: FakeClock):
    for _ in range(500):
        clock.advance(PERIOD)
        dog.observe_tick(PERIOD)
    assert latch.is_latched is False


def test_a_single_late_tick_is_tolerated(dog: Watchdog, latch: SafetyLatch, clock: FakeClock):
    """Jitter happens. One hiccup must not stop a shoot."""
    clock.advance(PERIOD)
    dog.observe_tick(PERIOD)

    clock.advance(PERIOD * 20)
    dog.observe_tick(PERIOD)

    clock.advance(PERIOD)
    dog.observe_tick(PERIOD)

    assert latch.is_latched is False


def test_sustained_lateness_engages(dog: Watchdog, latch: SafetyLatch, clock: FakeClock):
    clock.advance(PERIOD)
    dog.observe_tick(PERIOD)

    for _ in range(20):
        clock.advance(PERIOD * 10)
        dog.observe_tick(PERIOD)
        if latch.is_latched:
            break

    assert latch.is_latched is True
    assert latch.snapshot().source is LatchSource.WATCHDOG
    assert "running late" in latch.snapshot().reason


def test_the_first_tick_cannot_trip(dog: Watchdog, latch: SafetyLatch, clock: FakeClock):
    """There is no previous tick to measure a gap against."""
    clock.advance(1000.0)
    dog.observe_tick(PERIOD)
    assert latch.is_latched is False


# ── read failures ────────────────────────────────────────────────────────────


def test_isolated_read_failures_do_not_trip(dog: Watchdog, latch: SafetyLatch):
    for _ in range(50):
        dog.observe_read(ok=False)
        dog.observe_read(ok=False)
        dog.observe_read(ok=True)
    assert latch.is_latched is False


def test_consecutive_read_failures_engage(dog: Watchdog, latch: SafetyLatch):
    """A run of them means the loop is commanding an arm whose position it no
    longer knows."""
    for _ in range(WatchdogConfig().max_read_failures):
        dog.observe_read(ok=False)

    assert latch.is_latched is True
    assert "consecutive arm read failures" in latch.snapshot().reason


# ── drift under hold ─────────────────────────────────────────────────────────


def test_holding_accurately_never_trips(dog: Watchdog, latch: SafetyLatch, clock: FakeClock):
    for _ in range(500):
        clock.advance(PERIOD)
        dog.observe_hold({"joint1": 0.5001}, {"joint1": 0.5})
    assert latch.is_latched is False


def test_brief_drift_is_tolerated(dog: Watchdog, latch: SafetyLatch, clock: FakeClock):
    for _ in range(10):
        clock.advance(PERIOD)
        dog.observe_hold({"joint1": 1.0}, {"joint1": 0.5})

    clock.advance(PERIOD)
    dog.observe_hold({"joint1": 0.5}, {"joint1": 0.5})
    clock.advance(1.0)
    dog.observe_hold({"joint1": 0.5}, {"joint1": 0.5})

    assert latch.is_latched is False


def test_sustained_drift_under_hold_engages(dog: Watchdog, latch: SafetyLatch, clock: FakeClock):
    """Drift under a hold means torque was lost or something is pushing."""
    for _ in range(200):
        clock.advance(PERIOD)
        dog.observe_hold({"joint1": 1.0, "joint2": 0.0}, {"joint1": 0.5, "joint2": 0.0})
        if latch.is_latched:
            break

    assert latch.is_latched is True
    reason = latch.snapshot().reason
    assert "joint1" in reason, "the reason must name the joint that let go"
    assert "drifted" in reason


def test_not_holding_clears_the_drift_timer(dog: Watchdog, latch: SafetyLatch, clock: FakeClock):
    """During a move a large error is the whole point, not a fault."""
    for _ in range(200):
        clock.advance(PERIOD)
        dog.observe_hold(None, None)
    assert latch.is_latched is False


# ── interaction with the latch ───────────────────────────────────────────────


def test_watchdog_keeps_an_earlier_manual_reason(dog: Watchdog, latch: SafetyLatch):
    latch.engage("operator pressed stop", LatchSource.UI)

    for _ in range(50):
        dog.observe_read(ok=False)

    assert latch.snapshot().reason == "operator pressed stop"
    assert latch.snapshot().source is LatchSource.UI


def test_reset_forgets_accumulated_suspicion(dog: Watchdog, latch: SafetyLatch):
    for _ in range(WatchdogConfig().max_read_failures - 1):
        dog.observe_read(ok=False)

    dog.reset()
    dog.observe_read(ok=False)

    assert latch.is_latched is False
