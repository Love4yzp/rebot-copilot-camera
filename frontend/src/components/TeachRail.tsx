import { useEffect, useState } from "react";
import { api } from "../api";
import type { Routine } from "../types";
import { useToast } from "./Toasts";

interface Props {
  routine: Routine;
  /** Wizard step names: record one anchor per name, saved as that step's note. null = single anchor. */
  names: string[] | null;
  /** Finished or cancelled — the parent closes the rail. */
  onDone: () => void;
}

/**
 * Drag-teach, as a bottom rail rather than a centred modal.
 *
 * Teaching is the one flow where the operator is looking at the arm and has
 * both hands on it. A centred dialog over a full-screen scrim was the wrong
 * shape for that: it covered the 3D view, it covered the emergency stop, and
 * it put the instruction in the middle of a screen nobody was looking at. A
 * rail pinned to the bottom edge leaves the room visible, and carries its own
 * stop so the control is under the same thumb as the instruction.
 *
 * The rail owns teach mode for its whole lifetime: teach(true) on mount,
 * teach(false) on done / cancel / unmount. React StrictMode mounts twice, so
 * both directions must be idempotent — repeating teach(false) is harmless.
 */
export function TeachRail({ routine, names, onDone }: Props) {
  const { attempt } = useToast();
  const [step, setStep] = useState(0);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    void attempt(() => api.teach(true));
    return () => {
      void api.teach(false).catch(() => {});
    };
  }, [attempt]);

  const wizard = names !== null && names.length > 0;
  const total = wizard ? names.length : 1;
  const isLastStep = step >= total - 1;

  const finish = () => {
    void api.teach(false).catch(() => {});
    onDone();
  };

  const save = async () => {
    setSaving(true);
    const note = wizard ? names[step] : undefined;
    const updated = await attempt(() => api.waypoints.capture(routine.id, note));
    setSaving(false);
    if (!updated) return; // toast already shown; stay on this step
    if (isLastStep) finish();
    else setStep(step + 1);
  };

  const skip = () => {
    if (isLastStep) finish();
    else setStep(step + 1);
  };

  return (
    <div className="teach-rail" role="region" aria-label="录锚点">
      <button
        className="teach-rail__estop"
        onClick={() => attempt(() => api.estop.engage("operator pressed stop during teach"))}
      >
        急停
      </button>

      <div className="teach-rail__body">
        <p className="teach-rail__step">
          {wizard ? (
            <>
              把臂拖到 <b>「{names[step]}」</b>，松手后点保存
            </>
          ) : (
            <>用手把臂拖到位，松手后点保存</>
          )}
        </p>
        <span className="teach-rail__sub">
          臂已卸力，可以直接推。松手会自动锁在当前位置。
          {wizard && ` 第 ${step + 1} / ${total} 个`}
        </span>
      </div>

      {wizard && (
        <div className="teach-rail__dots" aria-hidden>
          {names.map((name, i) => (
            <span
              key={name}
              className={`teach-rail__dot ${i < step ? "done" : i === step ? "current" : ""}`}
            />
          ))}
        </div>
      )}

      <div className="teach-rail__actions">
        <button className="primary" onClick={() => void save()} disabled={saving}>
          {saving ? "保存中…" : "保存"}
        </button>
        {wizard && (
          <button className="ghost" onClick={skip} disabled={saving}>
            跳过
          </button>
        )}
        <button className="ghost" onClick={finish} disabled={saving}>
          {wizard ? "结束" : "取消"}
        </button>
      </div>
    </div>
  );
}
