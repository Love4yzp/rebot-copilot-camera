/**
 * Timeline model: the pure logic of the block/marker world.
 *
 * Shared between the React UI (preview, timeline rendering) and the dev mock
 * (PATCH-time normalization, playback engine) — one implementation, no drift.
 * It is also the blueprint the v2 backend ports to Python, so everything here
 * is a pure function with no framework or DOM dependency.
 */

import type { Block, Easing, EventMarker, HoldBlock, TransitionBlock } from "../types";

/** Defaults for an auto-generated transition: slow and smooth beats fast. */
export const DEFAULT_TRANSITION_S = 2.0;
export const DEFAULT_EASING: Easing = "ease_in_out";

/** Minimum hold duration — shorter than this is a mis-tap, not a station. */
export const MIN_HOLD_S = 0.5;

/** Pseudo-random 12-hex id, same length as the backend's uuid4().hex[:12]. */
export function newId(): string {
  return Math.random().toString(16).slice(2, 14).padEnd(12, "0");
}

export function makeMarker(
  kind: string,
  at: number,
  params: Record<string, unknown> = {},
  estimate_s = 0.3,
): EventMarker {
  return { id: newId(), kind, params, at, estimate_s };
}

export function makeHold(pose_id: string, duration_s = 3): HoldBlock {
  return { type: "hold", id: newId(), pose_id, duration_s, markers: [] };
}

export function makeTransition(
  duration_s = DEFAULT_TRANSITION_S,
  easing: Easing = DEFAULT_EASING,
): TransitionBlock {
  return { type: "transition", id: newId(), duration_s, easing, markers: [] };
}

// ── easing ───────────────────────────────────────────────────────────────────

const clamp01 = (t: number) => Math.min(1, Math.max(0, t));

/** Cubic easing curves; `t` is the raw proportion, returns the eased one. */
export function easingFn(name: Easing, t: number): number {
  const x = clamp01(t);
  switch (name) {
    case "linear":
      return x;
    case "ease_in":
      return x * x * x;
    case "ease_out":
      return 1 - Math.pow(1 - x, 3);
    case "ease_in_out":
      return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2;
  }
}

// ── normalize ────────────────────────────────────────────────────────────────

/**
 * Rebuild a block list so the physical rules hold after any edit.
 *
 * This is how "transitions are automatic and undeletable" is implemented:
 * not as an editing restriction but as a normalization that runs after every
 * change (in the UI before PATCH, and again in the mock on write).
 *
 *   - holds keep their identity, order, duration and markers
 *   - between two adjacent holds of *different* poses there is exactly one
 *     transition — the arm must physically get there, that is not a setting
 *   - between two adjacent holds of the *same* pose there is none — that is
 *     "stop halfway and take one more frame", not a move
 *   - a recreated transition inherits the previous transition's parameters
 *     for the same pose pair when one exists (e.g. the hold between two
 *     stations was deleted and the two flanks now join directly)
 *   - transitions anywhere else (leading, trailing, orphaned) are dropped
 */
export function normalize(blocks: Block[]): Block[] {
  // Pass 1: remember every existing transition by the pose pair it links, so
  // a rebuilt transition can inherit the old one's duration/easing/markers.
  const memory = new Map<string, TransitionBlock>();
  const holds: HoldBlock[] = [];
  for (let i = 0; i < blocks.length; i++) {
    const block = blocks[i];
    if (block.type === "hold") {
      holds.push(block);
      continue;
    }
    const prev = nearestHold(blocks, i, -1);
    const next = nearestHold(blocks, i, +1);
    if (prev && next) {
      const key = pairKey(prev.pose_id, next.pose_id);
      if (!memory.has(key)) memory.set(key, block);
    }
  }

  // Pass 2: lay holds down and fill the gaps from memory.
  const out: Block[] = [];
  for (let i = 0; i < holds.length; i++) {
    out.push(holds[i]);
    if (i >= holds.length - 1) continue;
    const a = holds[i];
    const b = holds[i + 1];
    if (a.pose_id === b.pose_id) continue; // same pose adjacent: no transition
    const remembered = memory.get(pairKey(a.pose_id, b.pose_id));
    out.push(
      remembered
        ? { ...remembered, markers: remembered.markers.map((m) => ({ ...m })) }
        : makeTransition(),
    );
  }
  return out;
}

