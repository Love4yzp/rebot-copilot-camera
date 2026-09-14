"""One explicitly selected runtime: hardware OR physical simulation."""

import asyncio
from contextlib import suppress
import json
import hashlib
import os
from pathlib import Path

from reBotArm_control_py.end_effector import EndEffectorSpec, build_end_effector_model
from reBotArm_control_py.viewer import ViewerSession

from . import assets, config
from .arm.session import ArmSession
from .tuning import TuningStore


class Runtime:
    def __init__(self, *, simulated: bool, tuning):
        self.simulated = simulated
        self.physics = None
        self._model_owner = None
        self.model_path = assets.urdf_path()
        self.payload_name = tuning.payload.profile.value
        spec_file = os.environ.get("REBOT_END_EFFECTOR_FILE")
        if spec_file:
            if not simulated and assets.has_gripper():
                raise ValueError(
                    "a custom replacement requires gripper: false in hardware YAML; do not remove the stock gripper mass while its motor is configured on the bus"
                )
            spec = EndEffectorSpec(**json.loads(Path(spec_file).read_text()))
            profile = "custom"
            self.payload_name = spec.name
        else:
            spec = None
            profile = tuning.payload.profile.value
            if profile == "camera":
                if simulated:
                    raise ValueError(
                        "camera mass/COM alone cannot simulate dynamics; provide REBOT_END_EFFECTOR_FILE with inertia and collision box"
                    )
                # Preserve legacy real-arm gravity calibration until a measured
                # complete model is supplied. Visual/collision geometry stays conservative.
                profile = "gripper"
        self._model_owner, self.model_path = build_end_effector_model(
            str(assets.urdf_path()), profile=profile, spec=spec
        )
        self.model_digest = hashlib.sha256(self.model_path.read_bytes()).hexdigest()
        self.inertia_source = (
            ("box-estimate" if spec.inertia_estimated else "provided") if spec else "stock-urdf"
        )
        if simulated:
            from reBotArm_control_py.physics import MujocoTransport

            self.physics = MujocoTransport(
                urdf_path=self.model_path,
                joint_names=assets.joint_names(),
                arm_joint_names=assets.arm_joint_names(),
            )
            self.arm = ArmSession(transport=self.physics, model_path=str(self.model_path))
        else:
            self.arm = ArmSession(model_path=str(self.model_path) if spec_file else None)
        self.arm.model_locked = True
        self.viewer = ViewerSession(
            urdf_path=str(self.model_path), joint_names=assets.arm_joint_names()
        )
        self._task = None
        self._subscription = None

    async def start(self, app):
        self.arm.hold(self.arm.read_state().positions)
        if self.physics is not None:
            self.physics.start()
        self._subscription = app.state.broadcaster.subscribe(
            asyncio.get_running_loop(), topics={"state"}
        )
        self._task = asyncio.create_task(self._publish())

    def connect(self):
        # Finish viewer startup before enabling the arm.
        self.viewer.start()
        try:
            self.arm.connect()
            self.arm.hold(self.arm.read_state().positions)
        except Exception:
            self.viewer.close()
            raise

    async def _publish(self):
        while True:
            message = await self._subscription.get()
            self.viewer.publish(message["data"]["positions"])

    async def close(self, app):
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        if self._subscription is not None:
            app.state.broadcaster.unsubscribe(self._subscription)
        await asyncio.to_thread(self.viewer.close)
        if self.physics is not None:
            self.physics.close()
        if self._model_owner is not None:
            self._model_owner.cleanup()


def runtime_tuning_store(simulated):
    # Simulation never loads or overwrites the operator's real calibration.
    path = config.DATA_DIR / "sim" / "tuning.yaml" if simulated else config.TUNING_FILE
    return TuningStore(path, hardware_constraints=not simulated)
