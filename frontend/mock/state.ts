/**
 * In-memory state for the no-backend preview (`npm run dev:mock`).
 *
 * The dev-server mock stands in for the v2 FastAPI backend: it holds the same
 * data the backend would (pose library, block/marker sequences, templates,
 * estop latch, control mode, joint positions) so the React UI can be
 * exercised end to end without the arm service running. Shapes mirror
 * `frontend/src/types.ts` — keep them aligned by hand; there is no generated
 * client. The REST surface this state backs is the contract the v2 backend
 * implements against.
 */

import type { Block, Easing, EventMarker, Sequence } from "../src/types";
import { makeHold, makeMarker, makeTransition, newId, normalize } from "../src/timeline/model";

export { newId };

export const JOINTS = ["joint1", "joint2", "joint3", "joint4", "joint5", "joint6", "gripper"];

export type MockMode = "idle" | "teach" | "playback" | "estop";

export interface MockPose {
  id: string;
  name: string;
  joints: Record<string, number>;
  created_at: number;
  updated_at: number;
}

export type MockSequence = Sequence;

export interface MockTemplate {
  id: string;
  name: string;
  created_at: number;
  station_count: number;
  recipe: Block[];
}

export interface MockSeqPlayback {
  sequence_id: string;
  sequence_name: string;
  block_index: number;
  block_total: number;
  phase: "hold" | "transition" | "wait" | "done" | "aborted";
  t_in_block: number;
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

/**
 * A single-pose goto in flight: an eased lerp from wherever the arm stood to
 * the target pose, standing in for the backend's ephemeral move. Null during
 * idle, teach, and sequence execution.
 */
export interface MockGoto {
  pose_id: string;
  name: string;
  from: Record<string, number>;
  to: Record<string, number>;
  duration_s: number;
}

export interface MockState {
  started_at: number;
  positions: Record<string, number>;
  velocities: Record<string, number>;
  mode: MockMode;
  estop: MockEstop;
  playback: MockSeqPlayback | null;
  /**
   * Who asked for the running sequence (PlaybackState.source). Retained while
   * the run's record is retained — an aborted run still reports who started it;
   * an explicit stop clears both. Mirrors the backend's PlaybackState.
   */
  playback_source: string | null;
  poses: MockPose[];
  sequences: MockSequence[];
  templates: MockTemplate[];
  goto: MockGoto | null;
  /**
   * The BLE half of the shutter chain: whether the imaginary board holds the
   * camera. Separate from the USB link, which the preview always has, because
   * they fail separately on the real machine and only this one decides whether
   * a frame is taken.
   */
  camera: boolean;
}

/** A plausible resting pose for every joint, so the 3D view starts relaxed. */
export function zeroPose(): Record<string, number> {
  return Object.fromEntries(JOINTS.map((j) => [j, 0]));
}

function shutterMarker(at: number): EventMarker {
  return makeMarker("shutter", at, { count: 1, interval_s: 1, focus_first: true }, 0.3);
}

function holdWith(poseId: string, duration_s: number, markers: EventMarker[] = []): Block {
  return { ...makeHold(poseId, duration_s), markers };
}

function transWith(
  duration_s: number,
  easing: Easing,
  markers: EventMarker[] = [],
): Block {
  return { ...makeTransition(duration_s, easing), markers };
}

function seedPoses(now: number): MockPose[] {
  const pose = (name: string, joints: Partial<Record<string, number>>, age_s: number): MockPose => ({
    id: newId(),
    name,
    joints: { ...zeroPose(), ...joints } as Record<string, number>,
    created_at: now - age_s,
    updated_at: now - age_s,
  });
  return [
    pose("正面", { joint2: 0.35, joint3: -0.3, joint5: 0.1, gripper: 0.02 }, 7200),
    pose("右45°", { joint1: 0.8, joint2: 0.45, joint3: -0.5, joint6: 0.25, gripper: 0.02 }, 7000),
    pose("侧面", { joint1: 1.4, joint2: 0.3, joint3: -0.25, joint5: -0.2, joint6: 0.5, gripper: 0.02 }, 6800),
    pose("俯拍", { joint1: 0.15, joint2: 0.9, joint3: -0.9, joint4: 0.3, joint5: 0.4, gripper: 0.02 }, 6600),
  ];
}

/**
 * The demo sequence from `docs/TIMELINE.md`: 20.5 s on the plan ruler, with a
 * wait marker at t=8 s that suspends both preview and execution until the
 * operator taps 继续.
 */
function seedDemoSequence(poses: MockPose[], now: number): MockSequence {
  const [front, right, side, top] = poses;
  const blocks = normalize([
    holdWith(front.id, 3),
    transWith(2, "ease_in_out"),
    holdWith(right.id, 5, [shutterMarker(2), makeMarker("wait", 3, {}, 0), shutterMarker(4)]),
    transWith(1.5, "linear", [
      // Record start/stop bracketing the move: the "duration" is carried by
      // the start marker's estimate, displayed as a translucent span.
      makeMarker("record_start", 0, {}, 1.5),
      makeMarker("record_stop", 1, {}, 0.3),
    ]),
    holdWith(side.id, 3),
    transWith(2, "ease_in_out", [makeMarker("fill_light", 0.4, {}, 0.3)]),
    holdWith(top.id, 4),
  ]);
  return {
    schema_version: 2,
    id: "mockdemo000001",
    name: "四方位拍摄",
    created_at: now - 3600,
    updated_at: now - 600,
    blocks,
  };
}

/** The four-station recipe: 3 s + one shutter per station, eased transitions. */
function seedTemplate(now: number): MockTemplate {
  const recipe: Block[] = [];
  for (let slot = 1; slot <= 4; slot++) {
    if (slot > 1) recipe.push(transWith(2, "ease_in_out"));
    recipe.push(holdWith(`slot:${slot}`, 3, [shutterMarker(2)]));
  }
  return {
    id: newId(),
    name: "四方位",
    created_at: now - 3000,
    station_count: 4,
    recipe: normalize(recipe),
  };
}

export function createState(options: { seed?: boolean } = {}): MockState {
  const seed = options.seed ?? true;
  const now = Date.now() / 1000;
  const poses = seed ? seedPoses(now) : [];
  return {
    started_at: now,
    positions: zeroPose(),
    velocities: zeroPose(),
    mode: "idle",
    // Paired, so the ordinary preview flow is one step. Set false to walk the
    // setup path an operator meets on a machine they have never used.
    camera: true,
    estop: { latched: false, reason: null, source: null, engaged_at: null, freeze_pose: null },
    playback: null,
    playback_source: null,
    poses,
    sequences: seed
      ? [
          seedDemoSequence(poses, now),
          {
            schema_version: 2,
            id: "mockempty00002",
            name: "空序列",
            created_at: now - 1200,
            updated_at: now - 1200,
            blocks: [],
          },
        ]
      : [],
    templates: seed ? [seedTemplate(now)] : [],
    goto: null,
  };
}
