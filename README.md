# Teach & Repeat · 示教回放

**English** | [中文](./README.zh-CN.md)

> **Teach it once, it walks it a thousand times.**
> 教它走一遍，它替你走一万遍。

A teach-and-repeat platform for spatial waypoints on a robot arm. Drag the arm to a pose, let go, hit record — that's a waypoint. Hang actions on waypoints (a camera shutter is the first), then press play: the arm walks the whole tour, and at every stop it settles and acts.

```
teach                     store                          act
drag → release → record → ordered waypoints + actions → arrive → settle → fire → next
```

The first deployment is automated multi-view photography: a reBot-RS six-axis arm holds a Canon camera, the subject stays put. Photos land on the camera's SD card — this project only drives the arm to the pose and presses the shutter.

> Software complete, 386 tests; two on-hardware checks pending. Read **[AGENTS.md](./AGENTS.md)** before touching the code (four rules that fail silently — wrong results, no errors).

---

## Requirements

| | | If you don't have it |
|---|---|---|
| reBot-RS robot arm | 6 joints + gripper, RobStride, 48V, CAN | `--sim` runs a simulated arm — everything but real motion works |
| USB-CAN adapter | host ↔ arm | same |
| Canon camera | body must support Bluetooth remote | `SimShutter` — shutter calls are logged only |
| XIAO ESP32-S3 | shutter bridge: USB to host, BLE to camera | same |
| reComputer R2x | deployment target | any dev machine works |

Software: **uv**, **Node 18+**, Python 3.11 (installed by uv).
Kinematics, dynamics and collision checking run on macOS and Linux dev machines — **only the CAN transport needs real hardware**.

---

## Install

```bash
git clone --recursive https://github.com/Love4yzp/rebot-copilot-camera.git
cd rebot-copilot-camera
uv sync
cd frontend && npm install && npm run build && cd ..
```

Already cloned without `--recursive`: `git submodule update --init`.

**Don't skip this.** The arm control library is a submodule, not a pip dependency (upstream has no `[build-system]`, so it can't install as a git dependency). Miss it and `uv sync` still succeeds — then imports fail.

---

## Try it (no hardware)

```bash
uv run -m backend.app --sim
```

Open **http://127.0.0.1:18790**. The simulated arm responds to teaching drags, walks waypoints and pretends to fire the shutter — the whole workflow runs.

Frontend work: `cd frontend && npm run dev` (hot reload, proxies to 18790).
Tests: `uv run pytest`.

`./dev.sh` wraps the two local modes — `./dev.sh prod` builds the frontend and runs the backend on one origin (add `--sim` for no hardware), `./dev.sh sim` runs the frontend alone. API integration without the frontend: `./dev.sh prod --no-build`, `/docs` is the console (requires the frontend to have been built once). The old name `mock` still works as an alias for `sim`, slated for removal. **Whatever the mode, the backend's arm safety measures (estop latch / motion gate / watchdog) are always on.** Deployment to the device is a different script, `./device.sh` — see "Deploy to the R2x"; daily use never touches it.

**Preview the frontend without the backend**: `./dev.sh sim`, or `cd frontend && npm run dev:mock`. API, WebSocket state stream and the 3D arm are all replaced by an in-memory mock — list / teach / record / play / estop all work, data is just ephemeral. The 3D arm reads the URDF from vendor/, so run `git submodule update --init` first; then open http://localhost:5173.

---

## Shoot a set

### 1 · Start, and confirm it's on the real arm

Arm on CAN, ESP32 on USB, camera mounted on the gripper. **No `--sim`**:

```bash
uv run -m backend.app
curl -s http://127.0.0.1:18790/api/health | grep simulated    # must be false
```

**Don't skip this check.** When the real arm is unreachable the service silently falls back to the simulator and keeps running — UI, logs and buttons all look normal, only the arm doesn't move.

### 2 · Pair the camera (once)

