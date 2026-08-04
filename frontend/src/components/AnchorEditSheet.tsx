import { useState } from "react";
import { api } from "../api";
import type { Action, ProviderInfo, Routine, ShutterAction, Waypoint } from "../types";
import { Dialog } from "./Dialog";
import { ProviderFields } from "./ProviderFields";
import { useToast } from "./Toasts";

interface Props {
  routine: Routine;
  index: number;
  /** Installed action providers, from `GET /api/plugins`. */
  providers: ProviderInfo[];
  /** Re-run every provider's self-test, for an accessory just plugged in. */
  onProbe: () => Promise<void>;
  /** Closed with the latest routine if anything was saved, null if untouched. */
  onClose: (updated: Routine | null) => void;
  /** Deleted: the parent offers an undo, so it needs the pose that just went away. */
  onRemoved: (updated: Routine, removed: Waypoint, index: number) => void;
}

/** 速度三档,秒数即写入模型的 duration_s。 */
const SPEED_TIERS = [
  { label: "慢", duration_s: 5 },
  { label: "标准", duration_s: 2.5 },
  { label: "快", duration_s: 1.2 },
] as const;

/** 按当前值就近归档:自定义秒数也显示为离它最近的那一档。 */
function nearestSpeedTier(duration_s: number): number {
  const values = SPEED_TIERS.map((t) => t.duration_s);
  let best = 0;
  for (let i = 1; i < values.length; i++) {
    if (Math.abs(values[i] - duration_s) < Math.abs(values[best] - duration_s)) best = i;
  }
  return best;
}

/**
 * The fixed defaults live here: on_failure / retries / timeout_s are
 * deliberately not in the UI, so this is the one place they are chosen.
 * settle_ms is never sent — the backend keeps the stored value.
 */
const POLICY = { on_failure: "abort", retries: 0, timeout_s: 5 } as const;

/** Read an action's editable parameters, whatever kind of action it is. */
function paramsOf(action: Action): Record<string, unknown> {
  if (action.type === "plugin") return { ...action.params };
  if (action.type === "shutter") {
    const { count, interval_s, focus_first } = action;
    return { count, interval_s, focus_first };
  }
  return { duration_s: (action as { duration_s: number }).duration_s };
}

/** Which provider drew an action, or null for the ones with no provider. */
function providerIdOf(action: Action): string | null {
  if (action.type === "plugin") return action.provider;
  if (action.type === "shutter") return "shutter";
  return null;
}

/**
 * Rebuild a stored action from edited params.
 *
 * The shutter keeps its own typed shape rather than becoming a PluginAction:
 * routines already on disk are typed, and the backend validates them on every
 * load. Only unknown providers go through the generic form.
 */
function toAction(providerId: string, params: Record<string, unknown>): Action {
  if (providerId === "shutter") {
    return {
      type: "shutter",
      focus_first: Boolean(params.focus_first ?? true),
      count: Number(params.count ?? 1),
      interval_s: Number(params.interval_s ?? 0),
      ...POLICY,
    } as ShutterAction;
  }
  return { type: "plugin", provider: providerId, params, ...POLICY } as Action;
}

/**
 * Edit sheet for one anchor: name, three-tier speed, its triggers, delete.
 *
 * The trigger half is drawn from `GET /api/plugins` rather than hard-coded for
 * the shutter, so installing a provider makes it appear here with no change to
 * this file. Actions are a list and their **order is what runs** — putting a
 * relay before the shutter is how "do this first" is expressed, and it needs no
 * mechanism beyond the up/down buttons.
 *
 * Every control saves immediately (api.waypoints.update) and the returned
 * routine replaces the local copy; close hands the latest routine (or null when
 * nothing changed) back to the parent. Displayed values come from local UI
 * state, not from patch responses, so an in-flight edit is never clobbered by a
 * late response.
 *
 * Delete is one tap with an undo, not two taps with a confirm. An anchor is a
 * pose somebody walked over and pushed the arm into, so it must be recoverable
 * — but a confirm dialog only slows down the deletes that were intended, and
 * does nothing for the one that was not.
 */
