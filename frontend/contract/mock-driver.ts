/**
 * Golden contract driver — the mock side.
 *
 * Runs the shared case files in `contract/cases/` against the in-memory mock
 * (`handleApi` for REST sessions, `normalize` from the timeline model for the
 * pure-logic cases) and returns a canonical transcript. `tests/test_contract.py`
 * runs the same cases against the FastAPI TestClient and diffs the two
 * transcripts entry by entry — a shape or semantic drift on either side turns
 * the suite red.
 *
 * Canonicalization exists so that values which *must* differ between two
 * processes (random ids, wall-clock timestamps, measured rates) don't drown
 * the comparison in noise, while everything structural is compared exactly.
 * The rules below are the contract's portability rules — keep them in sync
 * with their Python mirror in `tests/test_contract.py`:
 *
 *   - a null-valued key is the same as an absent key (FastAPI serializes
 *     optional fields as null; the mock omits them)
 *   - any 12-hex run inside a string is an id: replaced by `<id:N>` in
 *     first-appearance order, so "the same id in two places" is still
 *     checked — including ids embedded in messages ("no pose 'abc123…'")
 *   - a number ≥ 1e9 is a unix timestamp: replaced by `<ts>`
 *   - VOLATILE_KEYS name values neither side can control (measured rate,
 *     firmware banner): replaced by `<volatile>`
 *
 * Dict keys are traversed in sorted order so the id numbering does not depend
 * on either side's insertion order.
 */

import { handleApi } from "../mock/api";
import { createState } from "../mock/state";
import { normalize } from "../src/timeline/model";
import type { Block } from "../src/types";

const VOLATILE_KEYS = new Set(["rate_hz", "firmware_version", "uptime_s"]);
const ID_RE = /[0-9a-f]{12}/g;

export interface CaseStep {
  method: string;
  path: string;
  body?: unknown;
  /** var name → top-level response field captured for later `${var}` use. */
  save?: Record<string, string>;
  /** Top-level body keys dropped from comparison, with the reason in the case file. */
  ignore?: string[];
}

export interface GoldenCase {
  name: string;
  kind: "rest" | "normalize";
  steps?: CaseStep[];
  blocks?: unknown[];
}

export interface TranscriptEntry {
  status?: number;
  body?: unknown;
  blocks?: unknown;
}

export interface CaseTranscript {
  name: string;
  entries: TranscriptEntry[];
}

type Canon = (value: unknown, ids: Map<string, number>) => unknown;

const canon: Canon = (value, ids) => {
  if (Array.isArray(value)) return value.map((v) => canon(v, ids));
  if (typeof value === "object" && value !== null) {
    const out: Record<string, unknown> = {};
    for (const key of Object.keys(value).sort()) {
      const field = (value as Record<string, unknown>)[key];
      if (field === null || field === undefined) continue; // null ≈ absent
      out[key] = VOLATILE_KEYS.has(key) ? "<volatile>" : canon(field, ids);
    }
    return out;
  }
  if (typeof value === "string") {
    return value.replace(ID_RE, (match) => {
      if (!ids.has(match)) ids.set(match, ids.size + 1);
      return `<id:${ids.get(match)}>`;
    });
  }
  if (typeof value === "number" && value >= 1e9) return "<ts>";
  return value;
};

function substitute(value: unknown, vars: Record<string, unknown>): unknown {
  if (typeof value === "string") {
    return value.replace(/\$\{(\w+)\}/g, (_, name: string) => {
      if (!(name in vars)) throw new Error(`unset variable '${name}'`);
      return String(vars[name]);
    });
  }
  if (Array.isArray(value)) return value.map((v) => substitute(v, vars));
  if (typeof value === "object" && value !== null) {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value)) out[k] = substitute(v, vars);
    return out;
  }
  return value;
}

function runCase(golden: GoldenCase): TranscriptEntry[] {
  const ids = new Map<string, number>();

  if (golden.kind === "normalize") {
    const out = normalize(golden.blocks as Block[]);
    return [{ blocks: canon(out, ids) }];
  }

  // Fresh, empty state per case: the backend side starts from empty stores,
  // so the preview seeds (demo poses and the 四方位 sequence) stay out.
  const state = createState({ seed: false });
  const vars: Record<string, unknown> = {};
  const entries: TranscriptEntry[] = [];

  for (const step of golden.steps ?? []) {
    const path = substitute(step.path, vars) as string;
    const body = step.body === undefined ? undefined : substitute(step.body, vars);
    const url = new URL(path, "http://mock");
    const result = handleApi(state, step.method, url.pathname, url.searchParams, body);

    const entry: TranscriptEntry = { status: result.status };
    if (result.body !== undefined) {
      const bodyCanon = canon(result.body, ids) as Record<string, unknown>;
      for (const key of step.ignore ?? []) delete bodyCanon[key];
      entry.body = bodyCanon;
    }
    entries.push(entry);

    if (step.save && typeof result.body === "object" && result.body !== null) {
      for (const [varName, field] of Object.entries(step.save)) {
        vars[varName] = (result.body as Record<string, unknown>)[field];
      }
    }
  }
  return entries;
}

export function runCases(cases: GoldenCase[]): CaseTranscript[] {
  return cases.map((golden) => ({ name: golden.name, entries: runCase(golden) }));
}
