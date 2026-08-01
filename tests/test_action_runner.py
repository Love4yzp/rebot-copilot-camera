"""Provider work must not happen on the control loop.

The claim under test is one sentence: a provider that blocks costs the control
loop nothing. Everything else here exists because that claim has to survive the
ways providers actually misbehave — hanging forever, raising something
unexpected, finishing long after anyone was still waiting.

No test sleeps. Blocking providers block on an Event the test controls, and
timeouts are reached by moving an injected clock, so the only real concurrency
is the worker thread itself.
"""

import threading

import pytest
from pydantic import BaseModel

from backend.actions import (
    ActionContext,
    ActionTimeout,
    ActionUnavailable,
    InlineRunner,
    ShutterProvider,
    ThreadedRunner,
)
from backend.arm import SimArm
from backend.core import Broadcaster, Controller
from backend.routines import Routine, ShutterAction, Waypoint
from backend.safety import SafetyLatch, Watchdog
from backend.shutter import SimShutter

JOINTS = ("joint1", "joint2")
DT = 0.01
#: Real seconds to wait on a worker thread before calling the test itself
#: broken. Generous: it only bounds failure, it is never reached when passing.
JOIN_S = 5.0


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class Params(BaseModel):
    pass


class BlockingProvider:
    """Waits until the test lets it through, then does what it was told."""

    id = "blocker"
    label = "blocker"
    params_model = Params
    retryable = True

    def __init__(self, raises: Exception | None = None) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self.calls = 0
        self._raises = raises

    def fields(self):
        return []

    def probe(self) -> None:
        pass

    def run(self, params, ctx) -> None:
        self.calls += 1
        self.entered.set()
        self.release.wait(JOIN_S)
        try:
            if self._raises is not None:
                raise self._raises
        finally:
            self.finished.set()


def ctx() -> ActionContext:
    return ActionContext(
        routine_id="r", routine_name="r", waypoint_index=0, waypoint_note="", joints={}
    )


def submit(runner, provider_id="blocker", timeout_s=10.0):
    return runner.submit(provider_id, Params(), ctx(), timeout_s)


# ── the point of the whole module ────────────────────────────────────────────


def test_a_blocking_provider_does_not_stall_the_control_loop():
    """The reason this layer exists.

    Before it, the executor called the shutter driver from inside
    Controller.tick(). Esp32Shutter.shoot() waits up to six seconds for a
    camera waking over BLE, so a burst against a slow camera stopped the loop
    for seconds at a time — and the loop is what holds a 48 V arm up.
    """
    clock = FakeClock()
    provider = BlockingProvider()
    runner = ThreadedRunner([provider], clock=clock)
    arm = SimArm(JOINTS, clock=clock, tau=0.05)
    arm.connect()
    latch = SafetyLatch(clock=clock)
    controller = Controller(
        arm=arm,
        shutter=SimShutter(),
        latch=latch,
        broadcaster=Broadcaster(),
        clock=clock,
        watchdog=Watchdog(latch, clock=clock),
        expected_period_s=DT,
        actions=runner,
    )

    job = submit(runner, timeout_s=1000.0)
    assert provider.entered.wait(JOIN_S), "the worker never picked the job up"

    # A thousand ticks while the provider sits there. Each one must complete.
    for _ in range(1000):
        clock.now += DT
        arm.step(DT)
        controller.tick()

    assert not job.done, "the job resolved without the provider being released"
    assert not latch.is_latched, "a slow provider must not look like a lost arm"
    assert controller.rate_hz > 0

    provider.release.set()
    assert provider.finished.wait(JOIN_S)
    runner.close()


def test_a_slow_shutter_no_longer_trips_the_watchdog():
    """The concrete regression: a camera that is merely slow used to end the
    shoot. Two stalls past the watchdog's late-tick grace engage the stop."""
    clock = FakeClock()
    provider = BlockingProvider()
    runner = ThreadedRunner([provider], clock=clock)
    latch = SafetyLatch(clock=clock)
    watchdog = Watchdog(latch, clock=clock)

    submit(runner, timeout_s=1000.0)
    assert provider.entered.wait(JOIN_S)

    # Ticks keep arriving on time even though the provider has not returned.
    for _ in range(500):
        clock.now += DT
        watchdog.observe_tick(DT)

    assert not latch.is_latched
    provider.release.set()
    runner.close()


# ── giving up ────────────────────────────────────────────────────────────────


def test_a_job_gives_up_at_its_deadline_while_the_thread_runs_on():
    """Python cannot kill a thread, so the timeout is judged by the reader.

    Pretending the provider was stopped would be a lie the operator pays for:
    the next thing that talks to that hardware would find it mid-exchange.
    """
    clock = FakeClock()
    provider = BlockingProvider()
    runner = ThreadedRunner([provider], clock=clock)

    job = submit(runner, timeout_s=5.0)
    assert provider.entered.wait(JOIN_S)
    assert not job.done

    clock.now += 5.0
    assert job.done
    assert isinstance(job.error, ActionTimeout)
    assert not provider.finished.is_set(), "the provider is still running, as expected"

    provider.release.set()
    assert provider.finished.wait(JOIN_S)
    runner.close()


def test_a_provider_still_busy_refuses_the_next_action_instead_of_queueing():
    """The operator is waiting for an answer, not for the last anchor's work."""
    clock = FakeClock()
    provider = BlockingProvider()
    runner = ThreadedRunner([provider], clock=clock)

    first = submit(runner, timeout_s=1.0)
    assert provider.entered.wait(JOIN_S)
    clock.now += 1.0
    assert first.done and isinstance(first.error, ActionTimeout)

    second = submit(runner)
    assert second.done
    assert isinstance(second.error, ActionUnavailable)
    assert provider.calls == 1, "the second action was never handed to the provider"

    provider.release.set()
    assert provider.finished.wait(JOIN_S)
    runner.close()


