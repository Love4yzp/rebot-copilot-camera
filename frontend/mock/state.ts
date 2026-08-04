/**
 * In-memory state for the no-backend preview (`npm run dev:mock`).
 *
 * The dev-server mock stands in for the FastAPI backend: it holds the same
 * data the backend would (routine CRUD, estop latch, control mode, joint
 * positions) so the React UI can be exercised end to end without the arm
 * service running. Shapes mirror `frontend/src/types.ts` and
 * `backend/routines/models.py` — keep them aligned by hand; there is no
 * generated client.
 */

export const JOINTS = ["joint1", "joint2", "joint3", "joint4", "joint5", "joint6", "gripper"];

export type MockMode = "idle" | "teach" | "playback" | "estop";
export type FailurePolicy = "abort" | "skip" | "retry";

export interface MockActionBase {
  timeout_s: number;
  on_failure: FailurePolicy;
  retries: number;
}

export interface MockShutterAction extends MockActionBase {
  type: "shutter";
  focus_first: boolean;
  count: number;
  interval_s: number;
}

export interface MockSleepAction extends MockActionBase {
  type: "sleep";
  duration_s: number;
}

export type MockAction = MockShutterAction | MockSleepAction;

export interface MockWaypoint {
  id: string;
  joints: Record<string, number>;
  duration_s: number;
  settle_ms: number;
  actions: MockAction[];
  note: string;
}

export interface MockRoutine {
  schema_version: number;
  id: string;
  name: string;
  created_at: number;
  updated_at: number;
  waypoints: MockWaypoint[];
}

export interface MockPlayback {
  phase: "idle" | "moving" | "settling" | "acting" | "done" | "aborted";
  waypoint_index: number;
  waypoint_total: number;
  action_index: number | null;
  action_total: number;
  routine_id: string | null;
  routine_name: string | null;
  error: string | null;
  finished: boolean;
}

export interface MockEstop {
  latched: boolean;
  reason: string | null;
  source: "ui" | "api" | "watchdog" | null;
  engaged_at: number | null;
  freeze_pose: Record<string, number> | null;
}

export interface MockState {
  started_at: number;
  positions: Record<string, number>;
  velocities: Record<string, number>;
  mode: MockMode;
  estop: MockEstop;
  playback: MockPlayback | null;
  routines: MockRoutine[];
  /**
   * The single waypoint a `goto` is executing, standing in for the backend's
   * one-waypoint ephemeral routine (which is never stored). Null during idle,
   * teach, and full-routine playback.
   */
  gotoWaypoint: MockWaypoint | null;
  /**
   * The BLE half of the shutter chain: whether the imaginary board holds the
   * camera. Separate from the USB link, which the preview always has, because
   * they fail separately on the real machine and only this one decides whether
   * a frame is taken.
   */
  camera: boolean;
}

/** Pseudo-random 12-hex id, same length as the backend's uuid4().hex[:12]. */
export function newId(): string {
  return Math.random().toString(16).slice(2, 14).padEnd(12, "0");
}

export function touch(routine: MockRoutine, now: number): void {
  routine.updated_at = now;
}

export function shutterAction(overrides: Partial<MockShutterAction> = {}): MockShutterAction {
  return {
    type: "shutter",
    focus_first: true,
    count: 1,
    interval_s: 0,
    timeout_s: 5,
    on_failure: "abort",
    retries: 0,
    ...overrides,
  };
}

export function sleepAction(duration_s: number): MockSleepAction {
  return { type: "sleep", duration_s, timeout_s: duration_s + 1, on_failure: "abort", retries: 0 };
}

/** A plausible resting pose for every joint, so the 3D view starts relaxed. */
function zeroPose(): Record<string, number> {
  return Object.fromEntries(JOINTS.map((j) => [j, 0]));
}

export function toSummary(routine: MockRoutine): {
  id: string;
  name: string;
  created_at: number;
  updated_at: number;
  waypoint_count: number;
  shutter_count: number;
} {
  return {
    id: routine.id,
    name: routine.name,
    created_at: routine.created_at,
    updated_at: routine.updated_at,
    waypoint_count: routine.waypoints.length,
    shutter_count: routine.waypoints.reduce(
      (n, w) => n + w.actions.filter((a) => a.type === "shutter").length,
      0,
    ),
  };
}

function seedWaypoint(partial: Omit<MockWaypoint, "id">): MockWaypoint {
  return { id: newId(), ...partial };
}

function seedRoutines(): MockRoutine[] {
  const now = Date.now() / 1000;
  return [
    {
      schema_version: 1,
      id: "mockdemo000001",
      name: "示例拍摄 (3 点)",
      created_at: now - 3600,
      updated_at: now - 600,
      waypoints: [
        seedWaypoint({
          joints: { ...zeroPose(), joint2: 0.4, joint3: -0.3, gripper: 0.02 },
          duration_s: 2.0,
          settle_ms: 300,
          actions: [],
          note: "第一视角",
        }),
        seedWaypoint({
          joints: { ...zeroPose(), joint1: 0.7, joint2: 0.5, joint3: -0.5, joint6: 0.25, gripper: 0.02 },
          duration_s: 2.5,
          settle_ms: 400,
          actions: [shutterAction(), sleepAction(0.5)],
          note: "侧面特写",
        }),
        seedWaypoint({
          joints: { ...zeroPose(), joint1: 1.2, joint2: 0.3, joint3: -0.2, joint6: 0.5, gripper: 0.02 },
          duration_s: 3.0,
          settle_ms: 300,
          actions: [shutterAction()],
          note: "",
        }),
      ],
    },
    {
      schema_version: 1,
      id: "mockempty00002",
      name: "空序列 (演示校验)",
      created_at: now - 1200,
      updated_at: now - 1200,
      waypoints: [],
    },
  ];
}

export function createState(): MockState {
  return {
    started_at: Date.now() / 1000,
    positions: zeroPose(),
    velocities: zeroPose(),
    mode: "idle",
    // Paired, so the ordinary preview flow is one step. Set false to walk the
    // setup path an operator meets on a machine they have never used.
    camera: true,
    estop: { latched: false, reason: null, source: null, engaged_at: null, freeze_pose: null },
    playback: null,
    routines: seedRoutines(),
    gotoWaypoint: null,
  };
}
