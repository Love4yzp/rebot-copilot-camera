"""MuJoCo plant and an injected MIT transport for motion validation.

The plant receives the exact commands emitted by :class:`ArmSession`. It
holds the last command between sends and never interpolates an application
trajectory itself. The two prismatic finger joints remain fixed; there is no
calibrated mapping from the single gripper motor to finger travel.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .. import assets

try:  # Optional dependency: ordinary app imports must work without MuJoCo.
    import mujoco
except ImportError as exc:  # pragma: no cover - exercised by CLI error path
    raise ImportError(
        "MuJoCo validation is optional; install it with `uv sync --extra physics`"
    ) from exc


JOINTS = tuple(assets.arm_joint_names())
ALL_JOINTS = tuple(assets.joint_names())


@dataclass(frozen=True)
class MitCommand:
    t: float
    q_des: dict[str, float]
    v_des: dict[str, float]
    kp: dict[str, float]
    kd: dict[str, float]
    tau_ff: dict[str, float]


class _Group:
    def __init__(self, transport: "RecordingTransport", names: list[str]) -> None:
        self._transport = transport
        self.joint_names = names

    def mode_mit(self, kp=None, kd=None) -> None:
        return None

    def send_mit(self, pos, vel=None, kp=None, kd=None, tau=None) -> None:
        self._transport.record(self.joint_names, pos, vel, kp, kd, tau)


class RecordingTransport:
    """RebotArm-shaped transport with deterministic state injection."""

    def __init__(self, q0: dict[str, float] | None = None, clock=None) -> None:
        self._clock = clock or time.monotonic
        self._q = np.array([float((q0 or {}).get(n, 0.0)) for n in ALL_JOINTS])
        self._v = np.zeros(len(ALL_JOINTS))
        self._tau = np.zeros(len(ALL_JOINTS))
        self.commands: list[MitCommand] = []
        self._index = {n: i for i, n in enumerate(ALL_JOINTS)}
        self.groups = {
            "arm": _Group(self, list(JOINTS)),
            "gripper": _Group(self, [n for n in ALL_JOINTS if n not in JOINTS]),
        }

    @property
    def joint_names(self) -> list[str]:
        return list(ALL_JOINTS)

    def get_state(self):
        return self._q.copy(), self._v.copy(), self._tau.copy()

    def connect(self) -> None: return None
    def disconnect(self) -> None: return None
    def enable_all(self) -> None: return None

    def record(self, names, pos, vel, kp, kd, tau) -> None:
        def values(value, default=0.0):
            if value is None:
                return [default] * len(names)
            return np.asarray(value, dtype=float).tolist()
        vv, pp, dd, tt = values(vel), values(kp), values(kd), values(tau)
        qq = np.asarray(pos, dtype=float).tolist()
        self.commands.append(MitCommand(
            float(self._clock()),
            dict(zip(names, map(float, qq))),
            dict(zip(names, map(float, vv))),
            dict(zip(names, map(float, pp))),
            dict(zip(names, map(float, dd))),
            dict(zip(names, map(float, tt))),
        ))

    def set_feedback(self, q: np.ndarray, v: np.ndarray, tau: np.ndarray | None = None) -> None:
        self._q[:] = q
        self._v[:] = v
        if tau is not None:
            self._tau[:] = tau


@dataclass
class ModelBundle:
    model: Any
    data: Any
    root: Path
    urdf: Path
    body_ids: dict[str, int]
    joint_ids: dict[str, int]
    actuator_ids: dict[str, int]
    qpos_addr: dict[str, int]
    dof_addr: dict[str, int]
    effort_limits: dict[str, float]

    def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


def load_model() -> ModelBundle:
    """Load the shipped RS URDF with mesh paths fixed in a temporary tree."""
    source = assets.urdf_path()
    root = Path(tempfile.mkdtemp(prefix="rebot-mujoco-"))
    (root / "urdf").mkdir()
    (root / "meshes").mkdir()
    root_xml = ET.fromstring(source.read_text(encoding="utf-8"))
    effort = {
        joint.get("name"): float(joint.find("limit").get("effort"))
        for joint in root_xml.findall("joint")
        if joint.get("name") in JOINTS and joint.find("limit") is not None
    }
    for joint in root_xml.findall("joint"):
        if joint.get("name") in {"joint_left", "joint_right"}:
            joint.set("type", "fixed")
            for child in list(joint):
                if child.tag in {"axis", "limit"}:
                    joint.remove(child)
    mujoco_ext = ET.SubElement(root_xml, "mujoco")
    ET.SubElement(mujoco_ext, "compiler", {"fusestatic": "false"})
    xml = ET.tostring(root_xml, encoding="unicode").replace(
        'filename="meshes/', 'filename="../meshes/'
    )
    urdf = root / "urdf" / source.name
    urdf.write_text(xml, encoding="utf-8")
    for mesh in source.parent.parent.joinpath("meshes").iterdir():
        (root / "meshes" / mesh.name).symlink_to(mesh)
        # MuJoCo's URDF importer resolves the filename against the URDF dir.
        (root / "urdf" / mesh.name).symlink_to(mesh)
        (root / mesh.name).symlink_to(mesh)
    spec = mujoco.MjSpec.from_file(str(urdf))
    for name in JOINTS:
        actuator = spec.add_actuator()
        actuator.name = f"mit_{name}"
        actuator.target = name
        actuator.trntype = mujoco.mjtTrn.mjTRN_JOINT
        actuator.gaintype = mujoco.mjtGain.mjGAIN_FIXED
        actuator.biastype = mujoco.mjtBias.mjBIAS_AFFINE
        actuator.gainprm[0] = 1.0
        actuator.forcelimited = True
        actuator.forcerange = [-effort[name], effort[name]]
    model = spec.compile()
    # MIT velocity feedback is stiff relative to the small wrist inertias;
    # implicitfast keeps the validation integrator stable without changing
    # deployed gains or the physical model.
    model.opt.integrator = mujoco.mjtIntegrator.mjINT_IMPLICITFAST
    data = mujoco.MjData(model)
    model.actuator_gainprm[:, :] = 0.0
    model.actuator_biasprm[:, :] = 0.0
    bodies = {model.body(i).name: i for i in range(model.nbody)}
    joints = {model.joint(i).name: i for i in range(model.njnt)}
    actuators = {model.actuator(i).name: i for i in range(model.nu)}
    required = {"base_link", "gripper_end"}
    if not required <= bodies.keys():
        raise RuntimeError(f"fixed bodies were fused; missing {sorted(required - bodies.keys())}")
    if tuple(joints) != JOINTS:
        raise RuntimeError(f"unexpected physical joints: {tuple(joints)}")
    if len(actuators) != len(JOINTS):
        raise RuntimeError(f"expected six MIT actuators, got {len(actuators)}")
    qpos_addr = {name: int(model.jnt_qposadr[joints[name]]) for name in JOINTS}
    dof_addr = {name: int(model.jnt_dofadr[joints[name]]) for name in JOINTS}
    return ModelBundle(model, data, root, urdf, bodies, joints, actuators, qpos_addr, dof_addr, effort)


@dataclass
class PhysicsPlant:
    bundle: ModelBundle
    transport: RecordingTransport
    t: float = 0.0
    _initialized: bool = False
    samples: list[dict[str, Any]] = field(default_factory=list)

    def step(self, dt: float) -> None:
        """Advance one physics step using the last received MIT command."""
        if dt <= 0:
            raise ValueError("dt must be positive")
        model, data = self.bundle.model, self.bundle.data
        model.opt.timestep = float(dt)
        cmd = next(
            (candidate for candidate in reversed(self.transport.commands) if "joint2" in candidate.q_des),
            None,
        )
        if not self._initialized:
            for i, name in enumerate(JOINTS):
                data.qpos[self.bundle.qpos_addr[name]] = self.transport._q[i]
                data.qvel[self.bundle.dof_addr[name]] = self.transport._v[i]
            mujoco.mj_forward(model, data)
            self._initialized = True
        if cmd is not None:
            q = np.array([cmd.q_des.get(n, data.qpos[self.bundle.qpos_addr[n]]) for n in JOINTS])
            v = np.array([cmd.v_des.get(n, 0.0) for n in JOINTS])
            kp = np.array([cmd.kp.get(n, 0.0) for n in JOINTS])
            kd = np.array([cmd.kd.get(n, 0.0) for n in JOINTS])
            ff = np.array([cmd.tau_ff.get(n, 0.0) for n in JOINTS])
            for i, name in enumerate(JOINTS):
                aid = self.bundle.actuator_ids[f"mit_{name}"]
                data.ctrl[aid] = kp[i] * q[i] + kd[i] * v[i] + ff[i]
                model.actuator_gainprm[aid, 0] = 1.0
                model.actuator_biasprm[aid, 0] = 0.0
                model.actuator_biasprm[aid, 1] = -kp[i]
                model.actuator_biasprm[aid, 2] = -kd[i]
        else:
            data.ctrl[:] = 0.0
            model.actuator_gainprm[:, :] = 0.0
            model.actuator_biasprm[:, :] = 0.0
        mujoco.mj_step(model, data, nstep=1)
        self.t = float(data.time)
        q = self.transport._q.copy()
        vout = self.transport._v.copy()
        for i, name in enumerate(JOINTS):
            q[i] = data.qpos[self.bundle.qpos_addr[name]]
            vout[i] = data.qvel[self.bundle.dof_addr[name]]
        tau = np.zeros(len(self.transport.joint_names), dtype=float)
        for i, name in enumerate(JOINTS):
            tau[i] = data.qfrc_actuator[self.bundle.dof_addr[name]]
        self.transport.set_feedback(q, vout, tau)
        self.samples.append({
            "t": self.t,
            "q": q[:6].tolist(),
            "v": vout[:6].tolist(),
            "tau": tau[:6].tolist(),
            "ncon": int(data.ncon),
            "saturated": [
                abs(float(tau[i])) >= self.bundle.effort_limits[name] - 1e-6
                for i, name in enumerate(JOINTS)
            ],
        })


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
