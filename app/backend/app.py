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
import copy
import logging
import os
import socket
import sys
import time
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import __version__, assets, config
from .actions import ActionRegistry, ShutterProvider, ThreadedRunner
from .agent import AgentLease
from .api import agent, control, estop, logs, plugins, poses, sequences, templates
from .api import config as config_api
from .arm import SimArm, create_arm
from .core import Broadcaster, Controller
from .safety import ClientWatchdog, ContactObserver, SafetyLatch, Watchdog
from .safety.kinematics import arm_model
from .sequences import (
    PoseStore,
    SequenceStore,
    TemplateStore,
)
from .sequences.seed_demo import seed_demo_if_empty
from .shutter import SimShutter, create_shutter
from .tuning import TuningStore

log = logging.getLogger(__name__)

STARTED_AT = time.time()

#: Loop rate for the simulated arm. The real arm defers to upstream's
#: start_control_loop at the yaml's 500 Hz, pending the R2x measurement (B3).
SIM_LOOP_HZ = 100.0

#: Overall budget for the shutdown park. The worst-case approach is the full
#: joint travel (~3.1 rad) at FIRST_APPROACH_MAX_SPEED (0.25 rad/s) ≈ 12.4 s,
#: and the executor's own stuck-abort allows three times that ≈ 37 s. This
#: backstop covers a stuck control loop; systemd gets 60 s (TimeoutStopSec).
PARK_TIMEOUT_S = 45.0


