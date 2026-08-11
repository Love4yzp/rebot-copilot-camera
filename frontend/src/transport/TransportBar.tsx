import type { SeqPlayback } from "../types";
import type { PreviewApi } from "../preview/usePreview";

interface Props {
  preview: PreviewApi;
  executing: boolean;
  playback: SeqPlayback | null;
  latched: boolean;
  /** The plan ruler's length — always labelled 预估, never a promise. */
  total: number;
  /** What the timecode shows: plan time in preview, real progress in execution. */
  clockT: number;
  canExecute: boolean;
  onExecute: () => void;
  onStop: () => void;
  onResume: () => void;
  /** The arm is far from the first station — show "去起点" instead of "执行". */
  far: boolean;
  /** Callback for the "去起点" button. */
  onGoToStart: () => void;
  /** True when executing, approaching, and on block 0 — show "接近起点…". */
  showApproaching: boolean;
}

function timecode(t: number, total: number): string {
  const m = Math.floor(t / 60);
  const s = Math.floor(t % 60);
  const d = Math.floor((t * 10) % 10);
  const mm = m < 10 ? `0${m}` : `${m}`;
  const ss = s < 10 ? `0${s}` : `${s}`;
  return `${mm}:${ss}.${d} / 预估 ${total.toFixed(1)}s`;
}

/**
 * The transport: two verbs, kept apart on purpose.
 *
 * 预演 plays the plan ruler — the arm does not move, nothing lights up. 执行
 * moves a 48 V arm for real, and its button says so on its face. They never
 * share a button: a play icon that sometimes means "the arm will actually
 * move" is how studios lose cameras. Pressing 执行 mid-preview stops the
 * preview and goes; pressing 预演 mid-execution is refused (the ruler is
 * locked — it is showing the truth now).
 */
export function TransportBar({
  preview,
  executing,
  playback,
  latched,
  total,
  clockT,
  canExecute,
  onExecute,
  onStop,
  onResume,
  far,
  onGoToStart,
  showApproaching,
}: Props) {
  const waiting = preview.waiting || playback?.phase === "wait";

  let previewLabel = "▶ 预演";
  let previewDisabled = false;
  if (latched || executing) {
    previewDisabled = true;
  } else if (preview.approaching) {
    previewLabel = "预演 · 接近起点…";
    previewDisabled = true;
  } else if (preview.waiting) {
    previewLabel = "预演 · 等待中";
    previewDisabled = true;
  } else if (preview.active && preview.playing) {
    previewLabel = "⏸ 暂停预演";
  } else if (preview.active) {
    previewLabel = "▶ 继续预演";
  }

  const previewClick = () => {
    if (preview.active && preview.playing) preview.pause();
    else if (preview.active) preview.resume();
    else preview.start();
  };

  const execLabel = (() => {
    if (executing && showApproaching) return "接近起点…";
    if (executing) return playback?.phase === "wait" ? "执行中 · 等待" : "执行中…";
    if (far) return "去起点";
    return "执行（臂会动）";
  })();

  const execClick = far && !executing ? onGoToStart : onExecute;

  return (
    <div className="transport">
      <button type="button" className="touch-target" disabled={previewDisabled} onClick={previewClick}>
        {previewLabel}
      </button>
      <button
        type="button"
        className="touch-target transport__exec"
        disabled={latched || executing || !canExecute || (!far && total === 0)}
        onClick={execClick}
      >
        {execLabel}
      </button>
      <button
        type="button"
        className="touch-target ghost"
        disabled={latched || (!preview.active && !executing)}
        onClick={onStop}
      >
        停止
      </button>
      {waiting ? (
        <button type="button" className="touch-target transport__continue" onClick={onResume}>
          等待中 · 点继续
        </button>
      ) : null}
      <span className="transport__timecode num">{timecode(clockT, total)}</span>
    </div>
  );
}
