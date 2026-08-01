import { api } from "../api";
import type { RoutineSummary } from "../types";
import { useToast } from "./Toasts";

interface Props {
  routines: RoutineSummary[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onChanged: () => void;
}

export function RoutineList({ routines, selectedId, onSelect, onChanged }: Props) {
  const { attempt } = useToast();

  const create = async () => {
    const name = prompt("新序列名称", "多视角拍摄");
    if (!name) return;
    const routine = await attempt(() => api.routines.create(name));
    if (routine) {
      onChanged();
      onSelect(routine.id);
    }
  };

  const rename = async (summary: RoutineSummary) => {
    const name = prompt("重命名", summary.name);
    if (!name || name === summary.name) return;
    if (await attempt(() => api.routines.rename(summary.id, name))) onChanged();
  };

  const remove = async (summary: RoutineSummary) => {
    // Routines are hand-taught: every one represents someone standing at the
    // arm dragging it into position, so deletion always asks.
    if (!confirm(`删除「${summary.name}」？${summary.waypoint_count} 个点位会一起没。`)) return;
    if (await attempt(() => api.routines.remove(summary.id), "已删除")) onChanged();
  };

  return (
    <div className="pane">
      <div className="pane-header">
        <h2>序列</h2>
        <button className="ghost" onClick={create}>
          + 新建
        </button>
      </div>

      {routines.length === 0 ? (
        <div className="empty">还没有序列。
          <br />新建一个，然后拖动机械臂录点。</div>
      ) : (
        routines.map((routine) => (
          <button
            key={routine.id}
            className="routine-item"
            aria-current={routine.id === selectedId}
            onClick={() => onSelect(routine.id)}
            onDoubleClick={() => rename(routine)}
            onContextMenu={(event) => {
              event.preventDefault();
              remove(routine);
            }}
            title="双击重命名 · 右键删除"
          >
            <span className="name">{routine.name}</span>
            <span className="meta">
              {routine.waypoint_count} 点
              {routine.shutter_count > 0 ? ` · ${routine.shutter_count} 张` : ""}
            </span>
          </button>
        ))
      )}
    </div>
  );
}
