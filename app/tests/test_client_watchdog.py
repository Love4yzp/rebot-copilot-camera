"""ClientWatchdog at its public seam: feed / expired."""

from backend.safety import ClientWatchdog


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_expires_only_after_timeout():
    clock = FakeClock()
    wd = ClientWatchdog(clock, timeout_s=2.0)
    clock.now = 1.9
    assert wd.expired() is False
    clock.now = 2.0
    assert wd.expired() is True


def test_feed_resets_the_timer():
    clock = FakeClock()
    wd = ClientWatchdog(clock, timeout_s=2.0)
    clock.now = 1.5
    wd.feed()
    clock.now = 3.4
    assert wd.expired() is False
    clock.now = 3.5
    assert wd.expired() is True
