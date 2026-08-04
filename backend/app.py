"""FastAPI application entry point.

Run with::

    uv run -m backend.app --sim     # no hardware
    uv run -m backend.app           # real arm over CAN

Route mounting order matters: the static-file mount must come last, after every
router and websocket, or it swallows their paths. (Learned the hard way in the
previous generation of this service.)
"""

from __future__ import annotations

import argparse
import logging
import time

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import __version__, assets, config
from .agent import AgentLease
from .actions import ActionRegistry, ShutterProvider, ThreadedRunner
from .api import agent, control, estop, logs, plugins, routines
from .arm import SimArm, create_arm
from .core import Broadcaster, Controller
from .routines import RoutineStore
from .safety import SafetyLatch, Watchdog
from .safety.kinematics import arm_model
from .shutter import SimShutter, create_shutter

log = logging.getLogger(__name__)

STARTED_AT = time.time()

#: Loop rate for the simulated arm. The real arm defers to upstream's
#: start_control_loop at the yaml's 500 Hz, pending the R2x measurement (B3).
SIM_LOOP_HZ = 100.0


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run the control loop for the lifetime of the server.

    Started here rather than at import time so that importing the app -- which
    every test does -- does not spin a thread that commands an arm.
    """
    # Parsing 30 STL meshes takes a couple of seconds. Do it now rather than
    # inside the first request that needs a collision check.
    model = arm_model()
    log.info("kinematics ready: %d collision pairs", len(model.geom.collisionPairs))

    app.state.controller.start(rate_hz=SIM_LOOP_HZ)
    log.info("control loop started at %.0f Hz", SIM_LOOP_HZ)
    try:
        yield
    finally:
        # Stopping the loop stops commanding, but never disables the motors.
        app.state.controller.stop()
        # Then the action workers. After the loop, because a worker that is
        # mid-exchange with a camera should not be interrupted by a shutdown
        # the loop has not finished acknowledging yet.
        app.state.controller.actions.close()
        log.info("control loop stopped")


app = FastAPI(
    title="rebot-copilot-camera",
    version=__version__,
    description="Automated multi-view photography with a reBot-RS arm.",
    lifespan=lifespan,
)

#: One latch for the whole process. The control loop reads it every tick and
#: the API gates on it, so it must be a single shared instance.
app.state.latch = SafetyLatch()
app.state.routine_store = RoutineStore(config.ROUTINES_DIR)
app.state.broadcaster = Broadcaster()
app.state.agent_lease = AgentLease()

# The arm is chosen at import time so tests get a simulator without touching
# CAN. main() re-chooses it, so the running service can use real hardware.
app.state.watchdog = Watchdog(app.state.latch, clock=time.monotonic)
app.state.simulated = True
app.state.shutter_simulated = True

# One runner for the process, and a registry over it. The registry is only the
# discovery and health layer -- the runner stays the single register of which
# providers exist, so the two cannot disagree about what is installed.
_shutter = SimShutter()
_runner = ThreadedRunner()
app.state.plugins = ActionRegistry(_runner)
app.state.plugins.register(ShutterProvider(_shutter))

app.state.controller = Controller(
    arm=SimArm(assets.joint_names(), clock=time.monotonic, self_driven=True),
    shutter=_shutter,
    latch=app.state.latch,
    broadcaster=app.state.broadcaster,
    watchdog=app.state.watchdog,
    expected_period_s=1.0 / SIM_LOOP_HZ,
    actions=_runner,
)

app.include_router(estop.router)
app.include_router(routines.router)
app.include_router(control.router)
app.include_router(agent.router)
app.include_router(logs.router)
app.include_router(plugins.router)


@app.get("/api/health")
def health() -> dict:
    """Liveness plus enough identity to tell two deployments apart."""
    latch = app.state.latch.snapshot()
    return {
        "status": "ok",
        "version": __version__,
        "uptime_s": round(time.time() - STARTED_AT, 3),
        "estop": {
            "latched": latch.latched,
            "reason": latch.reason,
            "source": latch.source.value if latch.source else None,
        },
        "shutter": {"simulated": app.state.shutter_simulated},
        "arm": {
            "simulated": app.state.simulated,
            "urdf": str(assets.urdf_path()),
            "end_effector_frame": assets.end_effector_frame(),
            "joints": assets.joint_names(),
        },
    }


# ── static assets ────────────────────────────────────────────────────────────
#
# Mounted last, deliberately. `app.mount("/", ...)` matches everything, so a
# mount registered before the routers swallows every API path and websocket.
# The previous generation of this service lost an afternoon to that; keep these
# at the bottom of the file.

_URDF_DIR = assets.VENDOR_ROOT / "urdf"
if _URDF_DIR.is_dir():
    # Served from the submodule rather than copied: 63 MB of meshes, and a copy
    # would go stale the moment the submodule is bumped.
    app.mount("/assets/urdf", StaticFiles(directory=_URDF_DIR), name="urdf")

if assets.STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=assets.STATIC_DIR, html=True), name="ui")
else:
    log.info("no built frontend at %s — run `npm run build` in frontend/", assets.STATIC_DIR)


def main() -> None:
    parser = argparse.ArgumentParser(prog="rebot-copilot-camera")
    parser.add_argument("--sim", action="store_true", help="force SimArm/SimShutter")
    parser.add_argument("--host", default=config.HOST)
    parser.add_argument("--port", type=int, default=config.PORT)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    # Fail at startup rather than mid-motion if the DM arm's assets leaked in.
    assets.assert_rs_model()
    log.info("URDF: %s (frame=%s)", assets.urdf_path(), assets.end_effector_frame())

    arm, simulated = create_arm(force_sim=args.sim)
    app.state.simulated = simulated
    app.state.controller.arm = arm

    # The shutter is chosen the same way, but never falls back: a simulated
    # shutter reports every frame as fired, and an operator who walks a whole
    # set on that finds out when they review it. See backend/shutter/factory.
    shutter, shutter_simulated = create_shutter(force_sim=args.sim)
    app.state.shutter_simulated = shutter_simulated
    app.state.controller.set_shutter(shutter)
    # replace=True because the built-in registered at import time is being swapped
    # for one over the chosen driver. Discovery cannot ask for this: an installed
    # plugin claiming an id that is taken is refused and listed with the reason,
    # rather than quietly becoming the camera.
    app.state.plugins.register(ShutterProvider(shutter), replace=True)

    # Third-party providers load here rather than at import time: importing the
    # app is something every test does, and that must not run other people's
    # code. A plugin that fails to load is logged and listed as unavailable --
    # a missing accessory is not a missing machine.
    app.state.plugins.discover()

    # Say at startup which accessories answer, rather than at the first anchor.
    for status in app.state.plugins.probe_all():
        log.info(
            "action %r: %s", status.id, "ok" if status.available else f"DOWN — {status.reason}"
        )

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