function pairKey(poseA: string, poseB: string): string {
  // Direction does not matter for inheriting duration/easing: the way back
  // is the same road.
  return poseA < poseB ? `${poseA}|${poseB}` : `${poseB}|${poseA}`;
}

function nearestHold(blocks: Block[], from: number, step: -1 | 1): HoldBlock | undefined {
  for (let i = from + step; i >= 0 && i < blocks.length; i += step) {
    if (blocks[i].type === "hold") return blocks[i] as HoldBlock;
  }
  return undefined;
}

// ── time ─────────────────────────────────────────────────────────────────────

/**
 * The plan-ruler length: the sum of *commanded* durations. Markers add
 * nothing — their durations are estimates, and a wait marker is open-ended,
 * so the UI always labels this number 预估.
 */
export function sequenceDuration(blocks: Block[]): number {
  return blocks.reduce((total, block) => total + block.duration_s, 0);
}

/** Absolute start time of every block (length === blocks.length). */
export function blockStarts(blocks: Block[]): number[] {
  const starts: number[] = [];
  let acc = 0;
  for (const block of blocks) {
    starts.push(acc);
    acc += block.duration_s;
  }
  return starts;
}

/** Which block contains absolute time `t` (clamped to the last block). */
export function blockIndexAt(blocks: Block[], t: number): number {
  const starts = blockStarts(blocks);
  for (let i = starts.length - 1; i >= 0; i--) {
    if (t >= starts[i] - 1e-9) return i;
  }
  return 0;
}

/** Absolute time of a marker: seconds inside a hold, proportion inside a transition. */
export function markerAbsTime(blockStart: number, block: Block, marker: EventMarker): number {
  return block.type === "hold" ? blockStart + marker.at : blockStart + marker.at * block.duration_s;
}

export interface ScheduledMarker {
  /** Absolute second on the plan ruler. */
  t: number;
  marker: EventMarker;
  blockIndex: number;
}

/** Every marker as an absolute-time schedule — the preview's event timetable. */
export function markerSchedule(blocks: Block[]): ScheduledMarker[] {
  const starts = blockStarts(blocks);
  const out: ScheduledMarker[] = [];
  blocks.forEach((block, blockIndex) => {
    for (const marker of block.markers) {
      out.push({ t: markerAbsTime(starts[blockIndex], block, marker), marker, blockIndex });
    }
  });
  return out.sort((a, b) => a.t - b.t);
}

// ── pose interpolation ───────────────────────────────────────────────────────

export type PoseMap = Record<string, Record<string, number>>;

/**
 * The planned pose at absolute time `t`, for preview and scrubbing.
 *
 * This is the *plan path*: joint-space lerp with the transition's easing,
 * computed on the plan ruler. The real arm walks whatever path the upstream
 * `move_to` picks — close, not guaranteed identical, and the UI says so.
 */
export function poseAtTime(blocks: Block[], poses: PoseMap, t: number): Record<string, number> {
  if (blocks.length === 0) return {};
  const starts = blockStarts(blocks);
  const index = blockIndexAt(blocks, t);
  const block = blocks[index];

  if (block.type === "hold") {
    return { ...(poses[block.pose_id] ?? {}) };
  }

  const prev = nearestHold(blocks, index, -1);
  const next = nearestHold(blocks, index, +1);
  const from = prev ? poses[prev.pose_id] : undefined;
  const to = next ? poses[next.pose_id] : undefined;
  if (!from && !to) return {};
  if (!from) return { ...to! };
  if (!to) return { ...from };

  const local = clamp01((t - starts[index]) / Math.max(block.duration_s, 1e-9));
  const k = easingFn(block.easing, local);
  const out: Record<string, number> = {};
  for (const joint of new Set([...Object.keys(from), ...Object.keys(to)])) {
    const a = from[joint] ?? 0;
    const b = to[joint] ?? 0;
    out[joint] = a + (b - a) * k;
  }
  return out;
}

/** Absolute playback time from a SeqPlayback frame, clamped to the ruler. */
export function playbackAbsTime(
  blocks: Block[],
  playback: { block_index: number; t_in_block: number; finished: boolean },
): number {
  const starts = blockStarts(blocks);
  const total = starts.length ? starts[starts.length - 1] + blocks[blocks.length - 1].duration_s : 0;
  if (playback.finished || playback.block_index >= blocks.length) return total;
  const index = Math.max(0, Math.min(playback.block_index, blocks.length - 1));
  return starts[index] + Math.min(playback.t_in_block, blocks[index].duration_s);
}
