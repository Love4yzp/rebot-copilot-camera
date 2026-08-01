"""The arm interface everything else programs against.

Deliberately small. The heavy lifting -- kinematics, dynamics, gravity
compensation, trajectory planning -- lives upstream in ``reBotArm_control_py``
and is called directly; this Protocol only covers the handful of operations the
modes and the executor need from *an arm*, so that :class:`~backend.arm.sim.SimArm`
can stand in for the real one.

Note ``ArmState.velocities``: velocity is **finite-differenced from position**,
never read from the motor's velocity register. On this firmware ``mechVel
(0x701A)`` is not rad/s, and the float/lock decision in teach mode is driven by
end-effector velocity, so reading it would make the arm fail to lock or lock too
early. SimArm differences positions for the same reason -- logic developed
against the simulator then behaves the same on hardware.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol, Sequence, runtime_checkable


@dataclass(frozen=True)
class ArmState:
    """One sample of the arm, as of ``t``."""

    #: Joint angles in radians, keyed by hardware joint name.
    positions: Mapping[str, float]
    #: Joint velocities in rad/s, finite-differenced from position. Empty on
    #: the very first sample, when there is no previous position to difference.
    velocities: Mapping[str, float] = field(default_factory=dict)
    #: Timestamp from the injected clock, not from ``time.time`` directly.
    t: float = 0.0


@runtime_checkable
class ArmDriver(Protocol):
    """What a real arm and a simulated arm both have to provide."""

    @property
    def joint_names(self) -> Sequence[str]:
        """Hardware joint order: ``joint1``..``joint6`` then ``gripper``."""
        ...

    @property
    def is_connected(self) -> bool: ...

    def connect(self) -> None: ...

    def disconnect(self) -> None:
        """Release the bus. Must not drop the arm — see docs/HARDWARE_NOTES.md."""
        ...

    def read_state(self) -> ArmState: ...

    def hold(self, q_target: Mapping[str, float]) -> None:
        """Pin the arm at ``q_target``, immediately (MIT + gravity feedforward).

        This is also the emergency-stop path: while the latch is engaged the
        control loop keeps calling ``hold`` with the frozen pose. Holding is
        what keeps the arm up, so this must never be swapped for a disable.
        """
        ...

    def move_to(self, q_target: Mapping[str, float], duration_s: float) -> None:
        """Travel to ``q_target`` over roughly ``duration_s``.

        Distinct from :meth:`hold` on purpose. Holding is "be here now" and is
        what the emergency stop uses; moving is "get there over this long" and
        is what playback uses. Collapsing them would make a stop indistinguishable
        from a very fast move.

        On real hardware this defers to upstream's trajectory planner.
        """
        ...

    def set_float(self, enabled: bool) -> None:
        """Enter or leave zero-force float, for drag teaching.

        While floating, the arm compensates gravity but does not resist being
        pushed. Leaving float re-asserts a hold at wherever it currently is.
        """
        ...
