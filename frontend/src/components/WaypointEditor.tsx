import { useState } from "react";
import { api } from "../api";
import type { Action, Routine, Waypoint } from "../types";
import { useToast } from "./Toasts";

interface Props {
  routine: Routine | null;
  teaching: boolean;
  currentIndex: number | null;
  onChanged: (routine: Routine) => void;
  /** Selecting a waypoint drives the 3D preview; null goes back to the live pose. */
  onPreview: (index: number | null) => void;
}

const summarise = (joints: Record<string, number>) =>
  Object.entries(joints)
    .map(([name, value]) => `${name.replace("joint", "J")} ${value >= 0 ? " " : ""}${value.toFixed(3)}`)
    .join("   ");

export function WaypointEditor({ routine, teaching, currentIndex, onChanged, onPreview }: Props) {
  const { attempt, show } = useToast();
  const [expanded, setExpanded] = useState<string | null>(null);
  const [dragFrom, setDragFrom] = useState<number | null>(null);
  const [dragOver, setDragOver] = useState<number | null>(null);

  if (!routine) {
    return (
      <div className="pane">
        <div className="pane-header">
          <h2>点位</h2>
        </div>
        <div className="empty">左边选一个序列。</div>
      </div>
    );
  }

  const capture = async () => {
    if (!teaching) {
      show("info", "先开示教，把臂拖到位再录点");
      return;
    }
    const updated = await attempt(() => api.waypoints.capture(routine.id));
    if (updated) onChanged(updated);
  };

  const drop = async (to: number) => {
    const from = dragFrom;
    setDragFrom(null);
    setDragOver(null);
    if (from === null || from === to) return;

    // The backend demands a full permutation — anything else silently drops or
    // duplicates a waypoint — so build it here rather than sending a pair.
    const order = routine.waypoints.map((_, index) => index);
    order.splice(to, 0, ...order.splice(from, 1));

    const updated = await attempt(() => api.waypoints.reorder(routine.id, order));
    if (updated) onChanged(updated);
  };

  return (
    <div className="pane">
      <div className="pane-header">
        <h2>{routine.name}</h2>
        <button className="primary" onClick={capture} disabled={!teaching} title={teaching ? "" : "先开示教"}>
          记录当前位置
        </button>
      </div>

      {routine.waypoints.length === 0 ? (
        <div className="empty">
          还没有点位。
          <br />
          开示教 → 用手把臂拖到想要的机位 → 松手 → 按「记录当前位置」。
        </div>
      ) : (
        routine.waypoints.map((waypoint, index) => (
          <div
            key={waypoint.id}
            className={[
              "waypoint",
              index === currentIndex ? "current" : "",
              index === dragFrom ? "dragging" : "",
              index === dragOver ? "drop-target" : "",
            ]
              .filter(Boolean)
              .join(" ")}
            onDragOver={(event) => {
              event.preventDefault();
              setDragOver(index);
            }}
            onDrop={() => drop(index)}
          >
            <div
              className="waypoint-row"
              onClick={() => {
                const opening = expanded !== waypoint.id;
                setExpanded(opening ? waypoint.id : null);
                onPreview(opening ? index : null);
              }}
            >
              <span
                className="grab"
                draggable
                onDragStart={() => setDragFrom(index)}
                onDragEnd={() => {
                  setDragFrom(null);
                  setDragOver(null);
                }}
                title="拖动重排"
              >
                ⠿
              </span>
              <span className="waypoint-index">{index + 1}</span>
              <span className="joint-summary">{summarise(waypoint.joints)}</span>
              <span className="badges">
                {waypoint.settle_ms > 0 && <span className="badge">{waypoint.settle_ms}ms</span>}
                {waypoint.actions.map((action, i) => (
                  <span key={i} className={`badge ${action.type === "shutter" ? "shutter" : ""}`}>
                    {action.type === "shutter" ? "拍" : `等 ${action.duration_s}s`}
                  </span>
                ))}
              </span>
              <span className="row-actions">
                <button
                  className="ghost"
                  onClick={async (event) => {
                    event.stopPropagation();
                    const updated = await attempt(() => api.waypoints.remove(routine.id, index));
                    if (updated) onChanged(updated);
                  }}
                >
                  删除
                </button>
              </span>
            </div>

            {expanded === waypoint.id && (
              <WaypointDetail
                routineId={routine.id}
                index={index}
                waypoint={waypoint}
                onChanged={onChanged}
              />
            )}
          </div>
        ))
      )}
    </div>
  );
}

