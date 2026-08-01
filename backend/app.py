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

from . import __version__, assets, config
from .api import control, estop, routines
from .arm import SimArm
from .core import Broadcaster, Controller
from .routines import RoutineStore
from .safety import SafetyLatch
from .shutter import SimShutter

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
    app.state.controller.start(rate_hz=SIM_LOOP_HZ)
    log.info("control loop started at %.0f Hz", SIM_LOOP_HZ)
    try:
        yield
    finally:
        # Stopping the loop stops commanding, but never disables the motors.
        app.state.controller.stop()
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

# Simulated hardware by default. The real ArmSession (plan commit #9) swaps in
# here once CAN transport is confirmed; everything above the arm is unaware.
app.state.controller = Controller(
    arm=SimArm(assets.joint_names(), clock=time.monotonic),
    shutter=SimShutter(),
    latch=app.state.latch,
    broadcaster=app.state.broadcaster,
)

app.include_router(estop.router)
app.include_router(routines.router)
app.include_router(control.router)


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
        "arm": {
            "urdf": str(assets.urdf_path()),
            "end_effector_frame": assets.end_effector_frame(),
            "joints": assets.joint_names(),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="rebot-copilot-camera")
    parser.add_argument("--sim", action="store_true", help="force SimArm/SimShutter")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18790)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    # Fail at startup rather than mid-motion if the DM arm's assets leaked in.
    assets.assert_rs_model()
    log.info("URDF: %s (frame=%s)", assets.urdf_path(), assets.end_effector_frame())
    if args.sim:
        log.info("sim mode requested")

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
