/**
 * Mock REST handlers for the no-backend preview.
 *
 * Pure functions over `MockState`. The routes here are the v2 backend's
 * contract — the React UI only ever talks to these shapes, and the FastAPI
 * side will implement the same surface. Behavior follows the conventions the
 * UI depends on: 201 on create, 204 on delete, 400 on an empty-sequence
 * execute, 409 on motion commands while the estop is latched or another run
 * is in flight, re-engaging a latched estop reports `changed: false`.
 */

import { sequenceDuration, normalize, newId } from "../src/timeline/model";
import type { Block, HoldBlock } from "../src/types";
import type {
  MockEstop,
  MockPose,
  MockSequence,
  MockState,
  MockTemplate,
} from "./state";
import { JOINTS } from "./state";

export interface MockResponse {
  status: number;
  body?: unknown;
}

/** The plugin writes every response as JSON; only the status/body matter here. */
function json(status: number, body: unknown): MockResponse {
  return { status, body };
}

/** Plain-object check for the tuning deep merge (arrays like com replace, not merge). */
function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function notFound(detail: string): MockResponse {
  return json(404, { detail });
}

function badRequest(detail: string): MockResponse {
  return json(400, { detail });
}

function conflict(detail: string): MockResponse {
  return json(409, { detail });
}

/** 409 the way the motion gate phrases it: structured, with the latch's story. */
function estopConflict(state: MockState): MockResponse {
  return json(409, {
    detail: {
      error: "estop_latched",
      message: "Emergency stop is engaged; clear it before commanding motion.",
      reason: state.estop.reason,
      source: state.estop.source,
      engaged_at: state.estop.engaged_at,
    },
  });
}

/** `{mode, playing, teaching, rate_hz, playback, source}` — the PlaybackState shape. */
function playbackState(state: MockState): Record<string, unknown> {
  return {
    mode: state.mode,
    playing: state.mode === "playback",
    teaching: state.mode === "teach",
    rate_hz: 20,
    playback: state.playback,
    // Retained while the run's record is: an aborted run still says who
    // started it; an explicit stop cleared both.
    source: state.playback !== null ? state.playback_source : null,
  };
}

function estopStatus(estop: MockEstop, changed?: boolean): Record<string, unknown> {
  return {
    latched: estop.latched,
    reason: estop.reason,
    source: estop.source,
    engaged_at: estop.engaged_at,
    freeze_pose: estop.freeze_pose,
    changed,
  };
}

function findPose(state: MockState, id: string): MockPose | undefined {
  return state.poses.find((p) => p.id === id);
}

function findSequence(state: MockState, id: string): MockSequence | undefined {
  return state.sequences.find((s) => s.id === id);
}

function findTemplate(state: MockState, id: string): MockTemplate | undefined {
  return state.templates.find((t) => t.id === id);
}

function holdsOf(blocks: Block[]): HoldBlock[] {
  return blocks.filter((b): b is HoldBlock => b.type === "hold");
}

function toSummary(sequence: MockSequence): Record<string, unknown> {
  return {
    id: sequence.id,
    name: sequence.name,
    updated_at: sequence.updated_at,
    station_count: holdsOf(sequence.blocks).length,
    duration_s: sequenceDuration(sequence.blocks),
  };
}

/** A live run holds a structural claim on its sequence — see TIMELINE rule 5. */
function isExecuting(state: MockState, sequenceId: string): boolean {
  return (
    state.mode === "playback" &&
    state.playback !== null &&
    !state.playback.finished &&
    state.playback.sequence_id === sequenceId
  );
}

