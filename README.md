# Teach & Repeat · 示教回放

**English** | [中文](README.zh-CN.md)

Record named poses, assemble a sequence, then execute it with a reBot-RS arm. The application owns the workflow and safety policy; [reBotArm_control_py](https://github.com/Seeed-Projects/reBotArm_control_py) owns robot algorithms and transport.

The workbench has a pose library, one read-only MeshCat feedback viewport and a station editor. MuJoCo is the default simulation backend, not a browser animation. Plugins, shutter control and Agent runtime are removed; future integration contracts remain [design-only](docs/PLUGINS.md).

Read [AGENTS](AGENTS.md) before modifying code. Hardware restrictions and calibration evidence remain in [HARDWARE_NOTES](docs/HARDWARE_NOTES.md).

## Install and start

Requirements: uv, Python 3.11, Node 22+, Git. CAN hardware is needed only for prod.

```bash
git clone --recursive https://github.com/Love4yzp/rebot-copilot-camera.git
cd rebot-copilot-camera
./dev.sh sim
```

Open http://127.0.0.1:18790. This starts the full backend, installs the physics extra and builds the frontend. Only a person should start the backend through dev.sh; agents verify with pytest.

Already cloned without the submodule: `git submodule update --init`. The validated SDK baseline plus the checked-in extension patch are prepared automatically by dev.sh. For dependency-only setup:

```bash
cd app
python prepare_sdk.py
uv sync --frozen --extra physics
```

| Command | Meaning |
|---|---|
| `./dev.sh sim` | Full stack + torque-driven MuJoCo; no CAN |
| `./dev.sh prod` | Real arm; connection failure refuses startup, never falls back |
| `./dev.sh build` | The sole frontend build entry; no backend start |
| `./dev.sh status` | Confirm mode and arm.backend |
| `./dev.sh sim --local` | Bind the application to localhost |
| `./dev.sh sim --no-build` | Reuse an existing frontend build |

`ui` / `mock` and `dev:mock` are removed. For HMR use `cd app/frontend && npm run dev` with a human-started backend already running; Vite proxies API, control WS and /viewer. It does not implement a second backend. Port prechecks remain mandatory.

## Use the workbench

1. Click「+ 录位姿」. In prod the arm starts holding; a push releases float, then letting go locks it. **Only teach near zero until the real-arm calibration restrictions are resolved.**
2. In sim, open the monitor's「详细数据」and use the short「− 推动 / ＋ 推动」inputs while teaching. Each pulse lasts 0.15 s; server limits are 0.2 s and 20% of joint effort. This is a simulated torque input, not a CAN command.
3. Name the pose and click「保存」. Selecting a card only selects it. Click「移动到此位姿」to move there; number keys and clicks on the 3D arm never command motion.
4. Create a sequence and use「＋追加」to add stations. Edit hold/transition duration and waits. Different adjacent poses automatically receive a transition.
5. Use「执行仿真」in sim, or「执行（臂会动）」in prod. A distant first station requires「去起点」first. A wait holds the arm until「继续」. Editing is locked during execution.
6. Templates preserve structure and pose slots, not joint angles. Instantiation produces an independent sequence.

The viewport displays backend feedback only. Orbit, zoom, reset or hide it without changing the plant. A stale/disconnected viewport is labelled; it never proves where the arm is. New motion, teaching, estop or disconnection invalidates “arrived”; only fresh done feedback can restore it.

Grey is the base UI. Amber means motion/compliance, green means confirmed arrival/hold, red means estop. White exposure indication is unused. Selection and decoration do not use these status colours.

## Simulation and replaceable end effectors

Simulation is useful for checking model response, tracking error, torque saturation and coarse collisions. It does **not** calibrate a real arm, friction, gearbox backlash, motor firmware or a new gripper. There is no grasp simulation or invented gripper-motor-to-finger mapping.

The SDK plant consumes the same ArmSession MIT position/velocity/kp/kd/feedforward commands as prod, clips torque to URDF effort and integrates at 1 ms. The controller runs at 100 Hz. Getters never advance time. Physics continues independently of the viewer; losing the control client still invokes the normal SafeLock policy.

Set `REBOT_END_EFFECTOR_FILE` to a JSON file to replace the stock fixed end effector:

```json
{
  "name": "example-tool",
  "mass": 0.2,
  "com": [0, 0, 0.05],
  "box": [0.04, 0.04, 0.10],
  "frame": "gripper_end"
}
```

These are illustrative values, **not calibrated hardware settings**. Units: kg, m, kg·m². Optional `inertia` is [xx, yy, zz, xy, xz, yz] at the COM in axes parallel to the attachment frame. Box geometry is centred at COM. If inertia is omitted, a uniform-box estimate is reported as `box-estimate`; provided values are checked for physical validity. In prod, verify physical removal and set gripper: false before replacing the stock end effector; its mass cannot disappear while its motor is configured on the bus. Restart to change the end effector: gravity, physics, collision and visualization must use the same selected model.

Without a custom file, sim uses its own bare/gripper profile. Legacy camera mass/COM tuning alone is insufficient for a dynamic simulation; supply the complete description. Real calibration files and stored real poses are not modified. Runtime payload switching is refused; prepare the new configuration while stopped and restart.

## Emergency stop and shutdown

The top-bar「急停」or Esc freezes the pose with **continued MIT torque and gravity compensation**. It must never call upstream's motor-disable stop. Motion endpoints refuse requests while latched; clearing stays in hold and never resumes automatically. Esc also works when the viewer has focus.

Ctrl+C / SIGTERM first parks slowly at zero while the control loop keeps running, then exits. Repeated signals do not bypass the park. If the latch is engaged, no park motion is started; the frozen pose is held. Keep systemd's stop timeout at 60 s. See hardware notes for the limits of shutdown holding and near-zero teaching.

## Configuration and deployment

| Setting | Default / scope |
|---|---|
| `REBOT_HOST` | 0.0.0.0; use `--local` or 127.0.0.1 on untrusted networks |
| `REBOT_PORT` | 18790 |
| `REBOT_DATA_DIR` | app/data; real libraries at poses/sequences/templates, sim under sim/ |
| `REBOT_TUNING_FILE` | app/config/tuning.yaml, **prod only** |
| `REBOT_END_EFFECTOR_FILE` | Optional fixed end-effector JSON; loaded at startup |
| Sim tuning | app/data/sim/tuning.yaml; independent of real calibration |

Hot float gains and thresholds remain available through「调参」. All tuning writes are refused during execution; gravity correction is also refused while floating. Explicit save persists to the selected instance's tuning file.

This service has **no authentication**. Anyone reaching the application port can command the arm. The internal MeshCat HTTP/ZMQ service binds only to loopback, and its same-origin proxy is read-only; this is not authentication for the application's motion API.

For a deliberate device deployment:

```bash
export REBOT_HOST_SSH=recomputer@<device-ip>
./device.sh setup
./device.sh push
./device.sh enable
./device.sh status
./device.sh open
```

setup installs CAN/application units and permissions; it no longer installs shutter udev rules. push calls dev.sh build, preserves remote data and real tuning, and restarts the service. Prepare and verify real calibration explicitly on first installation; push does not copy the developer tuning.yaml. The shipped unit binds localhost; use the SSH tunnel or an authenticated deployment proxy. Do not expose an unauthenticated motion API publicly.

## API and verification

`/docs` and `/openapi.json` describe current routes: poses, sequences, templates, teach/rest, stop/resume, estop, tuning, health/logs, `/ws`, and `/api/events`. `GET /api/health` identifies arm.backend as hardware or mujoco. `GET /api/sim/state` reports model-derived diagnostics; `POST /api/sim/perturb` is sim-only, unlatched-teach-only.

`/api/plugins/*`, `/api/shutter/*` and `/api/agent/*` are not registered. Sequence schema is v3, with wait-only markers. Old v2 plugin sequences are not migrated or erased; user pose and calibration files remain intact. Removed source/examples remain recoverable from Git.

```bash
cd app
uv run --frozen --extra physics pytest
uv run --extra physics ruff check backend tests
uv run --extra physics python -m backend.export_contract --check
uv run --frozen --extra physics python -m backend.validation --output data/validation/current.json --csv data/validation/current.csv --check
cd ..
./dev.sh build
```

REST cases compare to checked-in goldens; normalization runs in both Python and TypeScript. Frontend types derive from OpenAPI and CI checks drift. To regenerate, `python -m backend.export_contract` prints TypeScript to stdout without starting a service. Review changes before updating the generated file.

## Troubleshooting

| Symptom | Check |
|---|---|
| Wrong mode / motion refused | dev.sh status; prod never falls back to sim; read 400/409 reasons |
| SDK import/patch error | Initialize submodule, run prepare_sdk.py; preserve overlapping SDK edits, do not force reset |
| Missing MuJoCo | Use dev.sh sim or install the physics extra |
| Blank/stale viewer | Backend running, selected model valid, /viewer/ws connected; the viewer is not a control heartbeat |
| macOS PCBUSB load failure | Install MacCAN libPCBUSB.dylib under ~/.local/lib with PCBUSB symlink; dev.sh provides the dyld path |
| Unexpected stop | Read the estop/SafeLock reason; do not disable the watchdog |
| Empty logs | Service account needs systemd-journal membership |
| No green “arrived” | Old done, manual teaching, stop or disconnect invalidated it; issue an explicit new motion |

[Architecture and reuse boundaries](docs/ARCHITECTURE.md) · [Code map](docs/CODEMAP.md) · [Interaction](docs/TIMELINE.md) · [Future interfaces](docs/PLUGINS.md) · [Current status](PROGRESS.md)
