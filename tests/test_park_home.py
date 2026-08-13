"""Shutdown parking: the arm goes slowly back to zero before the process exits.

The park is an ordinary goto through the executor (speed limit, arrival
detection, stuck-abort all inherited), so what is tested here is the wiring
around it: playback/teaching are torn down first, an engaged stop latch
refuses the move and holds the frozen pose instead, and a stop engaged
*mid-park* still wins. The server-side piece is that repeated exit signals
cannot force-quit past the park.
"""

import signal
import socket

import pytest
import uvicorn

from backend.actions import InlineRunner, ShutterProvider
from backend.app import ParkOnExitServer, _ensure_port_free, _park_arm, app
from backend.arm import SimArm
from backend.core import Broadcaster, Controller, Phase
from backend.sequences import HoldBlock, Pose, Sequence
from backend.safety import LatchSource, SafetyLatch
from backend.shutter import SimShutter

JOINTS = ("joint1", "joint2")
START = {"joint1": 1.0, "joint2": -0.5}
DT = 0.01


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class Rig:
    def __init__(self) -> None:
        self.clock = FakeClock()
        self.arm = SimArm(JOINTS, clock=self.clock, initial=START, tau=0.05)
        self.arm.connect()
        self.latch = SafetyLatch(clock=self.clock)
        self.controller = Controller(
            arm=self.arm,
            shutter=SimShutter(),
            latch=self.latch,
            broadcaster=Broadcaster(),
            clock=self.clock,
            expected_period_s=DT,
            actions=InlineRunner([ShutterProvider(SimShutter())]),
        )

    def step(self, n: int = 1) -> None:
        for _ in range(n):
            self.clock.now += DT
            self.arm.step(DT)
            self.controller.tick()

    def finish_park(self, max_steps: int = 5000):
        executor = self.controller.executor
        assert executor is not None
        for _ in range(max_steps):
            if executor.is_finished:
                return executor
            self.step()
        raise AssertionError("park never finished")


def positions(rig: Rig) -> dict[str, float]:
    return dict(rig.arm.read_state().positions)


def test_park_from_idle_reaches_zero() -> None:
    rig = Rig()
    rig.step(5)

    assert rig.controller.park_home() is not None
    executor = rig.finish_park()

    assert executor.progress().phase is Phase.DONE
    for name, q in positions(rig).items():
        assert abs(q) <= 0.01, f"{name} not parked: {q}"


def test_park_aborts_running_playback() -> None:
    rig = Rig()
    rig.step(5)
    pose = Pose(name="far", joints={"joint1": -1.0, "joint2": 0.5})
    sequence = Sequence(name="s", blocks=[HoldBlock(pose_id=pose.id, duration_s=30.0)])
    running = rig.controller.play(sequence, {pose.id: pose})
    rig.step(5)

    assert rig.controller.park_home() is not None
    executor = rig.finish_park()

    assert running.progress().phase is Phase.ABORTED
    assert executor.progress().phase is Phase.DONE
    for name, q in positions(rig).items():
        assert abs(q) <= 0.01, f"{name} not parked: {q}"


def test_park_leaves_teaching() -> None:
    rig = Rig()
    rig.step(5)
    rig.controller.set_teaching(True)
    rig.step(5)

    assert rig.controller.park_home() is not None
    executor = rig.finish_park()

    assert not rig.controller.is_teaching
    assert not rig.arm.is_floating
    assert executor.progress().phase is Phase.DONE


def test_park_refused_while_latched() -> None:
    rig = Rig()
    rig.step(5)
    rig.latch.engage("operator", LatchSource.UI)
    rig.step(2)  # a latched tick records the freeze pose
    frozen = positions(rig)

    assert rig.controller.park_home() is None
    rig.step(20)

    assert rig.controller.executor is None
    assert positions(rig) == pytest.approx(frozen)


def test_latch_engaged_mid_park_wins() -> None:
    rig = Rig()
    rig.step(5)
    assert rig.controller.park_home() is not None
    rig.step(10)  # underway, somewhere between START and zero

    rig.latch.engage("operator", LatchSource.UI)
    rig.step(2)
    frozen = positions(rig)
    rig.step(50)

    executor = rig.controller.executor
    assert executor is not None
    assert executor.progress().phase is Phase.ABORTED
    # The arm froze where the stop caught it — it did not sneak on to zero.
    assert positions(rig) == pytest.approx(frozen)
    assert any(abs(q) > 0.01 for q in frozen.values())


def test_repeated_exit_signals_do_not_force_quit() -> None:
    server = ParkOnExitServer(uvicorn.Config(app))

    server.handle_exit(signal.SIGINT, None)
    server.handle_exit(signal.SIGINT, None)
    server.handle_exit(signal.SIGTERM, None)

    assert server.should_exit is True
    assert server.force_exit is False
    # Not re-raised after the loop: a clean park is a clean exit.
    assert server._captured_signals == []


def test_park_skipped_when_the_loop_never_started() -> None:
    """A startup that failed before the loop came up must not command the arm."""
    rig = Rig()  # never controller.start()'d — as in a failed startup

    _park_arm(rig.controller)

    assert rig.controller.executor is None
    assert positions(rig) == pytest.approx(START)


def test_port_already_serving_refuses_to_start() -> None:
    """A second instance must lose before it connects the arm over CAN."""
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen()
    port = blocker.getsockname()[1]
    try:
        with pytest.raises(SystemExit):
            _ensure_port_free("127.0.0.1", port)
    finally:
        blocker.close()
    # And the same check passes once the port is free again.
    _ensure_port_free("127.0.0.1", port)
