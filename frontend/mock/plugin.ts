/**
 * Dev-only Vite plugin that stands in for the backend — loaded only when the
 * dev server runs with `--mode mock` (`npm run dev:mock`).
 *
 * It serves three things the React UI would otherwise proxy to the FastAPI
 * service on 18790:
 *
 *   - `/api/*`     — REST, handled by `./api.ts` against an in-memory `MockState`
 *   - `/ws`        — a real WebSocket streaming `{type:"state"}` ControlState
 *                    frames at 20 Hz, with a tiny arm simulation (playback
 *                    progresses waypoint by waypoint, teach drifts the joints)
 *   - `/assets/urdf/**` — URDF + STL meshes straight from the vendored
 *                    submodule, so the 3D view renders for real
 *
 * `npm run dev` is untouched: the plugin is only mounted in mock mode.
 */

import { existsSync, readFileSync, statSync } from "node:fs";
import type { IncomingMessage, ServerResponse } from "node:http";
import path from "node:path";
import type { Duplex } from "node:stream";
import { fileURLToPath } from "node:url";
import { WebSocket, WebSocketServer } from "ws";
import type { Connect, Plugin, ViteDevServer } from "vite";

import { handleApi } from "./api";
import { createState, JOINTS } from "./state";
import type { MockState } from "./state";

const TICK_MS = 50; // 20 Hz state broadcast
const RATE_HZ = Math.round(1000 / TICK_MS);

//: Vendored submodule — the same directory the backend mounts at /assets/urdf.
const URDF_ROOT = fileURLToPath(new URL("../../vendor/reBotArm_control_py/urdf", import.meta.url));

const MIME: Record<string, string> = {
  ".urdf": "application/xml",
  ".stl": "model/stl",
  ".dae": "model/vnd.collada+xml",
  ".obj": "text/plain",
  ".mtl": "text/plain",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
};

interface SimContext {
  /** ms timestamp of when the current playback phase (settle/acting) began. */
  phaseAt: number;
  /** ms timestamp of the previous tick, for velocity differencing. */
  lastTickAt: number;
}

// ── REST ─────────────────────────────────────────────────────────────────────

function sendJson(res: ServerResponse, status: number, body: unknown): void {
  res.statusCode = status;
  res.setHeader("content-type", "application/json");
  res.end(JSON.stringify(body));
}

/** Buffer the request body (JSON for every route the UI calls), then dispatch. */
function apiHandler(state: MockState, req: Connect.IncomingMessage, res: ServerResponse): void {
  const url = new URL(req.url ?? "/", "http://localhost");
  const chunks: Buffer[] = [];
  req.on("data", (chunk: Buffer) => chunks.push(chunk));
  req.on("end", () => {
    let body: unknown;
    const raw = Buffer.concat(chunks).toString("utf8").trim();
    if (raw) {
      try {
        body = JSON.parse(raw);
      } catch {
        sendJson(res, 400, { detail: "invalid JSON body" });
        return;
      }
    }
    const result = handleApi(state, req.method ?? "GET", url.pathname, url.searchParams, body);
    if (result.status === 204) {
      res.statusCode = 204;
      res.end();
      return;
    }
    sendJson(res, result.status, result.body);
  });
}

// ── static URDF / meshes ─────────────────────────────────────────────────────

function urdfHandler(req: Connect.IncomingMessage, res: ServerResponse): void {
  // Middleware runs un-mounted (we match prefixes ourselves), so req.url is
  // the full `/assets/urdf/...` — strip the mount prefix before joining.
  const rel = decodeURIComponent((req.url ?? "/").split("?")[0]).replace(/^\/assets\/urdf\/?/, "");
  const file = path.normalize(path.join(URDF_ROOT, rel));

  // Guard against `..` escaping the urdf tree.
  if (!file.startsWith(URDF_ROOT + path.sep)) {
    res.statusCode = 403;
    res.end("forbidden");
    return;
  }
  if (!existsSync(file) || !statSync(file).isFile()) {
    res.statusCode = 404;
    res.end("not found");
    return;
  }
  res.setHeader(
    "content-type",
    MIME[path.extname(file).toLowerCase()] ?? "application/octet-stream",
  );
  res.end(readFileSync(file));
}

// ── websocket + arm simulation ───────────────────────────────────────────────

function simulateTick(state: MockState, nowMs: number, sim: SimContext): void {
  if (state.mode === "estop") return; // frozen in place until cleared

  const dt = Math.max((nowMs - sim.lastTickAt) / 1000, 0);
  sim.lastTickAt = nowMs;
  const prev = { ...state.positions };

  if (state.mode === "teach") {
    // Gentle random drift stands in for zero-force dragging, so the joint
    // readout and capture button behave like the real thing.
    for (const joint of JOINTS) {
      state.positions[joint] += (Math.random() - 0.5) * 0.004;
    }
  } else if (state.mode === "playback" && state.playback) {
    advancePlayback(state, nowMs, sim);
  }

  for (const joint of JOINTS) {
    state.velocities[joint] = dt > 0 ? (state.positions[joint] - prev[joint]) / dt : 0;
  }
}

