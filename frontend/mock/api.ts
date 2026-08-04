/**
 * Mock REST handlers for the no-backend preview.
 *
 * Pure functions over `MockState`, mirroring the FastAPI endpoints the UI
 * calls (`backend/api/{estop,routines,control}.py`). Behavior follows the
 * backend where the UI depends on it: 201 on create, 204 on delete, 400 on an
 * empty-routine play, 409 on motion commands while the estop is latched,
 * re-engaging a latched estop reports `changed: false`. Exact pydantic
 * validation errors are not reproduced — the UI only renders their message
 * strings.
 */

import type {
  MockAction,
  MockEstop,
  MockRoutine,
  MockState,
  MockWaypoint,
} from "./state";
import { JOINTS, newId, toSummary, touch } from "./state";

export interface MockResponse {
  status: number;
  body?: unknown;
}

/** The plugin writes every response as JSON; only the status/body matter here. */
function json(status: number, body: unknown): MockResponse {
  return { status, body };
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

/** `{mode, playing, teaching, rate_hz, playback}` — the PlaybackState shape. */
function playbackState(state: MockState): Record<string, unknown> {
  return {
    mode: state.mode,
    playing: state.mode === "playback",
    teaching: state.mode === "teach",
    rate_hz: 20,
    playback: state.playback,
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

function findRoutine(state: MockState, rid: string): MockRoutine | undefined {
  return state.routines.find((r) => r.id === rid);
}

function findWaypoint(routine: MockRoutine, index: number): MockWaypoint | undefined {
  return routine.waypoints[index];
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
    const source =
      reqBody.source === "ui" || reqBody.source === "api" || reqBody.source === "watchdog"
        ? reqBody.source
        : "ui";
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
    if (state.mode === "estop") state.mode = "idle";
    return json(200, estopStatus(state.estop, true));
  }

  // ── control state ─────────────────────────────────────────────────────────
  if (pathname === "/api/control" && method === "GET") {
    return json(200, playbackState(state));
  }

  // ── routines ──────────────────────────────────────────────────────────────
  if (pathname === "/api/routines" && method === "GET") {
    return json(200, state.routines.map(toSummary));
  }
  if (pathname === "/api/routines" && method === "POST") {
    const name = typeof reqBody.name === "string" ? reqBody.name.trim() : "";
    if (!name) return badRequest("name must be at least 1 character");
    const routine: MockRoutine = {
      schema_version: 1,
      id: newId(),
      name,
      created_at: now,
      updated_at: now,
      waypoints: [],
    };
    state.routines.push(routine);
    return json(201, routine);
  }

  const ridMatch = /^\/api\/routines\/([^/]+)$/.exec(pathname);
  if (ridMatch) {
    const rid = decodeSegment(ridMatch[1]);
    const routine = findRoutine(state, rid);
    if (!routine) return notFound(`no routine '${rid}'`);

    if (method === "GET") return json(200, routine);
    if (method === "PATCH") {
      const name = typeof reqBody.name === "string" ? reqBody.name.trim() : "";
      if (!name) return badRequest("name must be at least 1 character");
      routine.name = name;
      touch(routine, now);
      return json(200, routine);
    }
    if (method === "DELETE") return { status: 204 };
  }

  // ── waypoints ─────────────────────────────────────────────────────────────
  const captureMatch = /^\/api\/routines\/([^/]+)\/waypoints\/capture$/.exec(pathname);
  if (captureMatch && method === "POST") {
    const routine = findRoutine(state, decodeSegment(captureMatch[1]));
    if (!routine) return notFound(`no routine '${decodeSegment(captureMatch[1])}'`);
    const waypoint: MockWaypoint = {
      id: newId(),
      joints: { ...state.positions },
      duration_s: typeof reqBody.duration_s === "number" ? reqBody.duration_s : 2.0,
      settle_ms: typeof reqBody.settle_ms === "number" ? reqBody.settle_ms : 300,
      actions: Array.isArray(reqBody.actions) ? (reqBody.actions as MockAction[]) : [],
      note: typeof reqBody.note === "string" ? reqBody.note : "",
    };
    const index = typeof reqBody.index === "number" ? reqBody.index : null;
    if (index === null || index === routine.waypoints.length) {
      routine.waypoints.push(waypoint);
    } else if (index >= 0 && index < routine.waypoints.length) {
      routine.waypoints.splice(index, 0, waypoint);
    } else {
      return notFound(`insert index ${index} out of range`);
    }
    touch(routine, now);
    return json(201, routine);
  }

  const reorderMatch = /^\/api\/routines\/([^/]+)\/waypoints\/reorder$/.exec(pathname);
  if (reorderMatch && method === "POST") {
    const routine = findRoutine(state, decodeSegment(reorderMatch[1]));
    if (!routine) return notFound(`no routine '${decodeSegment(reorderMatch[1])}'`);
    const order = reqBody.order;
    const isValid =
      Array.isArray(order) &&
      order.length === routine.waypoints.length &&
      [...order].sort((a, b) => (a as number) - (b as number)).every((v, i) => v === i);
    if (!isValid) {
      return badRequest(
        `order must be a permutation of 0..${routine.waypoints.length - 1}, got ${JSON.stringify(order)}`,
      );
    }
    routine.waypoints = (order as number[]).map((i) => routine.waypoints[i]);
    touch(routine, now);
    return json(200, routine);
  }

  const addMatch = /^\/api\/routines\/([^/]+)\/waypoints$/.exec(pathname);
  if (addMatch && method === "POST") {
    const routine = findRoutine(state, decodeSegment(addMatch[1]));
    if (!routine) return notFound(`no routine '${decodeSegment(addMatch[1])}'`);
    const joints = reqBody.joints;
    if (typeof joints !== "object" || joints === null) {
      return badRequest("a waypoint needs at least one joint angle");
    }
    const waypoint: MockWaypoint = {
      id: newId(),
      joints: joints as Record<string, number>,
      duration_s: typeof reqBody.duration_s === "number" ? reqBody.duration_s : 2.0,
      settle_ms: typeof reqBody.settle_ms === "number" ? reqBody.settle_ms : 300,
      actions: Array.isArray(reqBody.actions) ? (reqBody.actions as MockAction[]) : [],
      note: typeof reqBody.note === "string" ? reqBody.note : "",
    };
    const index = typeof reqBody.index === "number" ? reqBody.index : null;
    if (index === null || index === routine.waypoints.length) {
      routine.waypoints.push(waypoint);
    } else if (index >= 0 && index < routine.waypoints.length) {
      routine.waypoints.splice(index, 0, waypoint);
    } else {
      return notFound(`insert index ${index} out of range`);
    }
    touch(routine, now);
    return json(201, routine);
  }

  const waypointMatch = /^\/api\/routines\/([^/]+)\/waypoints\/(\d+)$/.exec(pathname);
  if (waypointMatch) {
    const routine = findRoutine(state, decodeSegment(waypointMatch[1]));
    if (!routine) return notFound(`no routine '${decodeSegment(waypointMatch[1])}'`);
    const index = Number(waypointMatch[2]);
    const waypoint = findWaypoint(routine, index);
    if (!waypoint) {
      return notFound(`waypoint index ${index} out of range (routine has ${routine.waypoints.length})`);
    }
    if (method === "PATCH") {
      if (reqBody.joints !== undefined) waypoint.joints = reqBody.joints as Record<string, number>;
      if (reqBody.duration_s !== undefined) waypoint.duration_s = reqBody.duration_s as number;
      if (reqBody.settle_ms !== undefined) waypoint.settle_ms = reqBody.settle_ms as number;
      if (reqBody.actions !== undefined) waypoint.actions = reqBody.actions as MockAction[];
      if (reqBody.note !== undefined) waypoint.note = reqBody.note as string;
      touch(routine, now);
      return json(200, routine);
    }
    if (method === "DELETE") {
      routine.waypoints.splice(index, 1);
      touch(routine, now);
      return json(200, routine);
    }
  }

  // ── playback / goto / teach / shutter ─────────────────────────────────────
  const playMatch = /^\/api\/routines\/([^/]+)\/play$/.exec(pathname);
  if (playMatch && method === "POST") {
    const routine = findRoutine(state, decodeSegment(playMatch[1]));
    if (!routine) return notFound(`no routine '${decodeSegment(playMatch[1])}'`);
    if (state.mode === "estop") return conflict("arm unavailable while emergency stop is engaged");
    if (!routine.waypoints.length) return badRequest("routine has no waypoints");
    state.mode = "playback";
    state.gotoWaypoint = null;
    state.playback = {
      phase: "moving",
      waypoint_index: 0,
      waypoint_total: routine.waypoints.length,
      action_index: null,
      action_total: routine.waypoints[0].actions.length,
      routine_id: routine.id,
      routine_name: routine.name,
      error: null,
      finished: false,
    };
    return json(200, playbackState(state));
  }
  const gotoMatch = /^\/api\/routines\/([^/]+)\/waypoints\/(\d+)\/goto$/.exec(pathname);
  if (gotoMatch && method === "POST") {
    // Check order mirrors the backend: the motion gate (estop) runs as a
    // dependency before the endpoint body ever loads the routine.
    if (state.estop.latched) {
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
    const rid = decodeSegment(gotoMatch[1]);
    const routine = findRoutine(state, rid);
    if (!routine) return notFound(`no routine '${rid}'`);
    const index = Number(gotoMatch[2]);
    const waypoint = findWaypoint(routine, index);
    if (!waypoint) {
      return notFound(`waypoint index ${index} out of range (routine has ${routine.waypoints.length})`);
    }
    if (state.mode === "playback") return conflict("a routine is already playing");
    if (state.mode === "teach") return conflict("cannot play while teaching");
    // A one-waypoint ephemeral run: gotoWaypoint stands in for the ephemeral
    // routine, the broadcast reports waypoint_total=1, and the sim moves,
    // settles, runs the actions, then holds (mode back to idle).
    const label = waypoint.note || `#${index + 1}`;
    state.mode = "playback";
    state.gotoWaypoint = waypoint;
    state.playback = {
      phase: "moving",
      waypoint_index: 0,
      waypoint_total: 1,
      action_index: null,
      action_total: waypoint.actions.length,
      routine_id: routine.id,
      routine_name: `${routine.name} · ${label}`,
      error: null,
      finished: false,
    };
    return json(200, playbackState(state));
  }
  if (pathname === "/api/playback/stop" && method === "POST") {
    state.mode = state.mode === "playback" ? "idle" : state.mode;
    state.playback = null;
    state.gotoWaypoint = null;
    return json(200, playbackState(state));
  }
  if (pathname === "/api/teach" && method === "POST") {
    if (state.mode === "estop") return conflict("arm unavailable while emergency stop is engaged");
    state.mode = reqBody.enabled ? "teach" : "idle";
    return json(200, playbackState(state));
  }
  // ── plugins ───────────────────────────────────────────────────────────────
  // Only the shutter, and always healthy: the preview has no serial port and
  // no way to install a package. The shape must match backend/api/plugins.py
  // and the field list must match ShutterProvider.fields(), because the edit
  // sheet draws itself from this and a preview that drew a different form
  // would be previewing an app that does not exist.
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
      return json(409, { detail: "cannot pair the camera while a routine is playing" });
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
