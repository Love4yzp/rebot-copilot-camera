export type Mode = "idle" | "teach" | "playback" | "estop";

export type FailurePolicy = "abort" | "skip" | "retry";

export interface ShutterAction {
  type: "shutter";
  focus_first: boolean;
  timeout_s: number;
  on_failure: FailurePolicy;
  retries: number;
  /** How many times the trigger fires per visit (1–50). */
  count: number;
  /** Pause between repeated triggers, in seconds (0–60). */
  interval_s: number;
}

export interface SleepAction {
  type: "sleep";
  duration_s: number;
  timeout_s: number;
  on_failure: FailurePolicy;
  retries: number;
}

export type Action = ShutterAction | SleepAction;

export interface Waypoint {
  id: string;
  joints: Record<string, number>;
  duration_s: number;
  settle_ms: number;
  actions: Action[];
  note: string;
}

export interface Routine {
  schema_version: number;
  id: string;
  name: string;
  created_at: number;
  updated_at: number;
  waypoints: Waypoint[];
}

export interface RoutineSummary {
  id: string;
  name: string;
  created_at: number;
  updated_at: number;
  waypoint_count: number;
  shutter_count: number;
}

export interface EstopState {
  latched: boolean;
  reason: string | null;
  source: "ui" | "api" | "watchdog" | null;
  engaged_at?: number | null;
  freeze_pose?: Record<string, number> | null;
}

export interface PlaybackProgress {
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

/** What the motion endpoints (play / goto / teach / stop) return. */
export interface PlaybackState {
  mode: string;
  playing: boolean;
  teaching: boolean;
  rate_hz: number;
  playback: PlaybackProgress | null;
}

/** One control-loop tick, as broadcast over /ws. */
export interface ControlState {
  t: number;
  positions: Record<string, number>;
  velocities: Record<string, number>;
  rate_hz: number;
  mode: Mode;
  estop: EstopState;
  playback: PlaybackProgress | null;
}

export type SocketMessage =
  | { type: "state"; data: ControlState }
  | { type: "playback"; data: PlaybackProgress };
