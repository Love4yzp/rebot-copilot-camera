import { useEffect, useState } from "react";
import { api } from "../api";
import { useToast } from "../components/Toasts";

interface Props {
  /** Live joint angles from the control loop. */
  positions: Record<string, number>;
  /** Finished (a pose was saved) or cancelled — the parent closes the bar. */
  onDone: () => void;
}

/**
 * Drag-teach, as a bottom bar. Teaching is the one flow where the operator is
 * looking at the arm with both hands on it, so the bar carries its own stop
 * and the readout sits at the bottom edge within thumb's reach.
 *
 * The bar owns teach mode for its whole lifetime: teach(true) on mount,
 * teach(false) on done / cancel / unmount. React StrictMode mounts twice, so
 * both directions must be idempotent — repeating teach(false) is harmless.
 * The estop force-closes the bar from the parent: unmounting runs the
 * cleanup, which exits teach — dragging against a latched arm is the one
 * combination that must be impossible to leave on screen.
 */
export function TeachBar({ positions, onDone }: Props) {
  const { attempt } = useToast();
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    void attempt(() => api.teach(true));
    return () => {
      void api.teach(false).catch(() => {});
    };
  }, [attempt]);

  const save = async () => {
    const trimmed = name.trim() || `位姿 ${new Date().toLocaleTimeString("zh-CN", { hour12: false })}`;
    setSaving(true);
    const pose = await attempt(() => api.poses.capture(trimmed), `已录位姿「${trimmed}」`);
    setSaving(false);
    if (pose) onDone();
  };

  const joints = Object.entries(positions);

  return (
    <div className="teach-bar" role="region" aria-label="录位姿">
      <button
        type="button"
        className="teach-bar__estop"
        onClick={() => attempt(() => api.estop.engage("operator pressed stop during teach"))}
      >
        急停
      </button>

      <div className="teach-bar__body">
        <p className="teach-bar__step">臂已卸力，可以直接推。把臂拖到位，松手后命名并保存。</p>
        <div className="teach-bar__readout num" aria-label="实时姿态">
          {joints.map(([joint, value]) => (
            <span key={joint}>
              {joint.replace("joint", "J")} {value.toFixed(2)}
            </span>
          ))}
        </div>
      </div>

      <div className="teach-bar__actions">
        <input
          value={name}
          placeholder="位姿名称（如：正面）"
          aria-label="位姿名称"
          onChange={(event) => setName(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !saving) void save();
          }}
        />
        <button type="button" className="primary" disabled={saving} onClick={() => void save()}>
          {saving ? "保存中…" : "保存位姿"}
        </button>
        <button type="button" className="ghost" disabled={saving} onClick={onDone}>
          退出示教
        </button>
      </div>
    </div>
  );
}
