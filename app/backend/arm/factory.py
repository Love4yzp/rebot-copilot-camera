"""Choosing between the real arm and the simulator.

``--sim`` is explicit. A failed real-arm connect does not keep serving as if
the arm were live — that failure is silent in the UI and the arm never moves.
"""

from __future__ import annotations

import logging
import time
from typing import Callable

from .. import assets
from .base import ArmDriver
from .sim import SimArm

log = logging.getLogger(__name__)


class ArmUnavailable(RuntimeError):
    """Real arm did not connect and ``--sim`` was not requested."""


def create_arm(
    force_sim: bool = False,
    clock: Callable[[], float] | None = None,
) -> tuple[ArmDriver, bool]:
    """Return ``(arm, is_simulated)``.

    With ``force_sim`` the real arm is not even imported, so ``--sim`` works on
    a machine with no CAN stack at all. Without it, connect failure raises.
    """
    clock = clock or time.monotonic

    if force_sim:
        log.info("sim mode requested: using SimArm")
        return _sim_arm(clock), True

    try:
        from .session import ArmSession

        arm = ArmSession(clock=clock)
        arm.connect()
        log.info("using the real arm over CAN")
        return arm, False
    except Exception as exc:
        log.error("real arm unavailable (%s: %s) — refusing to start without --sim", type(exc).__name__, exc)
        raise ArmUnavailable(f"real arm unavailable: {exc}") from exc


def _sim_arm(clock: Callable[[], float]) -> SimArm:
    """A simulator wired for a running service, not for a test.

    ``self_driven`` is the difference: tests advance the simulation by calling
    ``step()`` on a fake clock, and nothing in the service ever would, so an
    arm built the test way sits frozen and every routine times out.
    """
    return SimArm(assets.joint_names(), clock=clock, self_driven=True)