/** URL-decode a path segment (ids are hex, but be safe). */
function decodeSegment(raw: string): string {
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

export function handleApi(
  state: MockState,
  method: string,
  pathname: string,
  query: URLSearchParams,
  body: unknown,
): MockResponse {
  const now = Date.now() / 1000;
  const reqBody = (body ?? {}) as Record<string, unknown>;

  // ── health ────────────────────────────────────────────────────────────────
  if (pathname === "/api/health" && method === "GET") {
    return json(200, {
      status: "ok",
      version: "0.0.0-mock",
      uptime_s: Math.round(now - state.started_at),
      mode: "sim",
      estop: {
        latched: state.estop.latched,
        reason: state.estop.reason,
        source: state.estop.source,
      },
      shutter: { simulated: true },
      arm: {
        simulated: true,
        urdf: "vendor/reBotArm_control_py/urdf/00-arm-rs_asm-v3/urdf/00-arm-rs_asm-v3.urdf",
        end_effector_frame: "gripper_end",
        joints: JOINTS,
      },
    });
  }

  // ── estop ─────────────────────────────────────────────────────────────────
  if (pathname === "/api/estop" && method === "GET") {
    return json(200, estopStatus(state.estop));
  }
  if (pathname === "/api/estop" && method === "POST") {
    const reason =
      typeof reqBody.reason === "string" && reqBody.reason
        ? reqBody.reason
        : "operator engaged emergency stop";
    // The backend's EngageRequest defaults to "api"; the UI always passes
    // "ui" explicitly, so an absent source here means a script, not a finger.
    const source =
      reqBody.source === "ui" || reqBody.source === "api" || reqBody.source === "watchdog"
        ? reqBody.source
        : "api";
    if (state.estop.latched) {
      // Always 200, never 409: an emergency stop that argues is broken.
      return json(200, estopStatus(state.estop, false));
    }
    state.estop.latched = true;
    state.estop.reason = reason;
    state.estop.source = source;
    state.estop.engaged_at = now;
    state.estop.freeze_pose = { ...state.positions };
    state.mode = "estop";
    // The real control loop calls executor.abort() the moment it sees the
    // latch: a frozen run is over, not paused. Abort at engage — not at
    // clear — so the UI stops offering "继续" for a wait that can never
    // resume while the arm is pinned. The reason string is the control
    // loop's own, so an operator sees the same words on both sides.
    if (state.playback && !state.playback.finished) {
      state.playback.phase = "aborted";
      state.playback.finished = true;
      state.playback.error = "emergency stop engaged";
      state.playback.approaching = false;
    }
    return json(200, estopStatus(state.estop, true));
  }
  if (pathname === "/api/estop/clear" && method === "POST") {
    if (!state.estop.latched) {
      return json(200, estopStatus(state.estop, false));
    }
    state.estop.latched = false;
    state.estop.reason = null;
    state.estop.source = null;
    state.estop.engaged_at = null;
    state.estop.freeze_pose = null;
    // The backend's clear hands the arm to the operator in zero-gravity drag
    // teaching (locked until a hand moves it) — a rigidly held arm right
    // after a stop is one nobody can reposition. The run was already aborted
    // at engage (see above) — nothing resumes.
    if (state.mode === "estop") state.mode = "teach";
    state.goto = null;
    return json(200, estopStatus(state.estop, true));
  }

  // ── control state ─────────────────────────────────────────────────────────
  if (pathname === "/api/control" && method === "GET") {
    return json(200, playbackState(state));
  }

  // ── poses ─────────────────────────────────────────────────────────────────
  if (pathname === "/api/poses" && method === "GET") {
    return json(200, state.poses);
  }
  if (pathname === "/api/poses" && method === "POST") {
    const name = typeof reqBody.name === "string" ? reqBody.name.trim() : "";
    if (!name) return badRequest("name must be at least 1 character");
    if (typeof reqBody.joints !== "object" || reqBody.joints === null) {
      return badRequest("a pose needs joint angles");
    }
    const pose: MockPose = {
      id: newId(),
      name,
      joints: reqBody.joints as Record<string, number>,
      created_at: now,
      updated_at: now,
    };
    state.poses.push(pose);
    return json(201, pose);
  }
  if (pathname === "/api/poses/capture" && method === "POST") {
    const name = typeof reqBody.name === "string" ? reqBody.name.trim() : "";
    if (!name) return badRequest("name must be at least 1 character");
    const pose: MockPose = {
      id: newId(),
      name,
      joints: { ...state.positions },
      created_at: now,
      updated_at: now,
    };
    state.poses.push(pose);
    return json(201, pose);
  }

  const poseMatch = /^\/api\/poses\/([^/]+)$/.exec(pathname);
  if (poseMatch) {
    const pose = findPose(state, decodeSegment(poseMatch[1]));
    if (!pose) return notFound(`no pose '${decodeSegment(poseMatch[1])}'`);
    if (method === "PATCH") {
      if (reqBody.name !== undefined) {
        const name = typeof reqBody.name === "string" ? reqBody.name.trim() : "";
        if (!name) return badRequest("name must be at least 1 character");
        pose.name = name;
      }
      if (reqBody.joints !== undefined) {
        if (typeof reqBody.joints !== "object" || reqBody.joints === null) {
          return badRequest("joints must be an object");
        }
        pose.joints = reqBody.joints as Record<string, number>;
      }
      pose.updated_at = now;
      return json(200, pose);
    }
    if (method === "DELETE") {
      // Directly executes: telling the operator what this pose feeds first is
      // the UI's job (it asked GET links before offering the button).
      state.poses = state.poses.filter((p) => p.id !== pose.id);
      return { status: 204 };
    }
  }

  const poseLinksMatch = /^\/api\/poses\/([^/]+)\/links$/.exec(pathname);
  if (poseLinksMatch && method === "GET") {
    const pose = findPose(state, decodeSegment(poseLinksMatch[1]));
    if (!pose) return notFound(`no pose '${decodeSegment(poseLinksMatch[1])}'`);
    const links = state.sequences
      .map((sequence) => ({
        sequence_id: sequence.id,
        sequence_name: sequence.name,
        block_count: holdsOf(sequence.blocks).filter((h) => h.pose_id === pose.id).length,
      }))
      .filter((link) => link.block_count > 0);
    return json(200, { pose_id: pose.id, count: links.length, links });
  }

  const poseGotoMatch = /^\/api\/poses\/([^/]+)\/goto$/.exec(pathname);
  if (poseGotoMatch && method === "POST") {
    // Check order mirrors the backend: the motion gate (estop) runs as a
    // dependency before the endpoint body ever loads the pose.
    if (state.estop.latched) return estopConflict(state);
    const pose = findPose(state, decodeSegment(poseGotoMatch[1]));
    if (!pose) return notFound(`no pose '${decodeSegment(poseGotoMatch[1])}'`);
    if (state.mode === "playback") return conflict("a sequence is already executing");
    if (state.mode === "teach") return conflict("cannot move while teaching");
    state.mode = "playback";
    state.goto = {
      pose_id: pose.id,
      name: pose.name,
      from: { ...state.positions },
      to: { ...pose.joints },
      duration_s: 2,
    };
    state.playback_source = typeof reqBody.source === "string" && reqBody.source ? reqBody.source : "ui";
    state.playback = {
      sequence_id: pose.id,
      sequence_name: `位姿 · ${pose.name}`,
      block_index: 0,
      block_total: 1,
      phase: "transition",
      t_in_block: 0,
      error: null,
      finished: false,
      approaching: false,
    };
    return json(200, playbackState(state));
  }

  // ── sequences ─────────────────────────────────────────────────────────────
  if (pathname === "/api/sequences" && method === "GET") {
    return json(200, state.sequences.map(toSummary));
  }
  if (pathname === "/api/sequences" && method === "POST") {
    const name = typeof reqBody.name === "string" ? reqBody.name.trim() : "";
    if (!name) return badRequest("name must be at least 1 character");
    const sequence: MockSequence = {
      schema_version: 2,
      id: newId(),
      name,
      created_at: now,
      updated_at: now,
      blocks: [],
    };
    state.sequences.push(sequence);
    return json(201, sequence);
  }

  const seqMatch = /^\/api\/sequences\/([^/]+)$/.exec(pathname);
  if (seqMatch) {
    const sequence = findSequence(state, decodeSegment(seqMatch[1]));
    if (!sequence) return notFound(`no sequence '${decodeSegment(seqMatch[1])}'`);
    if (method === "GET") return json(200, sequence);
    if (method === "PATCH") {
      if (reqBody.blocks !== undefined) {
        // The executor is consuming this structure block by block — changing
        // it under a live run is the lockout the timeline overlay enforces.
        if (isExecuting(state, sequence.id)) {
          return conflict("sequence is executing; stop it before editing");
        }
        if (!Array.isArray(reqBody.blocks)) return badRequest("blocks must be an array");
        // Write-side normalization: transitions are automatic and undeletable,
        // so they are rebuilt here rather than trusted from the client.
        sequence.blocks = normalize(reqBody.blocks as Block[]);
      }
      if (reqBody.name !== undefined) {
        const name = typeof reqBody.name === "string" ? reqBody.name.trim() : "";
        if (!name) return badRequest("name must be at least 1 character");
        sequence.name = name;
      }
      sequence.updated_at = now;
      return json(200, sequence);
    }
    if (method === "DELETE") {
      if (isExecuting(state, sequence.id)) {
        return conflict("sequence is executing; stop it before deleting");
      }
      state.sequences = state.sequences.filter((s) => s.id !== sequence.id);
      return { status: 204 };
    }
  }

  const executeMatch = /^\/api\/sequences\/([^/]+)\/execute$/.exec(pathname);
  if (executeMatch && method === "POST") {
    if (state.estop.latched) return estopConflict(state);
    const sequence = findSequence(state, decodeSegment(executeMatch[1]));
    if (!sequence) return notFound(`no sequence '${decodeSegment(executeMatch[1])}'`);
    if (sequence.blocks.length === 0) return badRequest("sequence has no blocks");
    if (state.mode === "playback") return conflict("a sequence is already executing");
    if (state.mode === "teach") return conflict("cannot execute while teaching");
    state.mode = "playback";
    state.goto = null;
    state.playback_source = typeof reqBody.source === "string" && reqBody.source ? reqBody.source : "ui";
    state.playback = {
      sequence_id: sequence.id,
      sequence_name: sequence.name,
      block_index: 0,
      block_total: sequence.blocks.length,
      phase: sequence.blocks[0].type,
      t_in_block: 0,
      error: null,
      finished: false,
      approaching: sequence.blocks[0].type === "hold",
    };
    return json(200, playbackState(state));
  }

  // ── execution control ─────────────────────────────────────────────────────
  if (pathname === "/api/execute/stop" && method === "POST") {
    if (state.mode === "playback") state.mode = "idle";
    state.playback = null;
    state.playback_source = null;
    state.goto = null;
    return json(200, playbackState(state));
  }
  if (pathname === "/api/execute/resume" && method === "POST") {
    // The gate runs before the endpoint on the real machine, so it runs first
    // here too.
    if (state.estop.latched) return estopConflict(state);
    const pb = state.playback;
    if (!pb || pb.finished || pb.phase !== "wait") {
      return conflict("no wait marker to resume from");
    }
    const sequence = findSequence(state, pb.sequence_id);
    const block = sequence?.blocks[pb.block_index];
    if (!sequence || !block) return conflict("the executing sequence is gone");
    pb.phase = block.type;
    return json(200, playbackState(state));
  }

  // ── templates ─────────────────────────────────────────────────────────────
  if (pathname === "/api/templates" && method === "GET") {
    return json(200, state.templates);
  }
  if (pathname === "/api/templates" && method === "POST") {
    const sequenceId = typeof reqBody.sequence_id === "string" ? reqBody.sequence_id : "";
    const sequence = findSequence(state, sequenceId);
    if (!sequence) return notFound(`no sequence '${sequenceId}'`);
    const holds = holdsOf(sequence.blocks);
    if (holds.length === 0) return badRequest("a sequence with no stations cannot be a template");
    // Snapshot the structure with pose slots, not joint angles: a template's
    // value is the structure, and angles taught here are wrong elsewhere.
    const slots = new Map<string, number>();
    holds.forEach((hold, i) => slots.set(hold.id, i));
    const recipe: Block[] = sequence.blocks.map((block) => {
      const copy = { ...block, id: newId(), markers: block.markers.map((m) => ({ ...m, id: newId() })) };
      if (copy.type === "hold") copy.pose_id = `slot:${(slots.get(block.id) ?? 0) + 1}`;
      return copy;
    });
    const template: MockTemplate = {
      id: newId(),
      name:
        typeof reqBody.name === "string" && reqBody.name.trim()
          ? reqBody.name.trim()
          : sequence.name,
      created_at: now,
      station_count: holds.length,
      recipe: normalize(recipe),
    };
    state.templates.push(template);
    return json(201, template);
  }

  const tplMatch = /^\/api\/templates\/([^/]+)$/.exec(pathname);
  if (tplMatch && method === "DELETE") {
    const template = findTemplate(state, decodeSegment(tplMatch[1]));
    if (!template) return notFound(`no template '${decodeSegment(tplMatch[1])}'`);
    state.templates = state.templates.filter((t) => t.id !== template.id);
    return { status: 204 };
  }

  const instantiateMatch = /^\/api\/templates\/([^/]+)\/instantiate$/.exec(pathname);
  if (instantiateMatch && method === "POST") {
    const template = findTemplate(state, decodeSegment(instantiateMatch[1]));
    if (!template) return notFound(`no template '${decodeSegment(instantiateMatch[1])}'`);
    const name = typeof reqBody.name === "string" ? reqBody.name.trim() : "";
    if (!name) return badRequest("name must be at least 1 character");
    const poseIds = reqBody.pose_ids;
    if (!Array.isArray(poseIds) || poseIds.length !== template.station_count) {
      return badRequest(`pose_ids must list ${template.station_count} poses, one per slot`);
    }
    for (const id of poseIds) {
      if (typeof id !== "string" || !findPose(state, id)) {
        return badRequest(`unknown pose '${String(id)}'`);
      }
    }
    // Copy and detach: the new sequence and the template owe each other
    // nothing from here on.
    const blocks: Block[] = template.recipe.map((block) => {
      const copy = { ...block, id: newId(), markers: block.markers.map((m) => ({ ...m, id: newId() })) };
      if (copy.type === "hold") {
        const slot = /^slot:(\d+)$/.exec(copy.pose_id);
        const index = slot ? Number(slot[1]) - 1 : -1;
        copy.pose_id = poseIds[index] as string;
      }
      return copy;
    });
    const sequence: MockSequence = {
      schema_version: 2,
      id: newId(),
      name,
      created_at: now,
      updated_at: now,
      blocks: normalize(blocks),
    };
    state.sequences.push(sequence);
    return json(201, sequence);
  }

  // ── teach ─────────────────────────────────────────────────────────────────
  if (pathname === "/api/teach" && method === "POST") {
    // Gated on the real machine: the structured 409, not a plain string.
    if (state.estop.latched) return estopConflict(state);
    if (reqBody.enabled && state.mode === "playback") {
      return conflict("cannot teach while a sequence is executing");
    }
    state.mode = reqBody.enabled ? "teach" : "idle";
    return json(200, playbackState(state));
  }

  // ── plugins ───────────────────────────────────────────────────────────────
  // Only the shutter, and always healthy: the preview has no serial port and
  // no way to install a package. The shape must match backend/api/plugins.py
  // and the field list must match ShutterProvider.fields(), because the
  // marker inspector draws itself from this and a preview that drew a
  // different form would be previewing an app that does not exist.
  if (pathname === "/api/plugins" && (method === "GET" || method === "POST")) {
    return json(200, [
      {
        id: "shutter",
        label: "快门",
        installed: true,
        available: true,
        reason: null,
        retryable: true,
        fields: [
          { key: "count", kind: "stepper", label: "次数", default: 1, min: 1, max: 10 },
          {
            key: "interval_s",
            kind: "tiers",
            label: "间隔",
            default: 1,
            values: [0.5, 1, 2, 5],
            unit: "秒",
            when: { key: "count", min: 2 },
          },
          { key: "focus_first", kind: "switch", label: "先对焦", default: true },
        ],
      },
    ]);
  }
  if (pathname === "/api/plugins/probe" && method === "POST") {
    return handleApi(state, "GET", "/api/plugins", query, body);
  }

  // Two links, reported separately, exactly as the backend does: `connected` is
  // the USB cable to the board, `camera` is the BLE link on to the camera. The
  // preview starts paired so the ordinary flow is one step, and pairing is
  // still a real transition here so the setup path can be walked without a
  // board — the same reason SimShutter has a camera at all.
  if (pathname === "/api/shutter/test" && method === "POST") {
    const shoot = query.get("shoot") === "true";
    return json(200, {
      ok: state.camera,
      connected: true,
      camera: state.camera,
      fired: shoot && state.camera,
      firmware_version: "esp32-mock-1.0.0",
      error: state.camera ? null : "board is reachable but no camera is paired",
    });
  }

  if (pathname === "/api/shutter/pair" && method === "POST") {
    if (state.mode === "playback") {
      return json(409, { detail: "cannot pair the camera while a sequence is executing" });
    }
    state.camera = true;
    return json(200, {
      ok: true,
      connected: true,
      camera: true,
      fired: false,
      firmware_version: "esp32-mock-1.0.0",
      error: null,
    });
  }

  // Smartphone-mode pairing: same wire shape as /pair, same refusal while a
  // run is in flight. The preview's imaginary board succeeds either way.
  if (pathname === "/api/shutter/pair_smart" && method === "POST") {
    if (state.mode === "playback") {
      return json(409, { detail: "cannot pair the camera while a sequence is executing" });
    }
    state.camera = true;
    return json(200, {
      ok: true,
      connected: true,
      camera: true,
      fired: false,
      firmware_version: "esp32-mock-1.0.0",
      error: null,
    });
  }

  // ── tuning ────────────────────────────────────────────────────────────────
  const TUNING_SECTIONS = ["payload", "float", "floatlock", "settle", "approach"] as const;

  /**
   * Recursive merge for the PUT patch: plain objects merge key by key,
   * anything else (numbers, null, the com tuple) replaces. Shallow merging
   * would drop `camera.mass` when a patch only carries `camera.com`.
   */
  function deepMerge(
    target: Record<string, unknown>,
    patch: Record<string, unknown>,
  ): Record<string, unknown> {
    const out = { ...target };
    for (const [k, v] of Object.entries(patch)) {
      const t = out[k];
      out[k] = isPlainObject(t) && isPlainObject(v) ? deepMerge(t, v) : v;
    }
    return out;
  }

  function tuningResponse(state: MockState): Record<string, unknown> {
    const current = state.tuning_current as Record<string, unknown>;
    const saved = state.tuning_saved as Record<string, unknown>;
    const dirty = TUNING_SECTIONS.filter(
      (s) => JSON.stringify(current[s]) !== JSON.stringify(saved[s]),
    );
    return {
      current,
      saved,
      dirty,
      gripper_motor: true,
      payload_options: ["gripper"],
    };
  }

  if (pathname === "/api/config/tuning" && method === "GET") {
    return json(200, tuningResponse(state));
  }
  if (pathname === "/api/config/tuning" && method === "PUT") {
    const patch = reqBody as Record<string, unknown>;
    const current = state.tuning_current as Record<string, unknown>;
    for (const key of TUNING_SECTIONS) {
      if (!(key in patch)) continue;
      const pv = patch[key];
      if (isPlainObject(pv) && isPlainObject(current[key])) {
        current[key] = deepMerge(current[key] as Record<string, unknown>, pv);
      } else {
        current[key] = pv;
      }
    }
    return json(200, tuningResponse(state));
  }
  if (pathname === "/api/config/tuning/save" && method === "POST") {
    state.tuning_saved = structuredClone(state.tuning_current);
    return json(200, tuningResponse(state));
  }
  if (pathname === "/api/config/tuning/reset" && method === "POST") {
    state.tuning_current = structuredClone(state.tuning_saved);
    return json(200, tuningResponse(state));
  }

  // ── logs ──────────────────────────────────────────────────────────────────
  if (pathname === "/api/logs" && method === "GET") {
    const stamp = new Date().toISOString().slice(0, 19).replace("T", " ");
    return json(200, {
      available: true,
      note: null,
      lines: [
        `${stamp} mock systemd[1]: Started rebot-copilot-camera.service.`,
        `${stamp} mock python[1234]: control loop started at 100.00 Hz`,
        `${stamp} mock python[1234]: kinematics ready: 8 collision pairs`,
        `${stamp} mock python[1234]: shutter link: esp32-mock (firmware esp32-mock-1.0.0)`,
      ],
    });
  }

  return notFound(`no mock route: ${method} ${pathname}`);
}
