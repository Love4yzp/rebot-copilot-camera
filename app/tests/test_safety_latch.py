"""SafetyLatch behaviour.

The emergency stop is the one part of this project where being wrong damages
hardware or hurts someone, so it is tested before anything can move.
"""

import threading

import pytest

from backend.safety import LatchSource, SafetyLatch


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
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


def test_starts_clear(latch: SafetyLatch):
    assert latch.is_latched is False
    snap = latch.snapshot()
    assert snap.latched is False
    assert snap.reason is None
    assert snap.freeze_pose is None


def test_engage_records_reason_source_and_time(latch: SafetyLatch, clock: FakeClock):
    clock.now = 1234.5
    assert latch.engage("operator pressed stop", LatchSource.UI) is True

    snap = latch.snapshot()
    assert snap.latched is True
    assert snap.reason == "operator pressed stop"
    assert snap.source is LatchSource.UI
    assert snap.engaged_at == 1234.5


def test_reengage_keeps_the_first_reason(latch: SafetyLatch):
    """The first cause is the diagnostic one; later ones are usually symptoms."""
    latch.engage("tracking error exceeded 0.3 rad on joint2", LatchSource.WATCHDOG)
    assert latch.engage("operator pressed stop", LatchSource.UI) is False

    snap = latch.snapshot()
    assert snap.reason == "tracking error exceeded 0.3 rad on joint2"
    assert snap.source is LatchSource.WATCHDOG


def test_engage_requires_a_reason(latch: SafetyLatch):
    with pytest.raises(ValueError):
        latch.engage("", LatchSource.API)
    assert latch.is_latched is False


def test_clear_is_idempotent_when_not_latched(latch: SafetyLatch):
    assert latch.clear() is False
    assert latch.clear() is False
    assert latch.is_latched is False


def test_clear_releases_and_wipes_state(latch: SafetyLatch):
    latch.engage("CAN read failed 5 times", LatchSource.WATCHDOG)
    latch.record_freeze_pose({"joint1": 0.1})

    assert latch.clear() is True

    snap = latch.snapshot()
    assert snap.latched is False
    assert snap.reason is None
    assert snap.source is None
    assert snap.engaged_at is None
    assert snap.freeze_pose is None


def test_can_engage_again_after_clear(latch: SafetyLatch):
    latch.engage("first", LatchSource.API)
    latch.clear()
    assert latch.engage("second", LatchSource.API) is True
    assert latch.snapshot().reason == "second"


def test_freeze_pose_is_recorded_by_the_first_tick_only(latch: SafetyLatch):
    latch.engage("stop", LatchSource.UI)
    assert latch.snapshot().freeze_pose is None, "no tick has happened yet"

    latch.record_freeze_pose({"joint1": 0.5, "joint2": 1.0})
    latch.record_freeze_pose({"joint1": 9.9, "joint2": 9.9})

    assert latch.snapshot().freeze_pose == {"joint1": 0.5, "joint2": 1.0}


def test_freeze_pose_is_ignored_when_not_latched(latch: SafetyLatch):
    """A tick racing a clear must not resurrect stale state."""
    latch.record_freeze_pose({"joint1": 0.5})
    assert latch.snapshot().freeze_pose is None


def test_snapshot_does_not_alias_internal_state(latch: SafetyLatch):
    pose = {"joint1": 0.5}
    latch.engage("stop", LatchSource.UI)
    latch.record_freeze_pose(pose)

    pose["joint1"] = 99.0  # caller mutates their own dict
    snap = latch.snapshot()
    snap.freeze_pose["joint1"] = -99.0  # caller mutates the snapshot

    assert latch.snapshot().freeze_pose == {"joint1": 0.5}


def test_concurrent_engage_has_exactly_one_winner(latch: SafetyLatch):
    """The control loop reads this every tick while requests write to it."""
    winners: list[bool] = []
    barrier = threading.Barrier(8)

    def worker(n: int) -> None:
        barrier.wait()
        winners.append(latch.engage(f"reason {n}", LatchSource.API))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(winners) == 1
    assert latch.is_latched is True