1. Camera menu `Wireless communication settings > Bluetooth` → set to **Remote control** (not "Smartphone"). Pairing fails without this.
2. Select "Pairing" on the camera; it waits.
3. `curl -X POST http://127.0.0.1:18790/api/shutter/pair` (see the [firmware README](./firmware/esp32-shutter/README.md)).
4. The pairing is stored on the board and reconnects automatically on power-up.

Verify the whole chain — **this takes a real photo**:

```bash
curl -X POST 'http://127.0.0.1:18790/api/shutter/test?shoot=true'
```

Without `?shoot=true` no frame is burned, but both links are still checked: `connected` in the reply is the USB half, `camera` is the BLE half. **Only the second one answers "will a frame actually be taken"** — a board answering perfectly while nothing is paired is this machine's most expensive kind of silent failure.

### 3 · Record poses

"＋ 录位姿" at the bottom of the library opens a teach bar:

1. The arm **holds still first** — an arm that goes limp with nobody holding it will sag.
2. **Give it a push** — it detects the motion and releases into zero-force float; drag it freely.
3. Drag to the pose, **let go**. About 0.25 s after your hand stops, it locks in place.
4. Name it, press "保存位姿" (Save pose). Repeat 2–4 for the next one.

Poses live in the library and are **linked** by any number of sequences — edit a pose and every reference changes with it. The daily "go there" verb sits on each pose card: "去这里". The teach bar has its own estop button — in this mode your hands are on the arm, not the keyboard.

### 4 · Cut the timeline

**Drag a pose card onto the timeline** — that is one station (a hold block). A transition block is generated between two different poses automatically: the arm must physically get there, which is physics, not a setting — transitions cannot be deleted, only retimed and re-eased.

- Drag a hold's right edge to trim it; drag the whole block to reorder.
- **Double-click a block** to pin an event marker: shutter, wait, or any installed plugin (e.g. a turntable). Markers are pinned to a time inside their parent block and move/trim with it.
- Select a block or marker to edit its parameters in the inspector; `Delete` removes the selection.

"存为模板" (Save as template) snapshots the current sequence as a structural recipe — stations, durations, markers, transition parameters, **no joint angles**. "用它" (Use it) on a template card opens the **station-by-station wizard**: at each station, drag the arm and record a fresh pose, or bind an existing one (optionally "去这里" first to check the framing). The wizard generates a detached ordinary sequence — editing or deleting the template afterwards never touches it.

### 5 · Preview and execute

Two verbs, never one button:

- **▶ 预演 (Preview)**: the playhead walks the plan ruler and the monitor plays a greyscale simulation (transition easing is visible) — **the arm does not move**. Preview is not a machine state; none of the four status colours light up.
- **执行（臂会动）(Execute — the arm will move)**: the arm runs for real. The playhead walks true progress, the monitor flips to the live view, amber lights up, and the timeline is locked until the run ends.

A wait marker stops both: playback suspends there until "继续" (Continue). Before executing, the **entire sequence** is pre-checked for joint limits and self-collision, including the paths between adjacent poses — two individually legal poses can have a straight-line path through the arm's own base. Illegal means refused, **the arm doesn't move at all**.

### Reading the UI

The screen itself is grey. **Any colour means the machine is doing something** — so a glance from beside the arm is enough; no need to read text up close. The light band across the very top is the main signal:

| Colour | Meaning |
|---|---|
| dark | idle |
| amber sweep | arm is moving — hands off |
| amber solid | teaching — arm is limp, push it |
| white flash | shutter fired |
| green | in place, arm holding |
| red pulse | estopped |

Colour and words always appear together. **No green means the UI doesn't know where the arm is** — that's what an estop freeze or a manual push looks like, and it's not a bug.

---

## Emergency stop

**The big red button in the top bar, or `Esc`.** Works during playback and teaching.