export function AnchorEditSheet({
  routine,
  index,
  providers,
  onProbe,
  onClose,
  onRemoved,
}: Props) {
  const { attempt } = useToast();

  const [probing, setProbing] = useState(false);
  const [latest, setLatest] = useState<Routine | null>(null);
  const current = latest ?? routine;
  const waypoint = current.waypoints[index];

  const [note, setNote] = useState(waypoint.note);
  const [tier, setTier] = useState(() => nearestSpeedTier(waypoint.duration_s));
  const [actions, setActions] = useState<Action[]>(waypoint.actions);

  const patch = async (body: Parameters<typeof api.waypoints.update>[2]) => {
    const updated = await attempt(() => api.waypoints.update(current.id, index, body));
    if (updated) setLatest(updated);
    return updated;
  };

  const commitActions = (next: Action[]) => {
    setActions(next);
    void patch({ actions: next });
  };

  const addAction = (providerId: string) => {
    const provider = providers.find((p) => p.id === providerId);
    const params = Object.fromEntries((provider?.fields ?? []).map((f) => [f.key, f.default]));
    commitActions([...actions, toAction(providerId, params)]);
  };

  const editAction = (at: number, key: string, value: unknown) => {
    const providerId = providerIdOf(actions[at]);
    if (providerId === null) return;
    const params = { ...paramsOf(actions[at]), [key]: value };
    commitActions(actions.map((a, i) => (i === at ? toAction(providerId, params) : a)));
  };

  const moveAction = (at: number, by: number) => {
    const to = at + by;
    if (to < 0 || to >= actions.length) return;
    const next = [...actions];
    [next[at], next[to]] = [next[to], next[at]];
    commitActions(next);
  };

  const removeAction = (at: number) => commitActions(actions.filter((_, i) => i !== at));

  const commitNote = () => {
    const trimmed = note.trim();
    if (trimmed !== waypoint.note) void patch({ note: trimmed });
  };

  const pickTier = (i: number) => {
    setTier(i);
    void patch({ duration_s: SPEED_TIERS[i].duration_s });
  };

  const close = async () => {
    // A pending name edit is flushed before closing so onClose never reports a
    // routine that is one patch behind.
    const trimmed = note.trim();
    if (trimmed !== waypoint.note) {
      const updated = await patch({ note: trimmed });
      onClose(updated ?? latest);
    } else {
      onClose(latest);
    }
  };

  const remove = async () => {
    // Snapshot before the delete: the arm will have moved on long before the
    // operator decides they want it back.
    const removed = waypoint;
    const updated = await attempt(() => api.waypoints.remove(current.id, index));
    if (updated) onRemoved(updated, removed, index);
  };

  const title = waypoint.note.trim() || `锚点 ${index + 1}`;

  return (
    <Dialog label={`编辑锚点 ${title}`} onClose={() => void close()}>
      <div className="sheet__head">
        <h3 className="sheet__title">{title}</h3>
        <button className="ghost" onClick={() => void close()} aria-label="关闭">
          ✕
        </button>
      </div>

      <div className="sheet__field">
        <label className="sheet__label" htmlFor="anchor-name">
          名称
        </label>
        <input
          id="anchor-name"
          value={note}
          onChange={(event) => setNote(event.target.value)}
          onBlur={commitNote}
          onKeyDown={(event) => {
            if (event.key === "Enter") event.currentTarget.blur();
          }}
          placeholder={`锚点 ${index + 1}`}
        />
      </div>

      <div className="sheet__field">
        <span className="sheet__label">速度</span>
        <div className="sheet__tiers" role="radiogroup" aria-label="速度">
          {SPEED_TIERS.map((t, i) => (
            <button
              key={t.label}
              type="button"
              role="radio"
              aria-checked={tier === i}
              className={`sheet__tier ${tier === i ? "selected" : ""}`}
              onClick={() => pickTier(i)}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      <div className="sheet__actions-list">
        <span className="sheet__label">触发</span>
        {actions.length === 0 && <p className="hint">到位后什么都不做</p>}

        {actions.map((action, at) => {
          const providerId = providerIdOf(action);
          const provider = providers.find((p) => p.id === providerId);
          const params = paramsOf(action);
          // An uninstalled provider is shown, not hidden: the anchor really
          // does carry this action, and a row that vanished would read as the
          // operator having lost their configuration.
          const missing = providerId !== null && provider === undefined;

          return (
            <div className={`sheet__action ${provider?.available === false || missing ? "down" : ""}`} key={at}>
              <div className="sheet__action-head">
                <span className="sheet__action-name">
                  {provider?.label ?? providerId ?? "等待"}
                </span>
                <div className="sheet__action-tools">
                  <button
                    type="button"
                    className="ghost"
                    onClick={() => moveAction(at, -1)}
                    disabled={at === 0}
                    aria-label="上移"
                  >
                    ↑
                  </button>
                  <button
                    type="button"
                    className="ghost"
                    onClick={() => moveAction(at, 1)}
                    disabled={at === actions.length - 1}
                    aria-label="下移"
                  >
                    ↓
                  </button>
                  <button
                    type="button"
                    className="ghost"
                    onClick={() => removeAction(at)}
                    aria-label="删除这个触发"
                  >
                    ✕
                  </button>
                </div>
              </div>

              {missing && <p className="sheet__action-reason">没有安装 {providerId} 插件</p>}
              {provider && !provider.available && (
                <p className="sheet__action-reason">{provider.reason ?? "当前不可用"}</p>
              )}

              {provider && (
                <ProviderFields
                  fields={provider.fields}
                  values={params}
                  onChange={(key, value) => editAction(at, key, value)}
                />
              )}
            </div>
          );
        })}

        <div className="sheet__add">
          {providers
            // A provider the host could not load is listed elsewhere with its
            // reason, but it is not offered here: it has no params model, so
            // the host would refuse the action on write. Offering something the
            // API is bound to reject is a worse answer than not offering it.
            .filter((provider) => provider.installed)
            .map((provider) => (
              <button
                key={provider.id}
                type="button"
                className="sheet__add-btn"
                onClick={() => addAction(provider.id)}
                // Adding one that is merely down is allowed: an operator often
                // lays out a shoot before the accessory is plugged in. The
                // pre-flight refuses to *play* it, which is where the arm is at
                // stake.
                title={provider.available ? undefined : (provider.reason ?? "当前不可用")}
              >
                + {provider.label}
                {provider.available ? "" : " ⚠"}
              </button>
            ))}

          {providers.some((provider) => !provider.available) && (
            <button
              type="button"
              className="ghost sheet__probe"
              disabled={probing}
              onClick={() => {
                setProbing(true);
                void onProbe().finally(() => setProbing(false));
              }}
            >
              {probing ? "检测中…" : "重新检测配件"}
            </button>
          )}
        </div>
      </div>

      <div className="sheet__actions">
        <button type="button" className="primary" onClick={() => void close()}>
          完成
        </button>
      </div>

      <div className="sheet__danger">
        <button type="button" className="danger" onClick={() => void remove()}>
          删除锚点
        </button>
        <span className="hint">删掉后可以撤销</span>
      </div>
    </Dialog>
  );
}
