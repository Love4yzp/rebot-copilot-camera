"""The real arm: a thin wrapper over upstream's ``RebotArm``.

Thin is the point. Kinematics, dynamics, gravity compensation and trajectory
planning all live upstream and are called, not reimplemented. What this adds is
only the shape the rest of this project programs against -- dict-keyed joints
instead of positional numpy arrays, and hold/move as separate verbs.

The dict/array boundary is here on purpose. Upstream speaks ``np.ndarray``
indexed by joint order; everything above speaks ``{"joint1": 0.1, ...}``. A
silent off-by-one in that mapping commands the wrong joint, so the conversion
happens in exactly one place and is checked against the arm's own reported
joint names.

**This module never disables the motors.** Upstream's ``estop()`` forwards to
``disable_all()``, which cuts torque and drops a 48 V arm holding a camera.
Emergency stop in this project is a hold — see backend/safety/latch.py and
docs/HARDWARE_NOTES.md.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Mapping, Sequence

import numpy as np

from .. import assets
from ..safety.kinematics import ARM_JOINTS
from .base import ArmState

log = logging.getLogger(__name__)

#: MIT-mode gains for holding position. Overridden per joint from the hardware
#: yaml when it has them; these are the fallback.
DEFAULT_HOLD_KP = 50.0
DEFAULT_HOLD_KD = 3.0


class ArmSession:
    """Implements :class:`~backend.arm.base.ArmDriver` against real hardware."""

    def __init__(
        self,
        hardware_yaml: str | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        import time

        from reBotArm_control_py.actuator.rebotarm import RebotArm

        assets.assert_rs_model()
        self._clock = clock or time.monotonic
        self._arm = RebotArm(hardware_yaml or str(assets.HARDWARE_YAML))
        self._lock = threading.RLock()
        self._connected = False
        self._floating = False

        self._names: tuple[str, ...] = tuple(self._arm.joint_names)
        self._index = {name: i for i, name in enumerate(self._names)}
        self._verify_joint_names()

        # Velocity is finite-differenced, never read from the motor: mechVel
        # (0x701A) is not rad/s on this firmware, and the float/lock decision
        # rides on velocity. See docs/HARDWARE_NOTES.md.
        self._prev_q: np.ndarray | None = None
        self._prev_t: float | None = None
        self._velocities: dict[str, float] = {}

        self._dyn_model = None
        self._dyn_data = None

    def _verify_joint_names(self) -> None:
        expected = tuple(assets.joint_names())
        if self._names != expected:
            raise RuntimeError(
                "arm joint order does not match the hardware config: "
                f"arm reports {self._names}, config says {expected}. "
                "Commanding through a mismatched mapping moves the wrong joint."
            )

    # ── ArmDriver ────────────────────────────────────────────────────────────

    @property
    def joint_names(self) -> Sequence[str]:
        return self._names

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return self._connected

    def connect(self) -> None:
        with self._lock:
            self._arm.connect()
            self._arm.enable_all()
            self._connected = True
            log.info("arm connected: %d joints at %.0f Hz", self._arm.num_joints, self._arm.rate)

    def disconnect(self) -> None:
        """Release the bus.

        Note what this does *not* do: it does not disable the motors. Dropping
        the bus leaves the arm holding its last commanded pose, which is the
        safe end state for an arm carrying a camera.
        """
        with self._lock:
            self._arm.disconnect()
            self._connected = False

    def read_state(self) -> ArmState:
        with self._lock:
            q, _, _ = self._arm.get_state()
            now = self._clock()

            if self._prev_q is not None and self._prev_t is not None and now > self._prev_t:
                dt = now - self._prev_t
                self._velocities = {
                    name: float((q[i] - self._prev_q[i]) / dt) for name, i in self._index.items()
                }
            self._prev_q, self._prev_t = np.array(q, copy=True), now

            return ArmState(
                positions={name: float(q[i]) for name, i in self._index.items()},
                velocities=dict(self._velocities),
                t=now,
            )

    def hold(self, q_target: Mapping[str, float]) -> None:
        """Pin the arm at ``q_target`` with MIT stiffness plus gravity feedforward.

        Also the emergency-stop path. Every tick under an engaged stop calls
        this with the frozen pose, which is what keeps the arm up.
        """
        with self._lock:
            self._floating = False
            self._send_mit(self._to_array(q_target), kp=DEFAULT_HOLD_KP, kd=DEFAULT_HOLD_KD)

    def move_to(self, q_target: Mapping[str, float], duration_s: float) -> None:
        """Travel to ``q_target`` over roughly ``duration_s``.

        Uses POS_VEL with the velocity limit derived from the distance and the
        time, rather than trajectory planning: playback poses are already
        collision-checked as a sequence, and a velocity-limited point-to-point
        move is what the hardware does natively.
        """
        if duration_s <= 0:
            raise ValueError("duration_s must be positive")

        with self._lock:
            self._floating = False
            target = self._to_array(q_target)
            current, _, _ = self._arm.get_state()
            vlim = np.maximum(np.abs(target - current) / duration_s, 1e-3)

            for group in self._arm.groups.values():
                group.send_pos_vel(target, vlim)

    def set_float(self, enabled: bool) -> None:
        """Enter or leave zero-force float.

        Leaving re-asserts a hold at wherever the arm currently is, which is the
        "let go and it stays put" behaviour. Getting that wrong means the arm
        snaps back to a stale target the moment the operator releases it.
        """
        with self._lock:
            self._floating = enabled
            if not enabled:
                q, _, _ = self._arm.get_state()
                self._send_mit(q, kp=DEFAULT_HOLD_KP, kd=DEFAULT_HOLD_KD)

    @property
    def is_floating(self) -> bool:
        with self._lock:
            return self._floating

    # ── internals ────────────────────────────────────────────────────────────

    def _to_array(self, joints: Mapping[str, float]) -> np.ndarray:
        """Map a joint dict onto upstream's positional array.

        Unmentioned joints keep their current commanded value rather than
        defaulting to zero — a missing key must not mean "go to the rest pose".
        """
        current, _, _ = self._arm.get_state()
        target = np.array(current, copy=True, dtype=float)

        unknown = set(joints) - set(self._index)
        if unknown:
            raise KeyError(f"unknown joints: {sorted(unknown)}")

        for name, value in joints.items():
            target[self._index[name]] = value
        return target

    def _send_mit(self, q: np.ndarray, kp: float, kd: float) -> None:
        n = len(q)
        for group in self._arm.groups.values():
            group.send_mit(
                q,
                vel=np.zeros(n),
                kp=np.full(n, kp),
                kd=np.full(n, kd),
                tau=self._gravity_torque(q),
            )

    def _dynamics_model(self):
        """The RS dynamics model, loaded once.

        The URDF path is passed explicitly and always. Upstream's default
        resolves through ``config/rebotarm.yaml`` to the **B601-DM** arm and
        loads without raising, which would silently feed forward another
        robot's gravity — wrong torques, no error.
        """
        if self._dyn_model is None:
            from reBotArm_control_py.dynamics.robot_model import load_dynamics_model
            from reBotArm_control_py.dynamics.inverse_dynamics import create_data

            self._dyn_model = load_dynamics_model(str(assets.urdf_path()))
            self._dyn_data = create_data(self._dyn_model)
        return self._dyn_model

    def _gravity_torque(self, q: np.ndarray) -> np.ndarray:
        """Gravity feedforward, mapped from URDF DOFs onto hardware joints.

        The model has eight degrees of freedom (``joint1``..``joint6`` plus two
        prismatic gripper fingers); the hardware has seven (one gripper motor).
        Only the six arm joints line up. The gripper gets zero feedforward
        rather than a fabricated value: there is no calibrated mapping from
        finger travel to motor torque, and inventing one would put a made-up
        number into a torque command.
        """
        from reBotArm_control_py.dynamics.inverse_dynamics import compute_generalized_gravity

        model = self._dynamics_model()
        # Pass only the arm joints; upstream pads the gripper fingers out to the
        # model's eight DOFs. Handing it all seven hardware values would put the
        # gripper motor angle where a finger's metre-valued travel belongs.
        arm_q = np.array([q[self._index[name]] for name in ARM_JOINTS], dtype=float)
        g = compute_generalized_gravity(model, arm_q, self._dyn_data)

        tau = np.zeros(len(self._names), dtype=float)
        for position, name in enumerate(ARM_JOINTS):
            tau[self._index[name]] = float(g[position])
        return tau