def test_a_late_result_cannot_overwrite_a_job_that_already_timed_out():
    clock = FakeClock()
    provider = BlockingProvider()
    runner = ThreadedRunner([provider], clock=clock)

    job = submit(runner, timeout_s=1.0)
    assert provider.entered.wait(JOIN_S)
    clock.now += 1.0
    assert isinstance(job.error, ActionTimeout)

    provider.release.set()
    assert provider.finished.wait(JOIN_S)

    assert isinstance(job.error, ActionTimeout), "the late success rewrote the outcome"
    runner.close()


# ── misbehaving providers ────────────────────────────────────────────────────


def test_an_unexpected_exception_becomes_a_failed_job_not_a_dead_worker():
    clock = FakeClock()
    provider = BlockingProvider(raises=ValueError("third-party code did something"))
    runner = ThreadedRunner([provider], clock=clock)

    provider.release.set()
    job = submit(runner, timeout_s=1000.0)
    assert job.wait(JOIN_S), "the worker never resolved the job"

    assert job.done
    assert isinstance(job.error, ValueError)

    # And the worker is still alive for the next one.
    provider.entered.clear()
    second = submit(runner, timeout_s=1000.0)
    assert second.wait(JOIN_S)
    assert provider.calls == 2
    runner.close()


def test_an_unknown_provider_fails_the_action_rather_than_raising():
    """One path for "the action did not work", not two."""
    runner = ThreadedRunner([], clock=FakeClock())
    job = submit(runner, provider_id="nobody")

    assert job.done
    assert isinstance(job.error, ActionUnavailable)
    assert "nobody" in str(job.error)
    runner.close()


# ── the shutter, as a provider ───────────────────────────────────────────────


def test_the_shutter_provider_fires_one_frame_per_call():
    """Bursts are paced by the executor, not by the provider, so that an
    emergency stop lands between frames."""
    shutter = SimShutter()
    provider = ShutterProvider(shutter)
    runner = InlineRunner([provider])

    runner.submit("shutter", provider.params_model(count=5), ctx(), 5.0)

    assert shutter.shots == 1
    assert shutter.focuses == 1


def test_the_shutter_provider_probes_without_burning_a_frame():
    shutter = SimShutter()
    ShutterProvider(shutter).probe()

    assert shutter.pings == 1
    assert shutter.shots == 0


def test_a_provider_is_told_where_it_is_but_given_no_way_to_move():
    """A provider that cannot reach the arm cannot be the reason it moved."""
    seen: list[ActionContext] = []

    class Recorder(BlockingProvider):
        def run(self, params, c):
            seen.append(c)

    provider = Recorder()
    InlineRunner([provider]).submit(
        "blocker",
        Params(),
        ActionContext(
            routine_id="rid",
            routine_name="round the subject",
            waypoint_index=2,
            waypoint_note="正面",
            joints={"joint1": 0.4},
        ),
        5.0,
    )

    assert seen[0].waypoint_note == "正面"
    assert seen[0].joints == {"joint1": 0.4}
    assert not hasattr(seen[0], "arm")
    assert not hasattr(seen[0], "latch")


# ── through the executor ─────────────────────────────────────────────────────


def test_an_abort_mid_action_abandons_the_job_and_never_acts_on_it():
    """A serial write already on its way cannot be recalled. What stops is
    anything happening because of how it turns out."""
    from backend.core import Phase, RoutineExecutor

    clock = FakeClock()
    provider = BlockingProvider()
    runner = ThreadedRunner([provider], clock=clock)
    arm = SimArm(JOINTS, clock=clock, tau=0.05)
    arm.connect()

    # The executor asks for "shutter"; give that name to a provider that hangs.
    provider.id = "shutter"
    runner.register(provider)
    routine = Routine(
        name="x",
        waypoints=[
            Waypoint(
                joints={"joint1": 0.1, "joint2": 0.0},
                settle_ms=0,
                actions=[ShutterAction(timeout_s=120)],
            ),
            Waypoint(joints={"joint1": 0.5, "joint2": 0.0}),
        ],
    )
    executor = RoutineExecutor(routine, arm=arm, actions=runner, clock=clock)

    executor.start()
    for _ in range(2000):
        clock.now += DT
        arm.step(DT)
        executor.tick()
        if executor.phase is Phase.ACTING:
            break
    assert executor.phase is Phase.ACTING, "never reached the action"
    # Wait on the worker without moving the clock: the deadline must not pass
    # while the test is only waiting for the OS to schedule a thread.
    assert provider.entered.wait(JOIN_S), "the action was never handed over"

    executor.abort("emergency stop engaged")
    assert executor.phase is Phase.ABORTED

    provider.release.set()
    assert provider.finished.wait(JOIN_S)

    for _ in range(1000):
        clock.now += DT
        arm.step(DT)
        executor.tick()

    assert executor.phase is Phase.ABORTED
    assert executor.progress().waypoint_index == 0, "the late result moved the routine on"
    runner.close()


@pytest.mark.parametrize("runner_factory", [InlineRunner, ThreadedRunner])
def test_both_runners_report_a_missing_provider_the_same_way(runner_factory):
    runner = runner_factory([])
    job = runner.submit("gone", Params(), ctx(), 1.0)

    assert job.done
    assert isinstance(job.error, ActionUnavailable)
    runner.close()
