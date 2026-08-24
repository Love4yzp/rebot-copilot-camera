"""ContactObserver at its public seam: update() → trigger or not."""

from backend.safety import ContactObserver


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_disabled_observer_never_triggers():
    clock = FakeClock()
    obs = ContactObserver(threshold_nm=8.0, window_s=0.05, enabled=False, clock=clock)
    clock.now = 1.0
    assert obs.update([0] * 6, [0] * 6, [20] + [0] * 5) is False
    clock.now = 2.0
    assert obs.update([0] * 6, [0] * 6, [20] + [0] * 5) is False


def test_single_over_threshold_sample_does_not_lock():
    clock = FakeClock()
    obs = ContactObserver(threshold_nm=8.0, window_s=0.05, enabled=True, clock=clock)
    assert obs.update([0] * 6, [0] * 6, [20] + [0] * 5) is False
    assert obs.triggered is False


def test_sustained_window_triggers_once():
    clock = FakeClock()
    obs = ContactObserver(threshold_nm=8.0, window_s=0.05, enabled=True, clock=clock)
    obs.update([0] * 6, [0] * 6, [20] + [0] * 5)
    clock.now = 0.06
    assert obs.update([0] * 6, [0] * 6, [20] + [0] * 5) is True
    assert obs.update([0] * 6, [0] * 6, [20] + [0] * 5) is False


def test_drop_below_threshold_resets_the_window():
    clock = FakeClock()
    obs = ContactObserver(threshold_nm=8.0, window_s=0.05, enabled=True, clock=clock)
    obs.update([0] * 6, [0] * 6, [20] + [0] * 5)
    clock.now = 0.02
    obs.update([0] * 6, [0] * 6, [0] * 6)
    clock.now = 0.10
    assert obs.update([0] * 6, [0] * 6, [20] + [0] * 5) is False
