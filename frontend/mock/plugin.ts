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
 *                    walks the block list, teach drifts the joints)
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

import { easingFn } from "../src/timeline/model";
import type { Block, EventMarker, HoldBlock } from "../src/types";
import { handleApi } from "./api";
import { createState, JOINTS } from "./state";
import type { MockState } from "./state";

const TICK_MS = 50; // 20 Hz state broadcast
const RATE_HZ = Math.round(1000 / TICK_MS);
const TICK_S = TICK_MS / 1000;

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
  /** ms timestamp of the previous tick, for velocity differencing. */
  lastTickAt: number;
  /** Markers already fired in the current block — a wait must not re-suspend after resume. */
  fired: Set<string>;
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
  const dt = Math.max((nowMs - sim.lastTickAt) / 1000, 0);
  sim.lastTickAt = nowMs;
  if (state.mode === "estop") return; // frozen in place until cleared

  const prev = { ...state.positions };

  if (state.mode === "teach") {
    // Gentle random drift stands in for zero-force dragging, so the joint
    // readout and capture button behave like the real thing.
    for (const joint of JOINTS) {
      state.positions[joint] += (Math.random() - 0.5) * 0.004;
    }
  } else if (state.mode === "playback" && state.playback) {
    advancePlayback(state, sim);
  }

  for (const joint of JOINTS) {
    state.velocities[joint] = dt > 0 ? (state.positions[joint] - prev[joint]) / dt : 0;
  }
}

/** Where a marker sits inside its block, in seconds (proportion → seconds). */
function markerTimeInBlock(block: Block, marker: EventMarker): number {
  return block.type === "hold" ? marker.at : marker.at * block.duration_s;
}

function poseJoints(state: MockState, poseId: string): Record<string, number> | undefined {
  return state.poses.find((p) => p.id === poseId)?.joints;
}

function lerpInto(
  state: MockState,
  from: Record<string, number>,
  to: Record<string, number>,
  k: number,
): void {
  for (const joint of JOINTS) {
    const a = from[joint] ?? 0;
    const b = to[joint] ?? 0;
    state.positions[joint] = a + (b - a) * k;
  }
}

/**
 * Walk the block list one tick.
 *
 * Holds count down their commanded duration; transitions eased-lerp the
 * joints from the previous station's pose to the next one's. Markers fire as
 * their in-block time passes (the white flash itself is drawn by the UI from
 * the same clock); a wait marker suspends the run until the operator resumes.
 */
function advancePlayback(state: MockState, sim: SimContext): void {
  const pb = state.playback;
  if (!pb || pb.finished) return;

  // A goto is an ephemeral one-block run: eased lerp to the pose, then done.
  if (state.goto) {
    const goto_ = state.goto;
    pb.t_in_block += TICK_S;
    const k = easingFn("ease_in_out", pb.t_in_block / goto_.duration_s);
    lerpInto(state, goto_.from, goto_.to, k);
    if (pb.t_in_block >= goto_.duration_s) {
      lerpInto(state, goto_.from, goto_.to, 1);
      finishPlayback(state);
    }
    return;
  }

  const sequence = state.sequences.find((s) => s.id === pb.sequence_id);
  const block = sequence?.blocks[pb.block_index];
  if (!sequence || !block) {
    // Defensive: the REST layer refuses to delete/patch a running sequence,
    // so this should be unreachable — but a run whose plan vanished must say
    // so, not hang.
    pb.phase = "aborted";
    pb.finished = true;
    pb.error = "sequence disappeared mid-run";
    state.mode = "idle";
    return;
  }

  if (pb.phase === "wait") return; // suspended until POST /api/execute/resume

  pb.t_in_block += TICK_S;

  // Fire markers whose time has come, in block order. A wait marker suspends
  // the run exactly on its own time; everything else is an instant event.
  for (const marker of block.markers) {
    if (sim.fired.has(marker.id)) continue;
    if (pb.t_in_block < markerTimeInBlock(block, marker)) continue;
    sim.fired.add(marker.id);
    if (marker.kind === "wait") {
      pb.t_in_block = markerTimeInBlock(block, marker);
      pb.phase = "wait";
      return;
    }
  }

  if (block.type === "hold") {
    // Settle toward the station's pose. Transitions already land exactly on
    // it — this covers the very first block and poses deleted mid-life.
    const target = poseJoints(state, block.pose_id);
    if (target) {
      const rate = Math.min(1, TICK_S * 4);
      let allSettled = true;
      for (const joint of JOINTS) {
        const delta = (target[joint] ?? 0) - state.positions[joint];
        const settled = Math.abs(delta) < 1e-4;
        state.positions[joint] = settled ? target[joint] : state.positions[joint] + delta * rate;
        if (!settled) allSettled = false;
      }
      // The arm is no longer approaching once every joint has settled.
      if (allSettled) pb.approaching = false;
    }
    if (pb.t_in_block >= block.duration_s) nextBlock(state, sim);
    return;
  }

  // Transition: eased joint-space lerp between the flanking stations.
  const prev = blockAt(sequence.blocks, pb.block_index, -1);
  const next = blockAt(sequence.blocks, pb.block_index, +1);
  const from = (prev && poseJoints(state, prev.pose_id)) ?? { ...state.positions };
  const to = (next && poseJoints(state, next.pose_id)) ?? { ...state.positions };
  const k = easingFn(block.easing, pb.t_in_block / block.duration_s);
  lerpInto(state, from, to, k);
  if (pb.t_in_block >= block.duration_s) {
    lerpInto(state, from, to, 1);
    nextBlock(state, sim);
  }
}

function blockAt(blocks: Block[], index: number, step: -1 | 1): HoldBlock | undefined {
  for (let i = index + step; i >= 0 && i < blocks.length; i += step) {
    if (blocks[i].type === "hold") return blocks[i] as HoldBlock;
  }
  return undefined;
}

function nextBlock(state: MockState, sim: SimContext): void {
  const pb = state.playback;
  if (!pb) return;
  pb.block_index += 1;
  pb.t_in_block = 0;
  sim.fired.clear();
  if (pb.block_index >= pb.block_total) {
    finishPlayback(state);
    return;
  }
  const sequence = state.sequences.find((s) => s.id === pb.sequence_id);
  const block = sequence?.blocks[pb.block_index];
  if (!block) {
    finishPlayback(state);
    return;
  }
  pb.phase = block.type;
  // A new hold block starts approaching until the arm settles at the pose.
  pb.approaching = block.type === "hold";
}

/**
 * End a run the way the real controller ends one.
 *
 * `Controller` keeps its finished executor rather than dropping it, so `/ws`
 * goes on broadcasting the final progress — phase `done`, and `block_index`
 * sitting one past the last block, because the advance step increments
 * before it notices it is finished. The UI relies on that lingering `done`
 * to keep saying 到位 while the arm holds, so a mock that nulled the progress
 * out would preview a state the real device never reaches.
 */
function finishPlayback(state: MockState): void {
  const pb = state.playback;
  if (!pb) return;
  pb.phase = "done";
  pb.finished = true;
  pb.block_index = pb.block_total;
  pb.t_in_block = 0;
  state.mode = "idle";
  state.goto = null;
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
      const sim: SimContext = { lastTickAt: Date.now(), fired: new Set() };

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