def _ensure_port_free(host: str, port: int) -> None:
    """Refuse to start when the address is already serving.

    A second instance that discovers the conflict only at uvicorn's bind has
    already connected the arm over CAN — and its shutdown park would then
    drive a machine another process is responsible for. Fail here, before
    anything touches the bus. Plain bind, no SO_REUSEADDR: the point is to
    lose against an existing listener.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((host, port))
    except OSError:
        raise SystemExit(
            f"{host}:{port} is already serving — is another instance running? "
            "Exiting before connecting the arm."
        )
    finally:
        probe.close()


def _park_arm(controller: Controller) -> None:
    """Move the arm slowly to the zero pose before the process exits.

    Runs while the control loop is still ticking — stopping the loop is the
    last thing shutdown does, never the first. Returns immediately when the
    stop latch is engaged: the arm then holds its frozen pose through exit,
    which is the safe end state for a stop that means "something is wrong".
    """
    if not controller.is_running:
        # Startup never brought the loop up (e.g. the bind failed), so nothing
        # would drive the park's executor — waiting would just sit out the
        # whole timeout, and an instance that never served should not command
        # the arm on its way out.
        return
    executor = controller.park_home()
    if executor is None:
        return
    log.info("parking the arm at the zero pose before exit (up to %.0f s)", PARK_TIMEOUT_S)
    deadline = time.monotonic() + PARK_TIMEOUT_S
    while not executor.is_finished and time.monotonic() < deadline:
        time.sleep(0.05)
    progress = executor.progress()
    if progress.phase.value == "done":
        log.info("arm parked at zero")
    elif executor.is_finished:
        log.warning("park did not reach zero: %s", progress.error)
    else:
        log.warning("park timed out after %.0f s — exiting anyway", PARK_TIMEOUT_S)


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
        # Park first, while the loop still ticks: the move is driven by the
        # loop, so stopping the loop is the last thing shutdown does.
        _park_arm(app.state.controller)
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
app.state.pose_store = PoseStore(config.POSES_DIR)
app.state.sequence_store = SequenceStore(config.SEQUENCES_DIR)
app.state.template_store = TemplateStore(config.TEMPLATES_DIR)
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

# Operator-calibrated tuning: the file is the saved copy, the controller's
# live config is the applied one, and only an explicit save moves the former.
app.state.tuning_store = TuningStore(config.TUNING_FILE)

app.state.controller = Controller(
    arm=SimArm(assets.joint_names(), clock=time.monotonic, self_driven=True),
    shutter=_shutter,
    latch=app.state.latch,
    broadcaster=app.state.broadcaster,
    watchdog=app.state.watchdog,
    expected_period_s=1.0 / SIM_LOOP_HZ,
    actions=_runner,
    tuning=app.state.tuning_store.load(),
)

app.include_router(estop.router)
app.include_router(poses.router)
app.include_router(sequences.router)
app.include_router(templates.router)
app.include_router(control.router)
app.include_router(agent.router)
app.include_router(logs.router)
app.include_router(plugins.router)
app.include_router(config_api.router)


@app.get("/api/health")
def health() -> dict:
    """Liveness plus enough identity to tell two deployments apart."""
    latch = app.state.latch.snapshot()
    return {
        "status": "ok",
        "version": __version__,
        "uptime_s": round(time.time() - STARTED_AT, 3),
        "mode": "sim" if app.state.simulated else "prod",
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


class ParkOnExitServer(uvicorn.Server):
    """Uvicorn with signal handling that cannot interrupt the shutdown park.

    Stock uvicorn treats a second SIGINT as "force quit": it sets
    ``force_exit``, and ``Server.shutdown()`` then *skips* the lifespan
    shutdown hook — which is where the arm parks at zero. An operator who
    double-taps Ctrl+C out of habit would kill the park mid-move and leave
    the arm hanging wherever it happened to be. So repeated signals are
    logged and ignored instead; the first one still starts a graceful exit.

    The signal is deliberately not appended to ``_captured_signals``: that
    list is re-raised after the loop exits (SIGINT would surface as a
    KeyboardInterrupt out of ``run()``), and a clean park is a clean exit.
    """

    def handle_exit(self, sig: int, frame) -> None:
        if self.should_exit:
            log.info("exit already in progress — parking the arm first, ignoring signal %d", sig)
            return
        log.info("exit requested (signal %d) — parking the arm at zero first", sig)
        self.should_exit = True


LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class _LevelColorFormatter(logging.Formatter):
    """ANSI-color the level name so WARNING/ERROR stand out in a terminal.

    Same mapping uvicorn's own formatter uses (DEBUG blue, INFO green,
    WARNING yellow, ERROR red, CRITICAL bold red), so backend lines and
    server lines read the same. Installed only when stderr is a TTY and
    NO_COLOR is unset — journalctl and redirected logs get plain text,
    where escape codes would be noise.
    """

    _RESET = "\x1b[0m"
    _COLORS = {
        logging.DEBUG: "\x1b[34m",
        logging.INFO: "\x1b[32m",
        logging.WARNING: "\x1b[33m",
        logging.ERROR: "\x1b[31m",
        logging.CRITICAL: "\x1b[1;31m",
    }

    def format(self, record: logging.LogRecord) -> str:
        color = self._COLORS.get(record.levelno)
        if not color:
            return super().format(record)
        # Copy instead of mutating: the same record goes to every handler,
        # and escape codes must not leak into a plain-text one.
        record = copy.copy(record)
        record.levelname = f"{color}{record.levelname}{self._RESET}"
        return super().format(record)


def _configure_logging() -> None:
    use_color = sys.stderr.isatty() and "NO_COLOR" not in os.environ
    formatter: logging.Formatter = (
        _LevelColorFormatter(LOG_FORMAT) if use_color else logging.Formatter(LOG_FORMAT)
    )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=[handler])


def main() -> None:
    parser = argparse.ArgumentParser(prog="rebot-copilot-camera")
    parser.add_argument("--sim", action="store_true", help="force SimArm/SimShutter")
    parser.add_argument("--host", default=config.HOST)
    parser.add_argument("--port", type=int, default=config.PORT)
    args = parser.parse_args()

    _configure_logging()

    # Before anything touches CAN: a port conflict means another instance is
    # already responsible for the arm.
    _ensure_port_free(args.host, args.port)

    # Fail at startup rather than mid-motion if the DM arm's assets leaked in.
    assets.assert_rs_model()
    log.info("URDF: %s (frame=%s)", assets.urdf_path(), assets.end_effector_frame())

    arm, simulated = create_arm(force_sim=args.sim)
    app.state.simulated = simulated
    app.state.controller.bind_arm(arm)
    app.state.controller.client_watchdog = ClientWatchdog(clock=time.monotonic, timeout_s=2.0)
    app.state.controller.contact = ContactObserver(clock=time.monotonic, enabled=False)

    # The shutter is chosen the same way, but never falls back: a simulated
    # shutter reports every frame as fired, and an operator who walks a whole
    # set on that finds out when they review it. See backend/shutter/factory.
    shutter, shutter_simulated = create_shutter(force_sim=args.sim)

    # When the service is in sim mode, make the turntable plugin use its
    # in-process simulator too. The plugin decides between hardware and sim
    # by reading TURNTABLE_PORT at import time, so we set it before discovery.
    if args.sim:
        os.environ.setdefault("TURNTABLE_PORT", "sim")
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
    app.state.plugins.discover_dir(config.PLUGINS_DIR)

    # Say at startup which accessories answer, rather than at the first anchor.
    for status in app.state.plugins.probe_all():
        log.info(
            "action %r: %s", status.id, "ok" if status.available else f"DOWN — {status.reason}"
        )

    # First boot gets the reference demo: empty stores are planted with the
    # four-station shoot once, so the full stack demos like the mock. Skipped
    # when anything exists, when this deployment was seeded before, or when
    # REBOT_SEED_DEMO=0.
    seed_demo_if_empty(
        app.state.pose_store,
        app.state.sequence_store,
        app.state.template_store,
        enabled=os.environ.get("REBOT_SEED_DEMO", "1") != "0",
    )

    ParkOnExitServer(uvicorn.Config(app, host=args.host, port=args.port)).run()


if __name__ == "__main__":
    main()