function WaypointDetail({
  routineId,
  index,
  waypoint,
  onChanged,
}: {
  routineId: string;
  index: number;
  waypoint: Waypoint;
  onChanged: (routine: Routine) => void;
}) {
  const { attempt } = useToast();

  const patch = async (body: Parameters<typeof api.waypoints.update>[2]) => {
    const updated = await attempt(() => api.waypoints.update(routineId, index, body));
    if (updated) onChanged(updated);
  };

  const shutter = waypoint.actions.find((a): a is Extract<Action, { type: "shutter" }> => a.type === "shutter");

  const setActions = (actions: Action[]) => patch({ actions });

  return (
    <div className="waypoint-detail">
      <div className="field">
        <label>到位用时</label>
        <input
          type="number"
          step="0.1"
          min="0.1"
          defaultValue={waypoint.duration_s}
          onBlur={(event) => patch({ duration_s: Number(event.target.value) })}
        />
        <span className="hint">秒</span>
      </div>

      <div className="field">
        <label>稳定等待</label>
        <input
          type="number"
          step="50"
          min="0"
          defaultValue={waypoint.settle_ms}
          onBlur={(event) => patch({ settle_ms: Number(event.target.value) })}
        />
        <span className="hint">毫秒 — 臂停稳和照片不糊之间差几百毫秒</span>
      </div>

      <div className="field">
        <label>备注</label>
        <input
          style={{ flex: 1 }}
          defaultValue={waypoint.note}
          placeholder="比如「正面 45°」"
          onBlur={(event) => patch({ note: event.target.value })}
        />
      </div>

      <div className="field">
        <label>到位后</label>
        <div style={{ display: "grid", gap: 6, flex: 1 }}>
          {waypoint.actions.length === 0 && <span className="hint">什么都不做</span>}

          {waypoint.actions.map((action, i) => (
            <div key={i} className="action-row">
              <strong>{action.type === "shutter" ? "拍照" : "等待"}</strong>

              {action.type === "sleep" && (
                <input
                  type="number"
                  step="0.5"
                  min="0.1"
                  defaultValue={action.duration_s}
                  onBlur={(event) => {
                    const next = [...waypoint.actions];
                    next[i] = { ...action, duration_s: Number(event.target.value) };
                    setActions(next);
                  }}
                />
              )}

              {action.type === "shutter" && (
                <label className="hint">
                  <input
                    type="checkbox"
                    checked={action.focus_first}
                    onChange={(event) => {
                      const next = [...waypoint.actions];
                      next[i] = { ...action, focus_first: event.target.checked };
                      setActions(next);
                    }}
                  />{" "}
                  先对焦
                </label>
              )}

              <select
                value={action.on_failure}
                onChange={(event) => {
                  const next = [...waypoint.actions];
                  next[i] = { ...action, on_failure: event.target.value as Action["on_failure"] };
                  setActions(next);
                }}
                title="失败时"
              >
                <option value="abort">失败即停</option>
                <option value="retry">重试</option>
                <option value="skip">跳过</option>
              </select>

              <button
                className="ghost"
                onClick={() => setActions(waypoint.actions.filter((_, j) => j !== i))}
              >
                移除
              </button>
            </div>
          ))}

          <div style={{ display: "flex", gap: 6 }}>
            <button
              className="ghost"
              disabled={!!shutter}
              onClick={() =>
                setActions([
                  ...waypoint.actions,
                  { type: "shutter", focus_first: true, timeout_s: 5, on_failure: "abort", retries: 0 },
                ])
              }
            >
              + 拍照
            </button>
            <button
              className="ghost"
              onClick={() =>
                setActions([
                  ...waypoint.actions,
                  { type: "sleep", duration_s: 1, timeout_s: 5, on_failure: "abort", retries: 0 },
                ])
              }
            >
              + 等待
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
