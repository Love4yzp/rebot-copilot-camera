import { useState } from "react";
import { api } from "../api";
import type { Action, Routine, ShutterAction, Waypoint } from "../types";
import { Dialog } from "./Dialog";
import { useToast } from "./Toasts";

interface Props {
  routine: Routine;
  index: number;
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

const INTERVAL_TIERS = [0.5, 1, 2, 5] as const;

/** 按当前值就近归档:自定义秒数也显示为离它最近的那一档。 */
function nearest(values: readonly number[], value: number): number {
  let best = 0;
  for (let i = 1; i < values.length; i++) {
    if (Math.abs(values[i] - value) < Math.abs(values[best] - value)) best = i;
  }
  return best;
}

const nearestSpeedTier = (duration_s: number) =>
  nearest(SPEED_TIERS.map((t) => t.duration_s), duration_s);

const nearestIntervalTier = (interval_s: number) => nearest(INTERVAL_TIERS, interval_s);

/**
 * The fixed defaults live here: on_failure / retries / timeout_s are
 * deliberately not in the UI, so this is the one place they are chosen.
 * settle_ms is never sent — the backend keeps the stored value.
 */
function withFixedFields(action: Partial<ShutterAction>): ShutterAction {
  return {
    type: "shutter",
    focus_first: true,
    count: 1,
    interval_s: 0,
    ...action,
    on_failure: "abort",
    retries: 0,
    timeout_s: 5,
  };
}

/**
 * Edit sheet for one anchor: name, three-tier speed, trigger config, delete.
 *
 * Every control saves immediately (api.waypoints.update) and the returned
 * routine replaces the local copy; close hands the latest routine (or null
 * when nothing changed) back to the parent. Displayed values come from local
 * UI state, not from patch responses, so an in-flight edit is never clobbered
 * by a late response.
 *
 * Delete is one tap with an undo, not two taps with a confirm. An anchor is a
 * pose somebody walked over and pushed the arm into, so it must be
 * recoverable — but a confirm dialog only slows down the deletes that were
 * intended, and does nothing for the one that was not.
 */
export function AnchorEditSheet({ routine, index, onClose, onRemoved }: Props) {
  const { attempt } = useToast();

  const [latest, setLatest] = useState<Routine | null>(null);
  const current = latest ?? routine;
  const waypoint = current.waypoints[index];

  const existing = waypoint.actions.find((a): a is ShutterAction => a.type === "shutter") ?? null;

  const [note, setNote] = useState(waypoint.note);
  const [tier, setTier] = useState(() => nearestSpeedTier(waypoint.duration_s));
  const [triggerOn, setTriggerOn] = useState(existing !== null);
  const [count, setCount] = useState(Math.min(10, Math.max(1, existing?.count ?? 1)));
  const [intervalTier, setIntervalTier] = useState(() =>
    nearestIntervalTier(existing?.interval_s ?? 1),
  );
  const [focusFirst, setFocusFirst] = useState(existing?.focus_first ?? true);

  const patch = async (body: Parameters<typeof api.waypoints.update>[2]) => {
    const updated = await attempt(() => api.waypoints.update(current.id, index, body));
    if (updated) setLatest(updated);
    return updated;
  };

  /** Rebuild the actions array around the (first) shutter action. */
  const setShutter = (fields: Partial<ShutterAction> | null) => {
    const rest = waypoint.actions.filter((a) => a.type !== "shutter");
    const actions: Action[] =
      fields === null ? rest : [...rest, withFixedFields({ ...(existing ?? undefined), ...fields })];
    return patch({ actions });
  };

  const commitNote = () => {
    const trimmed = note.trim();
    if (trimmed !== waypoint.note) void patch({ note: trimmed });
  };

  const pickTier = (i: number) => {
    setTier(i);
    void patch({ duration_s: SPEED_TIERS[i].duration_s });
  };

  const toggleTrigger = (on: boolean) => {
    setTriggerOn(on);
    if (on) {
      void setShutter({
        focus_first: focusFirst,
        count,
        interval_s: count > 1 ? INTERVAL_TIERS[intervalTier] : 0,
      });
    } else {
      void setShutter(null);
    }
  };

  const stepCount = (next: number) => {
    const clamped = Math.min(10, Math.max(1, next));
    setCount(clamped);
    void setShutter({
      focus_first: focusFirst,
      count: clamped,
      interval_s: clamped > 1 ? INTERVAL_TIERS[intervalTier] : 0,
    });
  };

  const pickInterval = (i: number) => {
    setIntervalTier(i);
    void setShutter({ focus_first: focusFirst, count, interval_s: INTERVAL_TIERS[i] });
  };

  const toggleFocus = (on: boolean) => {
    setFocusFirst(on);
    void setShutter({
      focus_first: on,
      count,
      interval_s: count > 1 ? INTERVAL_TIERS[intervalTier] : 0,
    });
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

      <div className="sheet__field">
        <span className="sheet__label">触发</span>
        <button
          type="button"
          role="switch"
          aria-checked={triggerOn}
          className={`sheet__switch ${triggerOn ? "on" : ""}`}
          onClick={() => toggleTrigger(!triggerOn)}
        >
          {triggerOn ? "开" : "关"}
        </button>
      </div>

      {triggerOn && (
        <>
          <div className="sheet__field">
            <span className="sheet__label">次数</span>
            <div className="sheet__stepper">
              <button
                type="button"
                className="sheet__stepper-btn"
                onClick={() => stepCount(count - 1)}
                disabled={count <= 1}
                aria-label="减少次数"
              >
                −
              </button>
              <span className="sheet__stepper-num">{count}</span>
              <button
                type="button"
                className="sheet__stepper-btn"
                onClick={() => stepCount(count + 1)}
                disabled={count >= 10}
                aria-label="增加次数"
              >
                +
              </button>
            </div>
          </div>

          {count > 1 && (
            <div className="sheet__field">
              <span className="sheet__label">间隔</span>
              <div className="sheet__tiers" role="radiogroup" aria-label="间隔">
                {INTERVAL_TIERS.map((seconds, i) => (
                  <button
                    key={seconds}
                    type="button"
                    role="radio"
                    aria-checked={intervalTier === i}
                    className={`sheet__tier ${intervalTier === i ? "selected" : ""}`}
                    onClick={() => pickInterval(i)}
                  >
                    {seconds} 秒
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="sheet__field">
            <label className="sheet__check">
              <input
                type="checkbox"
                checked={focusFirst}
                onChange={(event) => toggleFocus(event.target.checked)}
              />
              先对焦
            </label>
          </div>
        </>
      )}

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
