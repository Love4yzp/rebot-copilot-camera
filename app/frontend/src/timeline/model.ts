/** Pure sequence structure and normalization. No browser motion simulation. */

import type {
  Block,
  Easing,
  EventMarker,
  HoldBlock,
  TransitionBlock,
} from "../types";

/** Defaults for an auto-generated transition: slow and smooth beats fast. */
export const DEFAULT_TRANSITION_S = 2.0;
export const DEFAULT_EASING: Easing = "ease_in_out";

/** Minimum hold duration — shorter than this is a mis-tap, not a station. */
export const MIN_HOLD_S = 0.5;

/**
 * 12-hex id, same length as the backend's uuid4().hex[:12].
 *
 * Real entropy from crypto, not Math.random().toString(16): that drops the
 * trailing zero hex digits of small values, so slice+padEnd mapped many
 * distinct values onto the same id — the mock seed data actually hit it on a
 * normal boot, and two blocks/markers with one id make React drop or duplicate
 * children silently.
 */
export function newId(): string {
  const bytes = new Uint8Array(6);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

export function makeMarker(
  kind: "wait",
  at: number,
  params: Record<string, unknown> = {},
  estimate_s: 0 = 0,
): EventMarker {
  return { id: newId(), kind, params, at, estimate_s };
}

export const DEFAULT_HOLD_S = 3;

export function makeHold(
  pose_id: string,
  duration_s = DEFAULT_HOLD_S,
): HoldBlock {
  return { type: "hold", id: newId(), pose_id, duration_s, markers: [] };
}

export function makeTransition(
  duration_s = DEFAULT_TRANSITION_S,
  easing: Easing = DEFAULT_EASING,
): TransitionBlock {
  return { type: "transition", id: newId(), duration_s, easing, markers: [] };
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
  const used = new Set<string>();
  for (let i = 0; i < holds.length; i++) {
    out.push(holds[i]);
    if (i >= holds.length - 1) continue;
    const a = holds[i];
    const b = holds[i + 1];
    if (a.pose_id === b.pose_id) continue; // same pose adjacent: no transition
    const key = pairKey(a.pose_id, b.pose_id);
    const remembered = memory.get(key);
    if (!remembered) {
      out.push(makeTransition());
    } else if (used.has(key)) {
      // The same pose pair can occur more than once in one sequence
      // (A→B→A→B): every rebuilt block needs its own identity. The first
      // occurrence keeps the remembered ids — a no-op normalize must not
      // move the inspector's selection — but later ones must be fresh, or
      // two blocks (and their markers) share a key and React silently drops
      // or duplicates timeline children.
      out.push({
        ...remembered,
        id: newId(),
        markers: remembered.markers.map((m) => ({ ...m, id: newId() })),
      });
    } else {
      used.add(key);
      out.push({
        ...remembered,
        markers: remembered.markers.map((m) => ({ ...m })),
      });
    }
  }
  return out;
}

function pairKey(poseA: string, poseB: string): string {
  // Direction does not matter for inheriting duration/easing: the way back
  // is the same road.
  return poseA < poseB ? `${poseA}|${poseB}` : `${poseB}|${poseA}`;
}

function nearestHold(
  blocks: Block[],
  from: number,
  step: -1 | 1,
): HoldBlock | undefined {
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
/** Largest single-joint delta between two poses (rad). */
export function maxJointDelta(
  from: Record<string, number>,
  to: Record<string, number>,
): number {
  let max = 0;
  for (const joint of new Set([...Object.keys(from), ...Object.keys(to)])) {
    const a = from[joint] ?? 0;
    const b = to[joint] ?? 0;
    const d = Math.abs(b - a);
    if (d > max) max = d;
  }
  return max;
}
