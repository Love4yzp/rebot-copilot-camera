/**
 * Display metadata for marker kinds: icon, label, and how to build a new one.
 *
 * "wait" is built into the host; everything else is a provider id whose label
 * and parameter defaults come from the provider's manifest. Unknown kinds
 * (seeded stand-ins, or a provider that was uninstalled) still render — a
 * marker that vanished would read as lost configuration.
 */

import type { EventMarker, ProviderInfo } from "../types";
import { makeMarker } from "./model";

export const WAIT_KIND = "wait";

const BUILTIN_LABEL: Record<string, string> = {
  wait: "等待",
  record_start: "开录",
  record_stop: "停录",
  fill_light: "补光",
};

const BUILTIN_ICON: Record<string, string> = {
  wait: "⏸",
  record_start: "▶",
  record_stop: "■",
};

export function markerLabel(kind: string, providers: ProviderInfo[]): string {
  if (kind === "shutter") return "快门";
  if (BUILTIN_LABEL[kind]) return BUILTIN_LABEL[kind];
  return providers.find((p) => p.id === kind)?.label ?? kind;
}

export function markerIcon(kind: string): string {
  if (kind === "shutter") return "◉";
  return BUILTIN_ICON[kind] ?? "✦";
}

/** One entry in the add-marker menu: 快门 / 等待 / every installed provider. */
export interface MarkerKindOption {
  kind: string;
  label: string;
  /** Unavailable providers stay visible but cannot be chosen. */
  enabled: boolean;
  reason: string | null;
}

export function markerKindOptions(providers: ProviderInfo[]): MarkerKindOption[] {
  const options: MarkerKindOption[] = [{ kind: WAIT_KIND, label: "等待", enabled: true, reason: null }];
  for (const provider of providers) {
    if (!provider.installed) continue;
    options.push({
      kind: provider.id,
      label: provider.label,
      enabled: provider.available,
      reason: provider.reason,
    });
  }
  return options;
}

/** Build a marker of the given kind with provider defaults filled in. */
export function newMarkerOfKind(
  kind: string,
  at: number,
  providers: ProviderInfo[],
): EventMarker {
  if (kind === WAIT_KIND) return makeMarker(WAIT_KIND, at, {}, 0);
  const provider = providers.find((p) => p.id === kind);
  const params: Record<string, unknown> = {};
  for (const field of provider?.fields ?? []) params[field.key] = field.default;
  return makeMarker(kind, at, params, 0.3);
}
