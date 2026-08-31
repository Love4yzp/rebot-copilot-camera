export type Mode = "idle" | "teach" | "playback" | "estop" | "rest" | "safelock";

/** App deployment mode: sim (simulator/frontend-only) or prod (production). */
export type AppMode = "sim" | "prod";

/** Shape of `GET /api/health`. */
export interface HealthResponse {
  status: string;
  version: string;
  uptime_s: number;
  mode: AppMode;
  estop: EstopState;
  shutter: { simulated: boolean };
  arm: {
    simulated: boolean;
    urdf: string;
    end_effector_frame: string;
    joints: string[];
  };
}

/**
 * One control in the marker inspector, as described by the provider.
 *
 * Only three kinds, and that is the contract: they are the three this app
 * already implements, and those have been through the touch-target, focus and
 * reduced-motion pass. A plugin that shipped its own markup would ship its own
 * colours, and here colour is a status channel rather than decoration.
 */
export interface ProviderField {
  key: string;
  kind: "switch" | "stepper" | "tiers";
  label: string;
  default: unknown;
  min?: number;
  max?: number;
  values?: number[];
  unit?: string;
  /** Show only once another field reaches a threshold: `{key, min}`. */
  when?: { key: string; min: number };
}

/** What `GET /api/plugins` reports about one action provider. */
export interface ProviderInfo {
  id: string;
  label: string;
  /**
   * Whether the host actually holds this provider. False for a package that
   * failed to load or claimed an id that was taken: it stays on the list so it
   * does not read as the operator's own mistake, but nothing can be configured
   * against it — the host has no params model to check what would be stored.
   */
  installed: boolean;
  available: boolean;
  /** Why it is unavailable. Shown verbatim — never hide a broken provider. */
  reason: string | null;
  retryable: boolean;
  fields: ProviderField[];
}

// ── timeline model (schema_version 2) ──────────────────────────────────────
// These shapes are the contract the v2 backend implements against: the mock
// serves them today and the FastAPI side will serve the same documents.

/** A named arm pose in the library. Hold blocks link to it by id. */
export interface Pose {
  id: string;
  name: string;
  joints: Record<string, number>;
  created_at: number;
  updated_at: number;
}

/**
 * An action pinned inside its parent block, at a time position inside it.
 * Inside a hold `at` is a second offset (0..duration_s); inside a transition
 * it is a proportion (0..1) — splitting a transition to say "midway" would
 * invent a pose nobody taught.
 */
export interface EventMarker {
  id: string;
  /** "wait" is built in; anything else is a provider id (e.g. "shutter"). */
  kind: "wait" | string;
  /** Provider params (shutter: count/interval_s/focus_first); wait has none. */
  params: Record<string, unknown>;
  at: number;
  /**
   * Estimated execution time in seconds, for the translucent span display.
   * Instant triggers ≈ 0.3; a wait marker is open-ended and carries 0.
   */
  estimate_s: number;
}

export interface HoldBlock {
  type: "hold";
  id: string;
  /** Link, not a copy: the joints live in the library pose. */
  pose_id: string;
  duration_s: number;
  markers: EventMarker[];
}

export type Easing = "linear" | "ease_in" | "ease_out" | "ease_in_out";

export interface TransitionBlock {
  type: "transition";
  id: string;
  duration_s: number;
  easing: Easing;
  markers: EventMarker[];
}

export type Block = HoldBlock | TransitionBlock;

export interface Sequence {
  schema_version: 2;
  id: string;
  name: string;
  created_at: number;
  updated_at: number;
  blocks: Block[];
}

export interface SequenceSummary {
  id: string;
  name: string;
  updated_at: number;
  /** Number of hold blocks (stations). */
  station_count: number;
  /** Sum of commanded durations — the plan-ruler length. */
  duration_s: number;
}

/**
 * A structural recipe: blocks with each hold's pose_id replaced by a slot
 * placeholder ("slot:1".."slot:N"). No joint angles — a template's value is
 * the structure, and angles taught in one studio are wrong in another.
 */
export interface SeqTemplate {
  id: string;
  name: string;
  created_at: number;
  station_count: number;
  recipe: Block[];
}

/** Which sequences link a pose, reported before delete/overwrite. */
export interface PoseLink {
  sequence_id: string;
  sequence_name: string;
  /** How many hold blocks in that sequence reference the pose. */
  block_count: number;
}

export interface PoseLinks {
  pose_id: string;
  count: number;
  links: PoseLink[];
}

// ── live control ────────────────────────────────────────────────────────────

export interface EstopState {
  latched: boolean;
  reason: string | null;
  source: "ui" | "api" | "watchdog" | null;
  engaged_at?: number | null;
  freeze_pose?: Record<string, number> | null;
}

/**
 * Block-walking playback progress, as broadcast over /ws by the mock today
 * and by the v2 backend later. `block_index` sits one past the last block
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

/**
 * What the shutter self-test and the pairing endpoint report.
 *
 * `connected` is the USB link to the board; `camera` is the BLE link from the
 * board to the camera. They fail separately, and only the second one predicts
 * whether a frame will actually be taken — a board answering perfectly while
 * nothing is paired is the case this pair of fields exists to make visible.
 */
export interface ShutterResult {
  ok: boolean;
  connected: boolean;
  camera: boolean | null;
  fired: boolean;
  firmware_version: string | null;
  error: string | null;
}

// ── tuning panel ────────────────────────────────────────────────────────────

export interface CameraPayload {
  mass: number | null;
  com: [number, number, number];
}

export interface PayloadTuning {
  profile: string;
  camera: CameraPayload;
}

export interface FloatTuning {
  kp: number;
  kd: number;
}

export interface FloatLockTuning {
  linear_threshold: number;
  angular_threshold: number;
  release_factor: number;
  lock_factor: number;
  min_still_s: number;
}

export interface SettleTuning {
  drift_rad: number;
  min_s: number;
}

export interface ApproachTuning {
  first_max_speed: number;
}

/** Per-joint gravity feedforward correction: tau = scale * g_model + bias.
 * Missing joints are identity (1.0 / 0.0). */
export interface GravityTuning {
  scale: Record<string, number>;
  bias: Record<string, number>;
}

export interface TuningConfig {
  payload: PayloadTuning;
  float: FloatTuning;
  floatlock: FloatLockTuning;
  settle: SettleTuning;
  approach: ApproachTuning;
  gravity: GravityTuning;
}

export interface TuningState {
  current: TuningConfig;
  saved: TuningConfig;
  dirty: string[];
  gripper_motor: boolean;
  payload_options: string[];
}
