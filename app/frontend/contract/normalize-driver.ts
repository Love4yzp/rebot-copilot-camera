/** Normalization parity driver. Canonicalization rules match tests/test_contract.py. */
import { normalize } from "../src/timeline/model";
import type { Block } from "../src/types";
const VOLATILE_KEYS = new Set(["rate_hz", "firmware_version", "uptime_s"]);
const ID_RE = /[0-9a-f]{12}/g;
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

export function runCases(
  cases: { name: string; kind: string; blocks?: Block[] }[],
) {
  return cases
    .filter((c) => c.kind === "normalize")
    .map((c) => ({
      name: c.name,
      entries: [{ blocks: canon(normalize(c.blocks ?? []), new Map()) }],
    }));
}
