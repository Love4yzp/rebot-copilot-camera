import { useEffect, useState } from "react";
import { api } from "../api";
import type { Pose } from "../types";
import { useToast } from "../components/Toasts";

interface Props {
  /** Live joint angles from the control loop. */
  positions: Record<string, number>;
  /** Prefilled name — saving without typing gets this (自动命名). */
  autoName?: string;
  /**
   * When a sequence is open, the bar offers 「保存为站位」: capture and append
   * in one tap — the teach → assemble chain never leaves this bar.
   */
  onCaptureAppend?: (pose: Pose) => void;
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
export function TeachBar({ positions, autoName, onCaptureAppend, onDone }: Props) {
  const { attempt } = useToast();
  const [name, setName] = useState(autoName ?? "");
  /** 关节读数默认折叠——示教时用户只需要「零重力可推」，不是工程数据。 */
  const [showJoints, setShowJoints] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    void attempt(() => api.teach(true));
    return () => {
      void api.teach(false).catch(() => {});
    };
  }, [attempt]);

  const capture = async () => {
    const trimmed = name.trim() || `位姿 ${new Date().toLocaleTimeString("zh-CN", { hour12: false })}`;
    setSaving(true);
    const pose = await attempt(() => api.poses.capture(trimmed), `已录位姿「${trimmed}」`);
    setSaving(false);
    return pose;
  };

  const save = async () => {
    const pose = await capture();
    if (pose) onDone();
  };

  const saveAsStation = async () => {
    const pose = await capture();
    if (!pose) return;
    onCaptureAppend?.(pose);
    onDone();
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
        <p className="teach-bar__step">零重力 · 臂可推动，松手自动锁定——直接保存，名称自动取</p>
        {showJoints ? (
          <div className="teach-bar__readout num" aria-label="实时姿态">
            {joints.map(([joint, value]) => (
              <span key={joint}>
                {joint.replace("joint", "J")} {value.toFixed(2)}
              </span>
            ))}
          </div>
        ) : null}
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
        <button
          type="button"
          className="ghost"
          disabled={saving}
          onClick={() => setShowJoints((v) => !v)}
        >
          详细数据 {showJoints ? "▴" : "▾"}
        </button>
        <button type="button" className="primary" disabled={saving} onClick={() => void save()}>
          {saving ? "保存中…" : "保存"}
        </button>
        {onCaptureAppend ? (
          <button
            type="button"
            className="primary"
            disabled={saving}
            title="录下当前姿态并排到序列末尾，成为一个新站位"
            onClick={() => void saveAsStation()}
          >
            {saving ? "保存中…" : "保存并追加到序列"}
          </button>
        ) : null}
        <button type="button" className="ghost" disabled={saving} onClick={onDone}>
          × 取消
        </button>
      </div>
    </div>
  );
}