- The arm **holds torque and freezes in place** — no power cut, no going limp.
- Every request that would move the arm returns 409 with a reason.
- **After clearing, it stays put — nothing auto-resumes** — the scene has most likely changed by the time you clear (arm dragged away, sample removed).

Besides the human button, a watchdog triggers automatically: the control loop persistently late, sustained CAN read failures, joints persistently drifting while holding. The reason is shown on the estop bar.

## Shutting down

**Ctrl+C (or `systemctl stop`) does not exit immediately**: the arm first moves slowly back to the zero pose (all joints q=0, ~14°/s, up to ~45 s), and only then does the control loop stop and the process exit. Pressing Ctrl+C again during the park neither speeds it up nor interrupts it — repeated signals are ignored. After the process exits the motors stay energized, pinning the arm at zero.

One exception: **if the emergency stop is latched, shutdown does not park** — the arm exits holding its frozen pose. A latched stop means something went wrong, and planning a new motion is exactly what it exists to prevent.

---

## Configuration

| Env var | Default | Notes |
|---|---|---|
| `REBOT_HOST` | `127.0.0.1` | Listen address. **Setting `0.0.0.0` opens arm control to the whole network — this project has no auth layer** |
| `REBOT_PORT` | `18790` | Port |
| `REBOT_DATA_DIR` | `./data` | Operator data root: `poses/`, `sequences/`, `templates/` live under it, one JSON per document |
| `REBOT_SHUTTER_PORT` | `/dev/rebot-shutter` | Shutter board serial port. The stable udev name — never `/dev/ttyACM*`, whose numbering swaps with plug order |
| `REBOT_SHUTTER_BAUD` | `115200` | Shutter board baud. Change it together with the firmware's `-D REBOT_SERIAL_BAUD` |
| `REBOT_TUNING_FILE` | `./config/tuning.yaml` | Where the tuning panel persists. Missing file = defaults |

CLI: `--sim` / `--host` / `--port`.
`device.sh` additionally requires `REBOT_HOST_SSH` (no default — point it at your device, e.g. `recomputer@192.168.1.10`) and reads `REBOT_REMOTE_DIR`.

**Tuning panel** (the「调参」button on the right of the monitor area; entering it in prod asks for confirmation): float kp/kd, float/lock thresholds, arrival settle, approach speed limit, and the payload profile (bare/camera/gripper). Changes apply hot — float gains can even be tuned mid-drag; but a payload switch is refused while the arm floats, and every write is refused while a sequence executes. Hot changes live in memory only;「保存到配置」writes `config/tuning.yaml`, and「恢复已保存」reverts to the last save.