function advancePlayback(state: MockState, nowMs: number, sim: SimContext): void {
  const pb = state.playback;
  if (!pb) return;
  const routine = state.routines.find((r) => r.id === pb.routine_id);
  // A goto runs one waypoint outside the stored routine (the backend's
  // ephemeral single-waypoint routine); full playback walks the routine.
  const waypoint = state.gotoWaypoint ?? routine?.waypoints[pb.waypoint_index];
  if (!waypoint) {
    finishPlayback(state);
    return;
  }

  switch (pb.phase) {
    case "moving": {
      // Step a fixed fraction of the move per tick, so the whole move takes
      // about `duration_s` (the target is snapped once within one step, which
      // is exactly what the real arm's trapezoidal profile ends as).
      const step = TICK_MS / 1000 / Math.max(waypoint.duration_s, 0.1);
      let maxError = 0;
      for (const joint of JOINTS) {
        const target = waypoint.joints[joint] ?? 0;
        const delta = target - state.positions[joint];
        if (Math.abs(delta) <= step) {
          state.positions[joint] = target;
        } else {
          state.positions[joint] += Math.sign(delta) * step;
        }
        maxError = Math.max(maxError, Math.abs(target - state.positions[joint]));
      }
      if (maxError === 0) {
        if (waypoint.actions.length === 0) {
          nextWaypoint(state);
        } else {
          pb.phase = "settling";
          sim.phaseAt = nowMs;
        }
      }
      break;
    }
    case "settling": {
      if (nowMs - sim.phaseAt >= waypoint.settle_ms) {
        pb.phase = "acting";
        pb.action_index = 0;
        sim.phaseAt = nowMs;
      }
      break;
    }
    case "acting": {
      const action = waypoint.actions[pb.action_index ?? 0];
      // sleep takes its duration; a shutter burst runs count × interval_s
      // (with a minimum beat so a single instant shot still reads as one).
      const durationMs =
        action?.type === "sleep"
          ? action.duration_s * 1000
          : Math.max((action?.count ?? 1) * (action?.interval_s ?? 0) * 1000, 300);
      if (nowMs - sim.phaseAt >= durationMs) {
        pb.action_index = (pb.action_index ?? 0) + 1;
        sim.phaseAt = nowMs;
        if (pb.action_index >= waypoint.actions.length) nextWaypoint(state);
      }
      break;
    }
    default:
      break;
  }
}

/**
 * End a run the way the real controller ends one.
 *
 * `Controller` keeps its finished `RoutineExecutor` rather than dropping it,
 * so `/ws` goes on broadcasting the final progress — phase `done`, and
 * `waypoint_index` sitting one past the last waypoint, because
 * `_advance_waypoint` increments before it notices it is finished. The UI
 * relies on that lingering `done` to keep saying 已到位 while the arm holds,
 * so a mock that nulled the progress out would preview a state the real
 * device never reaches.
 */
function finishPlayback(state: MockState): void {
  const pb = state.playback;
  if (!pb) return;
  pb.phase = "done";
  pb.finished = true;
  pb.action_index = null;
  state.mode = "idle";
  state.gotoWaypoint = null;
}

function nextWaypoint(state: MockState): void {
  const pb = state.playback;
  if (!pb) return;
  pb.waypoint_index += 1;
  if (pb.waypoint_index >= pb.waypoint_total) {
    finishPlayback(state);
    return;
  }
  const routine = state.routines.find((r) => r.id === pb.routine_id);
  pb.phase = "moving";
  pb.action_index = null;
  pb.action_total = routine?.waypoints[pb.waypoint_index].actions.length ?? 0;
}

function broadcastState(state: MockState, clients: Set<WebSocket>): void {
  const data = {
    t: Date.now() / 1000,
    positions: { ...state.positions },
    velocities: { ...state.velocities },
    rate_hz: RATE_HZ,
    mode: state.mode,
    estop: {
      latched: state.estop.latched,
      reason: state.estop.reason,
      source: state.estop.source,
      engaged_at: state.estop.engaged_at,
      freeze_pose: state.estop.freeze_pose ? { ...state.estop.freeze_pose } : null,
    },
    playback: state.playback ? { ...state.playback } : null,
  };
  const message = JSON.stringify({ type: "state", data });
  for (const client of clients) {
    if (client.readyState === WebSocket.OPEN) client.send(message);
  }
}

function wireWebSocket(server: ViteDevServer, clients: Set<WebSocket>): void {
  const wss = new WebSocketServer({ noServer: true });
  server.httpServer?.on("upgrade", (req: IncomingMessage, socket: Duplex, head: Buffer) => {
    const { pathname } = new URL(req.url ?? "/", "http://localhost");
    if (pathname !== "/ws") return; // leave Vite's own HMR socket alone
    wss.handleUpgrade(req, socket, head, (ws) => {
      clients.add(ws);
      ws.on("close", () => clients.delete(ws));
      ws.on("error", () => clients.delete(ws));
    });
  });
}

// ── plugin ───────────────────────────────────────────────────────────────────

export function mockPreview(): Plugin {
  return {
    name: "rebot-mock-preview",
    configureServer(server) {
      const state = createState();
      const clients = new Set<WebSocket>();
      const sim: SimContext = { phaseAt: 0, lastTickAt: Date.now() };

      // Plain middleware with a prefix check rather than a connect mount path:
      // a mount path rewrites req.url, and the handlers need the full path.
      server.middlewares.use((req: Connect.IncomingMessage, res: ServerResponse, next) => {
        const { pathname } = new URL(req.url ?? "/", "http://localhost");
        if (pathname.startsWith("/api")) {
          apiHandler(state, req, res);
          return;
        }
        if (pathname.startsWith("/assets/urdf")) {
          urdfHandler(req, res);
          return;
        }
        next();
      });

      wireWebSocket(server, clients);

      const timer = setInterval(() => {
        simulateTick(state, Date.now(), sim);
        broadcastState(state, clients);
      }, TICK_MS);
      server.httpServer?.on("close", () => clearInterval(timer));
    },
  };
}
