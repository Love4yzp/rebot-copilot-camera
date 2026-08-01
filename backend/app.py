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

from fastapi import FastAPI

from . import __version__, assets
from .api import estop
from .safety import SafetyLatch

log = logging.getLogger(__name__)

STARTED_AT = time.time()

app = FastAPI(
    title="rebot-copilot-camera",
    version=__version__,
    description="Automated multi-view photography with a reBot-RS arm.",
)

#: One latch for the whole process. The control loop reads it every tick and
#: the API gates on it, so it must be a single shared instance.
app.state.latch = SafetyLatch()

app.include_router(estop.router)


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
