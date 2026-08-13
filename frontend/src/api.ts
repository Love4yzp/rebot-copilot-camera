import type {
  Block,
  EstopState,
  PlaybackState,
  Pose,
  PoseLinks,
  ProviderInfo,
  SeqTemplate,
  Sequence,
  SequenceSummary,
  ShutterResult,
  TuningState,
} from "./types";

/** Error carrying the server's structured reason, so the UI can show it. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly detail?: unknown,
  ) {
    super(message);
  }
}

/** Pull a readable sentence out of whatever shape the server sent. */
function explain(status: number, body: unknown): string {
  const detail = (body as { detail?: unknown })?.detail;

  if (typeof detail === "string") return detail;

  if (detail && typeof detail === "object") {
    const d = detail as Record<string, unknown>;
    if (Array.isArray(d.reasons)) return (d.reasons as string[]).join("; ");
    if (typeof d.message === "string") {
      return d.reason ? `${d.message} (${d.reason})` : d.message;
    }
  }

  if (Array.isArray(detail)) {
    // Pydantic validation errors.
    return detail
      .map((e: { loc?: unknown[]; msg?: string }) =>
        `${(e.loc ?? []).slice(1).join(".")}: ${e.msg ?? "invalid"}`,
      )
      .join("; ");
  }

  return `request failed (${status})`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: init?.body ? { "content-type": "application/json" } : undefined,
  });

  if (response.status === 204) return undefined as T;

  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(response.status, explain(response.status, body), body);
  }
  return body as T;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

const patch = <T>(path: string, body: unknown) =>
  request<T>(path, { method: "PATCH", body: JSON.stringify(body) });

const del = (path: string) => request<void>(path, { method: "DELETE" });

export const api = {
  health: () => request<Record<string, unknown>>("/api/health"),

  // ── emergency stop ──────────────────────────────────────────────────────
  estop: {
    get: () => request<EstopState>("/api/estop"),
    engage: (reason: string) => post<EstopState>("/api/estop", { reason, source: "ui" }),
    clear: () => post<EstopState>("/api/estop/clear"),
  },

  // ── poses (the library) ─────────────────────────────────────────────────
  poses: {
    list: () => request<Pose[]>("/api/poses"),
    create: (name: string, joints: Record<string, number>) =>
      post<Pose>("/api/poses", { name, joints }),
    /** Record wherever the arm is standing right now, under a name. */
    capture: (name: string) => post<Pose>("/api/poses/capture", { name }),
    patch: (id: string, body: { name?: string; joints?: Record<string, number> }) =>
      patch<Pose>(`/api/poses/${id}`, body),
    remove: (id: string) => del(`/api/poses/${id}`),
    /**
     * Which sequences link this pose. The UI asks before deleting or
     * overwriting — silently rewriting the physical path of N sequences is
     * the "a whole round of empty frames" class of failure.
     */
    links: (id: string) => request<PoseLinks>(`/api/poses/${id}/links`),
    /** Single-pose goto: eased move, then hold. */
    goto: (id: string) => post<PlaybackState>(`/api/poses/${id}/goto`),
  },

  // ── sequences ───────────────────────────────────────────────────────────
  sequences: {
    list: () => request<SequenceSummary[]>("/api/sequences"),
    get: (id: string) => request<Sequence>(`/api/sequences/${id}`),
    create: (name: string) => post<Sequence>("/api/sequences", { name }),
    /**
     * Whole-document patch. When `blocks` is present the server normalizes
     * before storing (transitions are automatic, never edited directly).
     */
    patch: (id: string, body: { name?: string; blocks?: Block[] }) =>
      patch<Sequence>(`/api/sequences/${id}`, body),
    remove: (id: string) => del(`/api/sequences/${id}`),
    /** Run it for real — the arm moves. */
    execute: (id: string) => post<PlaybackState>(`/api/sequences/${id}/execute`),
  },

  // ── templates ───────────────────────────────────────────────────────────
  templates: {
    list: () => request<SeqTemplate[]>("/api/templates"),
    /** Snapshot a sequence as a structural recipe (pose slots, no joints). */
    create: (sequenceId: string, name?: string) =>
      post<SeqTemplate>("/api/templates", { sequence_id: sequenceId, name }),
    remove: (id: string) => del(`/api/templates/${id}`),
    /** Copy the recipe with each slot bound to a library pose. */
    instantiate: (id: string, body: { name: string; pose_ids: string[] }) =>
      post<Sequence>(`/api/templates/${id}/instantiate`, body),
  },

  // ── execution control ───────────────────────────────────────────────────
  execute: {
    stop: () => post<PlaybackState>("/api/execute/stop"),
    /** Continue past a wait marker the run is suspended on. */
    resume: () => post<PlaybackState>("/api/execute/resume"),
  },

  // ── plugins ─────────────────────────────────────────────────────────────
  plugins: {
    /** Every installed action provider, working or not. */
    list: () => request<ProviderInfo[]>("/api/plugins"),
    /** Re-run every provider's self-test. Moves no joints, burns no frame. */
    probe: () => post<ProviderInfo[]>("/api/plugins/probe"),
  },

  // ── teach + shutter ─────────────────────────────────────────────────────
  teach: (enabled: boolean) => post<PlaybackState>("/api/teach", { enabled }),
  testShutter: (shoot: boolean) => post<ShutterResult>(`/api/shutter/test?shoot=${shoot}`),
  /**
   * Attach the camera over BLE. Slow — the board scans for thirty seconds
   * while somebody puts the camera into its own pairing mode — and refused
   * with a 409 while a sequence is executing.
   */
  pairShutter: () => post<ShutterResult>("/api/shutter/pair"),

  // ── tuning ──────────────────────────────────────────────────────────────
  tuning: {
    get: () => request<TuningState>("/api/config/tuning"),
    /** Deep-merge partial patch. Body is {section: {field: value, ...}} */
    put: (patch: Record<string, unknown>) =>
      request<TuningState>("/api/config/tuning", { method: "PUT", body: JSON.stringify(patch) }),
    save: () => post<TuningState>("/api/config/tuning/save"),
    reset: () => post<TuningState>("/api/config/tuning/reset"),
  },
};
