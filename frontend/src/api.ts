import type {
  Action,
  EstopState,
  PlaybackState,
  ProviderInfo,
  Routine,
  RoutineSummary,
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

export const api = {
  health: () => request<Record<string, unknown>>("/api/health"),

  // ── emergency stop ──────────────────────────────────────────────────────
  estop: {
    get: () => request<EstopState>("/api/estop"),
    engage: (reason: string) => post<EstopState>("/api/estop", { reason, source: "ui" }),
    clear: () => post<EstopState>("/api/estop/clear"),
  },

  // ── routines ────────────────────────────────────────────────────────────
  routines: {
    list: () => request<RoutineSummary[]>("/api/routines"),
    get: (id: string) => request<Routine>(`/api/routines/${id}`),
    create: (name: string) => post<Routine>("/api/routines", { name }),
    rename: (id: string, name: string) =>
      request<Routine>(`/api/routines/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ name }),
      }),
    remove: (id: string) => request<void>(`/api/routines/${id}`, { method: "DELETE" }),
  },

  // ── waypoints ───────────────────────────────────────────────────────────
  waypoints: {
    /**
     * Add a waypoint from explicit joint angles, optionally at a given index.
     *
     * `capture` records wherever the arm is standing; this one records a pose
     * you already hold. That is what makes undoing a delete possible — the
     * arm has usually moved on by the time the operator changes their mind.
     */
    add: (
      routineId: string,
      body: {
        joints: Record<string, number>;
        duration_s?: number;
        settle_ms?: number;
        actions?: Action[];
        note?: string;
        index?: number;
      },
    ) => post<Routine>(`/api/routines/${routineId}/waypoints`, body),
    capture: (routineId: string, note?: string) =>
      post<Routine>(`/api/routines/${routineId}/waypoints/capture`, note === undefined ? undefined : { note }),
    remove: (routineId: string, index: number) =>
      request<Routine>(`/api/routines/${routineId}/waypoints/${index}`, { method: "DELETE" }),
    update: (
      routineId: string,
      index: number,
      patch: { settle_ms?: number; duration_s?: number; note?: string; actions?: Action[] },
    ) =>
      request<Routine>(`/api/routines/${routineId}/waypoints/${index}`, {
        method: "PATCH",
        body: JSON.stringify(patch),
      }),
    reorder: (routineId: string, order: number[]) =>
      post<Routine>(`/api/routines/${routineId}/waypoints/reorder`, { order }),
  },

  // ── plugins ─────────────────────────────────────────────────────────────
  plugins: {
    /** Every installed action provider, working or not. */
    list: () => request<ProviderInfo[]>("/api/plugins"),
    /** Re-run every provider's self-test. Moves no joints, burns no frame. */
    probe: () => post<ProviderInfo[]>("/api/plugins/probe"),
  },

  // ── motion ──────────────────────────────────────────────────────────────
  play: (routineId: string, source = "ui") =>
    post<PlaybackState>(`/api/routines/${routineId}/play`, { source }),
  /** Single-shot goto: move to one anchor, settle, fire its actions, hold. */
  goto: (routineId: string, index: number, source = "ui") =>
    post<unknown>(`/api/routines/${routineId}/waypoints/${index}/goto`, { source }),
  stopPlayback: () => post<PlaybackState>("/api/playback/stop"),
  teach: (enabled: boolean) => post<PlaybackState>("/api/teach", { enabled }),
  testShutter: (shoot: boolean) => post<{ ok: boolean; error: string | null }>(
    `/api/shutter/test?shoot=${shoot}`,
  ),
};