**After mounting the camera**: weigh the body + mount, enter the mass under「负载 → 相机质量」and the centre-of-mass offset as com, switch to the camera profile, then verify by float-drift feel — release the estop, the backend drops into zero-force teach, and the arm should stay put; drift means the gravity feedforward is off (per-joint correction workflow in `docs/HARDWARE_NOTES.md` #B2). No code constants to edit anymore.

---

## Deploy to the R2x

Point `device.sh` at your device first — no target is baked in:

```bash
export REBOT_HOST_SSH=recomputer@<device-ip>   # `recomputer` is the reComputer factory-default user

./device.sh setup     # once: uv + systemd + CAN + udev + groups
./device.sh push      # after changes: build frontend + rsync + restart
./device.sh enable    # start on boot
./device.sh status    # running? real arm or simulator?
./device.sh logs      # tail journalctl
./device.sh open      # SSH tunnel + open browser
./device.sh run       # foreground, for print/breakpoint debugging
```

**No auth layer**, and this service moves a 48V arm. Two deployment shapes:

**Localhost only (default, the unit in this repo)**

The service listens on `127.0.0.1`; remote access goes through an SSH tunnel: `./device.sh open` builds the tunnel and opens the browser. Right for networks you don't trust.

**LAN access (common on a reComputer)**

Device on a reComputer, other hosts on the same LAN opening the UI directly: change `Environment=REBOT_HOST=127.0.0.1` in `deploy/rebot-copilot-camera.service` to `0.0.0.0` (or the device's static LAN IP), `push`, then visit `http://<device-ip>:18790` from the LAN. Note **anyone who can reach that port can move the arm** — only do this on a LAN you control; a static IP avoids the "device joined a new network and got exposed" surprise.

Untrusted network plus remote access: don't expose the service directly — put an authenticating reverse proxy in front of the localhost service (Caddy / nginx basic auth is enough), or go through a private network with ACLs (WireGuard / Tailscale). Auth is the deployment layer's job, not this application's — such configs belong to the deployment site and stay out of the repo.

`push` never deletes `data/` on the device — the operator's taught poses and sequences live there and exist only there.

---

## Troubleshooting

| Symptom | Probably | Do |
|---|---|---|
| Service running, arm dead still | silently fell back to the simulator | `curl -s :18790/api/health \| grep simulated`. `true` means not connected; the startup log has the reason |
| Falls back on macOS, log says `load PCBUSB failed` | missing MacCAN CAN runtime — macOS has no SocketCAN; CAN goes through `libPCBUSB.dylib` (supports PEAK and PEAK-compatible adapters such as XCAN-USB) | install `libPCBUSB.dylib` into `~/.local/lib/` with a symlink named `PCBUSB` pointing at it (motorbridge ships a tarball under `third_party/pcan/macos/`). `./dev.sh prod` injects the dyld search path itself; a direct `uv run -m backend.app` needs `DYLD_FALLBACK_LIBRARY_PATH="$HOME/.local/lib:/usr/local/lib:/usr/lib"` |
| `import reBotArm_control_py` fails | submodule not pulled | `git submodule update --init` |
| Play returns **400** | a waypoint exceeds limits / self-collides, or a path between adjacent points intersects | read `detail.reasons` in the response — it names the joint or segment. Note **recording only warns, doesn't refuse** (the arm is physically there); the check happens before play |
| Play returns **409** | estop latched, or already playing / teaching | `detail` says which |
| Arm won't drag | teach not on, or estop latched | with teach on the arm **starts holding** — push it once to release. Design, not stuck |
| Shutter self-test passes, nothing shot during play | the camera declined or went to sleep — `camera: true` says it was paired when asked, not that it will answer | test the whole chain with `?shoot=true`. Usual causes: camera asleep, Bluetooth not set to "remote control", or the board rebooted and lost its pairing (re-pair with `POST /api/shutter/pair`) |
| Host receives nothing from the ESP32 at all | `platformio.ini` missing `-D ARDUINO_USB_CDC_ON_BOOT=1` | add it and reflash. Without it `Serial` goes to the UART0 pins: the board enumerates, the port opens, writes succeed — **no error anywhere in the chain** |
| `/api/logs` is empty | service account not in the `systemd-journal` group | `./device.sh setup` adds it; log in again after |
| Chinese becomes `?` in journalctl | systemd defaults to `LANG=C` | the unit and `device.sh run` both set `LANG=zh_CN.UTF-8` |
| Arm suddenly stopped on its own | watchdog-triggered estop | reason is on the estop bar. All three conditions require **sustained** failure — a jitter or a dropped frame won't trigger |
| 3D blank in the frontend | URDF / meshes not loaded | the drawer says "load failed" / "mesh missing" / "3D failed to initialise" — follow that line. Most common: submodule not pulled, `git submodule update --init`. Self-check: `curl -I :18790/assets/urdf/00-arm-rs_asm-v3/meshes/base_link.STL` should return 200 — note meshes live at the **package root**, not under `urdf/` |
| Never lights green (in place) | arm was estopped or moved by teaching | correct behaviour. After the arm is frozen elsewhere or pushed by hand, the UI stops claiming to know where it is — "去这里" any pose or run the sequence again |

---

## API

Interactive docs at `http://127.0.0.1:18790/docs`, OpenAPI at `/openapi.json`.

| | |
|---|---|
| `GET/POST /api/estop` · `POST /api/estop/clear` | Emergency stop. Engage always 200s; repeat engages keep the first reason |
| `GET/POST /api/poses` · `PATCH/DELETE /api/poses/{id}` | Pose library. `POST /api/poses/capture` records the arm's current pose |
| `GET /api/poses/{id}/links` | Which sequences reference this pose — asked before delete/overwrite |
| `POST /api/poses/{id}/goto` | Single pose: go, hold. Accepts `{"source": "..."}` to record who triggered it |
| `GET/POST /api/sequences` · `GET/PATCH/DELETE /api/sequences/{id}` | Sequence CRUD. Block writes are normalized on the way in (transitions are automatic); a running sequence is locked against edits |
| `POST /api/sequences/{id}/execute` · `POST /api/execute/stop` · `POST /api/execute/resume` | Execution. Full pre-flight before execute (path + pose references + plugin availability); resume continues past a wait marker |
| `GET/POST /api/templates` · `DELETE /api/templates/{id}` · `POST /api/templates/{id}/instantiate` | Structural recipes with pose slots; instantiate copies with each slot bound to a library pose |
| `POST /api/teach` | Zero-force teaching toggle |
| `POST /api/shutter/test` | Shutter self-test. Checks both links, USB and BLE; `?shoot=true` takes a real shot |
| `POST /api/shutter/pair` | Put the board into BLE pairing mode and wait for the camera (30s). 409 while playing |
| `GET /api/plugins` · `POST /api/plugins/probe` | Which action plugins are installed and usable. The frontend renders trigger forms from this |
| `GET /api/control` · `/api/health` · `/api/logs` · `WS /ws` | State and logs |
| `WS /api/events` | Semantic event stream: arrived / action / estop. For integrators; no 20 Hz joint angles |

**Every endpoint that moves the arm returns 409 with a reason during estop.**

**Extending the machine**: action plugins (in-process — drop a folder with a `plugin.json` into `plugins/`, or `uv pip install` a package declaring a `rebot.actions` entry point), trigger sources (HTTP clients calling `goto`), event subscriptions (WS clients on `/api/events`). Full contracts for all three extension points and the no-hardware dev loop `uv run -m backend.actions.check` are in [`docs/PLUGINS.md`](./docs/PLUGINS.md); the worked example is an installable package at [`examples/rebot-plugin-turntable/`](./examples/rebot-plugin-turntable/) rather than a listing in a document, so the packaging metadata is covered by tests.

**Agent API** (`/api/agent/*`) for external LLMs / scripts: `acquire` takes an exclusive token, `control/joints` and `control/play/{id}` issue commands, `release` hands it back (`?force=true` lets the Web UI forcibly reclaim). Leases expire after 5 idle minutes or 30 minutes held. **It grants control, not safety exemption** — during estop the agent is refused exactly like a human. Full parameters in `/docs`.

---

## More

| | |
|---|---|
| [AGENTS.md](./AGENTS.md) | Read before coding: four iron rules, code map, unbreakable conventions, architecture layers |
| [docs/HARDWARE_NOTES.md](./docs/HARDWARE_NOTES.md) | Hardware facts and traps — **verified** and **to-be-measured** kept strictly apart |
| [PROGRESS.md](./PROGRESS.md) | Progress, blockers, handoff protocol |
| [firmware/esp32-shutter/](./firmware/esp32-shutter/README.md) | Flashing, pairing, serial protocol |
| [issue #1](https://github.com/Love4yzp/rebot-copilot-camera/issues/1) | Original design and decision log (archived, no longer appended) |

The arm layer is not written here — kinematics, dynamics, gravity compensation, trajectory planning and URDF all come from [reBotArm_control_py](https://github.com/Seeed-Projects/reBotArm_control_py).

MIT
