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
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING

import numpy as np

from .. import assets
from .base import ArmState
from .limits import expanded_joint_bounds
from .profile import DEFAULT_LIMITS, MotionLimits, PreparedMotion, prepare_profiles

if TYPE_CHECKING:
    from ..tuning import PayloadTuning

log = logging.getLogger(__name__)

#: Deployed MIT hold gains. Hardware YAML gains are not consumed in this path.
DEFAULT_HOLD_KP = 50.0
DEFAULT_HOLD_KD = 3.0

#: MIT-mode gains while floating: near-zero stiffness so the operator's hand
#: moves the arm, while gravity feedforward carries the load. Values from
#: upstream's example/9_gravity_compensation.py, tuned on the *unloaded* arm —
#: expect to retune with the camera mounted (docs/HARDWARE_NOTES.md, B2).
FLOAT_KP = 2.0
FLOAT_KD = 1.0


class ArmSession:
    """Implements :class:`~backend.arm.base.ArmDriver` against real hardware."""

    def __init__(
        self,
        hardware_yaml: str | None = None,
        clock: Callable[[], float] | None = None,
        transport: object | None = None,
        model_path: str | None = None,
    ) -> None:
        import time

        assets.assert_rs_model()
        self._clock = clock or time.monotonic
        if transport is None:
            from reBotArm_control_py.actuator.rebotarm import RebotArm

            transport = RebotArm(hardware_yaml or str(assets.effective_hardware_yaml()))
        self._arm = transport
        self.model_path = model_path
        self.model_locked = model_path is not None
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
        self._payload: PayloadTuning | None = None
        self._float_kp = FLOAT_KP
        self._float_kd = FLOAT_KD
        #: Per-joint gravity feedforward correction (tau = scale * g + bias),
        #: mirroring upstream auto_float_test's --k/--c. Empty = identity.
        self._gravity_scale: dict[str, float] = {}
        self._gravity_bias: dict[str, float] = {}

        #: Active point-to-point profile: (q0, target, t0, duration_s).
        #: The real arm stays in MIT mode for its whole life (see connect),
        #: so a move is a MIT ramp streamed every tick — the executor
        #: re-issues move_to and this re-computes the interpolated setpoint.
        self._motion: PreparedMotion | None = None
        self._motion_started_at: float | None = None
        self._generation = 0
        self._reference: dict[str, tuple[float, float, float]] = {}
        self._requested_target: dict[str, float] | None = None

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
            # Parse the selected model before enabling any hardware.
            self._dynamics_model()
            self._arm.connect()
            # The firmware latches its control mode at enable: MIT must be
            # set BEFORE enable_all, and runtime mode switches are ignored —
            # proved on the real arm, where a post-enable mode_pos_vel left
            # POS_VEL frames inert and the estop freeze soft. So the arm
            # lives in MIT: motion is a MIT ramp, hold is a MIT pin, float
            # is a MIT follow.
            for group in self._arm.groups.values():
                group.mode_mit()
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
            q, _, tau = self._arm.get_state()
            now = self._clock()

            if self._prev_q is not None and self._prev_t is not None and now > self._prev_t:
                dt = now - self._prev_t
                self._velocities = {
                    name: float((q[i] - self._prev_q[i]) / dt) for name, i in self._index.items()
                }
            self._prev_q, self._prev_t = np.array(q, copy=True), now

            torques = {}
            if tau is not None:
                torques = {name: float(tau[i]) for name, i in self._index.items()}

            return ArmState(
                positions={name: float(q[i]) for name, i in self._index.items()},
                velocities=dict(self._velocities),
                t=now,
                torques=torques,
            )

    def hold(self, q_target: Mapping[str, float]) -> None:
        """Pin the arm at ``q_target`` with MIT stiffness plus gravity feedforward.

        Also the emergency-stop path. Every tick under an engaged stop calls
        this with the frozen pose, which is what keeps the arm up. Clears any
        active motion profile: a freeze replaces the ramp, not the other way
        around.
        """
        with self._lock:
            self._floating = False
            self._motion = None
            self._requested_target = None
            self._generation += 1
            self._send_mit(self._to_array(q_target), kp=DEFAULT_HOLD_KP, kd=DEFAULT_HOLD_KD)

    def move_to(self, q_target: Mapping[str, float], duration_s: float) -> float:
        """One tick of a point-to-point move toward ``q_target``.

        The firmware latches its mode at enable and ignores runtime switches,
        so the arm never leaves MIT: a move is a ramp whose setpoint this
        method interpolates from the profile's start time, and the executor
        re-issues it every tick until arrival. The ramp is eased (smoothstep)
        so the arm accelerates and decelerates instead of jerking at the ends;
        its peak velocity is ``EASE_PEAK``× the linear average. The same call
        with the same target continues the profile; a new target starts a new
        one.
        """
        if duration_s <= 0:
            raise ValueError("duration_s must be positive")

        with self._lock:
            now = self._clock()
            requested = self._full_target(q_target)
            if self._motion is None or requested != (self._requested_target or {}):
                self.commit_move(self.prepare_move(q_target, duration_s))
            assert self._motion is not None and self._motion_started_at is not None
            values = [
                self._motion.profiles[name].eval(now - self._motion_started_at)
                for name in self._names
            ]
            self._send_mit(
                np.array([v[0] for v in values]),
                vel=np.array([v[1] for v in values]),
                kp=DEFAULT_HOLD_KP,
                kd=DEFAULT_HOLD_KD,
            )
            self._reference = {name: values[i][:3] for i, name in enumerate(self._names)}
            self._generation += 1
            return self._motion.duration

    def prepare_move(
        self,
        q_target: Mapping[str, float],
        requested_duration: float,
        *,
        limits: MotionLimits = DEFAULT_LIMITS,
    ) -> PreparedMotion:
        with self._lock:
            target = self._full_target(q_target)
            current, _, _ = self._arm.get_state()
            if self._motion is not None and self._motion_started_at is not None:
                # Bind the handoff to the last transmitted reference. A clock
                # advance alone is not a command sent to the motors.
                starts = dict(self._reference)
            else:
                starts = {name: (float(current[i]), 0.0, 0.0) for i, name in enumerate(self._names)}
            return prepare_profiles(
                starts,
                target,
                requested_duration,
                self._generation + 1,
                limits,
                joint_bounds=expanded_joint_bounds(),
            )

    def commit_move(self, prepared: PreparedMotion) -> float:
        with self._lock:
            if prepared.generation != self._generation + 1:
                raise RuntimeError("prepared motion is stale")
            self._generation = prepared.generation
            self._motion = prepared
            self._reference = {name: p.eval(0.0)[:3] for name, p in prepared.profiles.items()}
            self._motion_started_at = self._clock()
            self._requested_target = dict(prepared.target)
            self._floating = False
            return prepared.duration

    def relax(self) -> None:
        """Zero-gain MIT at the current position: the motors command no torque
        and the arm rests on its stops. The bus stays up — the next hold or
        move re-asserts torque immediately. Only legal at the zero pose; the
        caller gates that (zero torque anywhere else is a free-fall)."""
        with self._lock:
            self._floating = False
            self._motion = None
            self._requested_target = None
            self._generation += 1
            q, _, _ = self._arm.get_state()
            for group in self._arm.groups.values():
                idxs = [self._index[name] for name in group.joint_names]
                n = len(idxs)
                group.send_mit(
                    q[idxs],
                    vel=np.zeros(n),
                    kp=np.zeros(n),
                    kd=np.zeros(n),
                    tau=np.zeros(n),
                )

    def set_float(self, enabled: bool) -> None:
        """Enter or leave zero-force float.

        Entering only marks the mode — what actually frees the arm is the
        per-tick :meth:`follow` stream that the control loop runs while
        floating. Leaving re-asserts a hold at wherever the arm currently is,
        which is the "let go and it stays put" behaviour. Getting that wrong
        means the arm snaps back to a stale target the moment the operator
        releases it.
        """
        with self._lock:
            self._floating = enabled
            self._motion = None
            self._requested_target = None
            self._generation += 1
            if not enabled:
                q, _, _ = self._arm.get_state()
                self._send_mit(q, kp=DEFAULT_HOLD_KP, kd=DEFAULT_HOLD_KD)

    def follow(self) -> None:
        """One zero-force tick: target = where the arm is, gravity at live q.

        MIT mode executes the last command it received, so float is only real
        while this is streamed every tick. Silence on the bus leaves the
        motors running the stale lock setpoint, and the arm pulls back toward
        wherever it locked — which an operator reads as "it keeps lifting".
        """
        with self._lock:
            if not self._floating:
                return
            q, _, _ = self._arm.get_state()
            self._send_mit(q, kp=self._float_kp, kd=self._float_kd)
            self._generation += 1

    def set_gravity_correction(self, scale: Mapping[str, float], bias: Mapping[str, float]) -> None:
        """Apply the per-joint correction to the gravity feedforward. Called by
        the controller when tuning changes; the arm must not be floating (the
        feedforward jumps with the correction)."""
        with self._lock:
            self._gravity_scale = dict(scale)
            self._gravity_bias = dict(bias)

    def set_float_gains(self, kp: float, kd: float) -> None:
        """Retune the float gains live — see :class:`~backend.arm.base.ArmDriver`."""
        with self._lock:
            self._float_kp = kp
            self._float_kd = kd

    def reload_dynamics(self, payload: PayloadTuning | None = None) -> None:
        """Drop the cached dynamics model so the next torque computes against
        the new payload. The rebuild happens lazily in :meth:`_dynamics_model`."""
        with self._lock:
            self._payload = payload
            self._dyn_model = None
            self._dyn_data = None

    @property
    def is_floating(self) -> bool:
        with self._lock:
            return self._floating

    # ── internals ────────────────────────────────────────────────────────────

    def _full_target(self, joints: Mapping[str, float]) -> dict[str, float]:
        """Map a joint dict onto upstream's positional array.

        Unmentioned joints keep their current commanded value rather than
        defaulting to zero — a missing key must not mean "go to the rest pose".
        """
        unknown = set(joints) - set(self._index)
        if unknown:
            raise KeyError(f"unknown joints: {sorted(unknown)}")

        if self._motion is not None:
            target = dict(self._motion.target)
        else:
            current, _, _ = self._arm.get_state()
            target = {name: float(current[i]) for i, name in enumerate(self._names)}
        target.update({name: float(value) for name, value in joints.items()})
        return target

    def _to_array(self, joints: Mapping[str, float]) -> np.ndarray:
        target = self._full_target(joints)
        return np.array([target[name] for name in self._names], dtype=float)

    def _send_mit(self, q: np.ndarray, kp: float, kd: float, vel: np.ndarray | None = None) -> None:
        # Each group receives arrays sized to its own joints, in its own
        # order. Upstream's JointGroup indexes pos[i]/vel[i]/kp[i]/… by the
        # group's joint list: a full-arm array silently makes the gripper
        # read joint1's value, and send_pos_vel walks off the end of the arm
        # group's joint list (IndexError). The real arm pays for both; the
        # simulator never does, because SimArm has no groups.
        measured, _, _ = self._arm.get_state()
        tau = self._gravity_torque(np.asarray(measured, dtype=float))
        velocity = np.zeros(len(self._names)) if vel is None else vel
        for group in self._arm.groups.values():
            idxs = [self._index[name] for name in group.joint_names]
            n = len(idxs)
            group.send_mit(
                q[idxs],
                vel=velocity[idxs],
                kp=np.full(n, kp),
                kd=np.full(n, kd),
                tau=tau[idxs],
            )

    def _dynamics_model(self):
        """The RS dynamics model, loaded once.

        The URDF path is passed explicitly and always. Upstream's default
        resolves through ``config/rebotarm.yaml`` to the **B601-DM** arm and
        loads without raising, which would silently feed forward another
        robot's gravity — wrong torques, no error. The path goes through
        :func:`assets.effective_urdf_path` so a detached gripper's mass comes
        out of the gravity model along with its motor, and a camera payload
        goes in when the tuning profile says one is mounted.
        """
        if self._dyn_model is None:
            from reBotArm_control_py.dynamics.inverse_dynamics import create_data
            from reBotArm_control_py.dynamics.robot_model import load_dynamics_model

            self._dyn_model = load_dynamics_model(
                self.model_path or str(assets.effective_urdf_path(self._payload))
            )
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
        arm_q = np.array([q[self._index[name]] for name in assets.arm_joint_names()], dtype=float)
        g = compute_generalized_gravity(model, arm_q, self._dyn_data)

        tau = np.zeros(len(self._names), dtype=float)
        for position, name in enumerate(assets.arm_joint_names()):
            value = float(g[position])
            value = self._gravity_scale.get(name, 1.0) * value + self._gravity_bias.get(name, 0.0)
            tau[self._index[name]] = value
        return tau
