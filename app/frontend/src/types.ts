import type { components } from "./generated/api";
type DeepRequired<T> = T extends object
  ? { [K in keyof T]-?: DeepRequired<T[K]> }
  : T;
type Wire<K extends keyof components["schemas"]> = DeepRequired<
  components["schemas"][K]
>;

export type Mode =
  | "idle"
  | "teach"
  | "playback"
  | "estop"
  | "rest"
  | "safelock";

/** App deployment mode: sim (MuJoCo physics) or prod (production). */
export type AppMode = "sim" | "prod";

/** Shape of `GET /api/health`. */
export interface HealthResponse {
  status: string;
  version: string;
  uptime_s: number;
  mode: AppMode;
  estop: EstopState;
  arm: {
    simulated: boolean;
    urdf: string;
    end_effector_frame: string;
    joints: string[];
  };
}

// ── timeline model (schema_version 3) ──────────────────────────────────────
// Data document types are generated from the backend OpenAPI schema.

/** A named arm pose in the library. Hold blocks link to it by id. */
export type Pose = Wire<"Pose">;

/**
 * An action pinned inside its parent block, at a time position inside it.
 * Inside a hold `at` is a second offset (0..duration_s); inside a transition
 * it is a proportion (0..1) — splitting a transition to say "midway" would
 * invent a pose nobody taught.
 */
export type EventMarker = Wire<"EventMarker">;

export type HoldBlock = Wire<"HoldBlock">;

export type Easing = "linear" | "ease_in" | "ease_out" | "ease_in_out";

export type TransitionBlock = Wire<"TransitionBlock">;

export type Block = HoldBlock | TransitionBlock;

export type Sequence = Wire<"Sequence">;

export type SequenceSummary = Wire<"SequenceSummary">;

/**
 * A structural recipe: blocks with each hold's pose_id replaced by a slot
 * placeholder ("slot:1".."slot:N"). No joint angles — a template's value is
 * the structure, and angles taught in one studio are wrong in another.
 */
export type SeqTemplate = Wire<"SeqTemplate">;

/** Which sequences link a pose, reported before delete/overwrite. */
export type PoseLink = Wire<"PoseLink">;

export type PoseLinks = Wire<"PoseLinks">;

// ── live control ────────────────────────────────────────────────────────────

export interface EstopState {
  latched: boolean;
  reason: string | null;
  source: "ui" | "api" | "watchdog" | null;
  engaged_at?: number | null;
  freeze_pose?: Record<string, number> | null;
}

/**
 * Block-walking playback progress broadcast over /ws. `block_index` sits one past the last block
 * once finished (the executor increments before it notices it is done) —
 * clamp before indexing.
 */
export interface SeqPlayback {
  sequence_id: string;
  sequence_name: string;
  block_index: number;
  block_total: number;
  phase: "hold" | "transition" | "wait" | "done" | "aborted";
  t_in_block: number;
  error: string | null;
  finished: boolean;
  /**
   * True while the current block is a hold and the arm is still flying toward
   * — or settling at — its pose: the hold's clock has not yet started. Once
   * the arm has arrived and held still this flips to false for the rest of
   * the block. Transition, wait, done, and aborted phases always report false.
   */
  approaching: boolean;
}

/** What the motion endpoints (execute / goto / teach / stop) return. */
export interface PlaybackState {
  mode: string;
  activity: string;
  playing: boolean;
  teaching: boolean;
  rate_hz: number;
  playback: SeqPlayback | null;
  /** Who asked for the running sequence. A label, never a permission. */
  source?: string | null;
}

/** One control-loop tick, as broadcast over /ws. */
export interface ControlState {
  t: number;
  positions: Record<string, number>;
  velocities: Record<string, number>;
  rate_hz: number;
  mode: Mode;
  /** Exclusive activity; ``mode`` is this, or ``estop`` when latched. */
  activity?: string;
  /** Rest: zero torque, the arm lying on its stops. */
  resting?: boolean;
  estop: EstopState;
  playback: SeqPlayback | null;
}

export type SocketMessage =
  | { type: "state"; data: ControlState }
  | { type: "playback"; data: SeqPlayback };

// ── tuning panel ────────────────────────────────────────────────────────────

export type CameraPayload = Wire<"CameraPayload">;

export type PayloadTuning = Wire<"PayloadTuning">;

export type FloatTuning = Wire<"FloatTuning">;

export type FloatLockTuning = Wire<"FloatLockTuning">;

export type SettleTuning = Wire<"SettleTuning">;

export type ApproachTuning = Wire<"ApproachTuning">;

/** Per-joint gravity feedforward correction: tau = scale * g_model + bias.
 * Missing joints are identity (1.0 / 0.0). */
export type GravityTuning = Wire<"GravityTuning">;

export type TuningConfig = Wire<"TuningConfig">;

export type TuningState = Wire<"TuningState">;

export type SimulationState = Wire<"SimulationState">;
