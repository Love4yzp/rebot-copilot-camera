"""Choosing between the real arm and the simulator.

Inherited from the previous generation of this service, where it was the single
most useful thing in the codebase: the whole application runs, and every
workflow above the arm can be exercised, with no hardware attached.

The rule is that falling back is always announced. A service that silently
switches to a simulator looks identical to one that is working, right up until
someone presses play expecting the arm to move.
"""

from __future__ import annotations

import logging
import time
from typing import Callable

from .. import assets
from .base import ArmDriver
from .sim import SimArm

log = logging.getLogger(__name__)


def create_arm(
    force_sim: bool = False,
    clock: Callable[[], float] | None = None,
) -> tuple[ArmDriver, bool]:
    """Return ``(arm, is_simulated)``.

    With ``force_sim`` the real arm is not even imported, so ``--sim`` works on
    a machine with no CAN stack at all.
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
        # Deliberately loud. This covers a missing CAN interface, a powered-down
        # arm, and a genuine bug in the arm layer, and the operator needs to see
        # which -- so the reason is logged rather than swallowed into "sim mode".
        log.warning("real arm unavailable (%s: %s) — falling back to SimArm", type(exc).__name__, exc)
        return _sim_arm(clock), True


def _sim_arm(clock: Callable[[], float]) -> SimArm:
    """A simulator wired for a running service, not for a test.

    ``self_driven`` is the difference: tests advance the simulation by calling
    ``step()`` on a fake clock, and nothing in the service ever would, so an
    arm built the test way sits frozen and every routine times out.
    """
    return SimArm(assets.joint_names(), clock=clock, self_driven=True)
