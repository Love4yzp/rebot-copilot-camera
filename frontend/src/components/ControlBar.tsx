import { useState } from "react";
import { api } from "../api";
import type { Mode, PlaybackProgress, Routine } from "../types";
import { useToast } from "./Toasts";

interface Props {
  routine: Routine | null;
  playback: PlaybackProgress | null;
  mode: Mode | null;
  teaching: boolean;
  latched: boolean;
  config: boolean;
  viewerOpen: boolean;
  onToggleConfig: () => void;
  onToggleViewer: () => void;
}

const PHASE_LABEL: Record<PlaybackProgress["phase"], string> = {
  idle: "待命",
  moving: "移动中",
  settling: "稳定中",
  acting: "触发中",
  done: "完成",
  aborted: "已中止",
};

/**
 * The bottom bar: run the whole collection, and switch modes.
 *
 * Single-anchor progress deliberately does not live here — it fills the card
 * that was tapped, where the operator is already looking. What is left here is
 * the routine-wide run, which has no single card to report to.
 */
export function ControlBar({
  routine,
  playback,
  mode,
  teaching,
  latched,
  config,
  viewerOpen,
  onToggleConfig,
  onToggleViewer,
}: Props) {
  const { attempt } = useToast();
  const [pairing, setPairing] = useState(false);
  const playing = mode === "playback";
  const anchors = routine?.waypoints.length ?? 0;

  // A whole-collection run reports here; a single goto reports on its card.
  const fullRun = playback !== null && playback.waypoint_total > 1;
  const total = playback?.waypoint_total ?? anchors;
  const done = playback ? (playback.finished ? total : playback.waypoint_index) : 0;
  const percent = total > 0 ? (done / total) * 100 : 0;

  return (
    <div className="control-bar">
      {playing ? (
        <button
          className="touch-target"
          onClick={() => attempt(() => api.stopPlayback(), "已停止")}
        >
          停止
        </button>
      ) : (
        <button
          className="primary touch-target"
          disabled={anchors === 0 || latched || teaching}
          onClick={() => routine && attempt(() => api.play(routine.id))}
        >
          播放全部
        </button>
      )}

      {config && (
        <button
          className="ghost touch-target"
          onClick={() =>
            attempt(async () => {
              const result = await api.testShutter(false);
              if (!result.ok) throw new Error(result.error ?? "快门链路不通");
              return result;
            }, "快门链路正常，相机已连接")
          }
        >
          测快门
        </button>
      )}

      {config && (
        // Pairing, not testing: the board keeps its camera across a routine but
        // loses it whenever it resets, and until this button existed the only
        // way back was a serial terminal. Disabled during playback because the
        // board answers one command at a time — the backend refuses it too, but
        // a button that reports 409 is a button that should not have been lit.
        <button
          className="ghost touch-target"
          disabled={pairing || playing}
          onClick={() => {
            setPairing(true);
            void attempt(async () => {
              const result = await api.pairShutter();
              if (!result.ok) throw new Error(result.error ?? "没有找到相机");
              return result;
            }, "相机已配对").finally(() => setPairing(false));
          }}
        >
          {pairing ? "配对中…" : "配对相机"}
        </button>
      )}

      {fullRun ? (
        <>
          <div className="progress-track">
            <div
              className={`progress-fill ${playback.phase === "aborted" ? "aborted" : ""} ${
                playback.phase === "done" ? "done" : ""
              }`}
              style={{ width: `${percent}%` }}
            />
          </div>
          <span className="control-status num">
            {PHASE_LABEL[playback.phase]} {Math.min(playback.waypoint_index + 1, total)}/{total}
          </span>
        </>
      ) : (
        <>
          <span className="control-spacer" />
          <span className="control-status">
            {anchors > 0 ? `${anchors} 个锚点 · 点卡片，臂过去并自动拍摄` : "还没有锚点"}
          </span>
        </>
      )}

      {playback?.error && <span className="control-error">{playback.error}</span>}

      <button className="ghost touch-target" aria-pressed={viewerOpen} onClick={onToggleViewer}>
        3D
      </button>
      <button className="ghost touch-target" aria-pressed={config} onClick={onToggleConfig}>
        {config ? "完成" : "编辑"}
      </button>
    </div>
  );
}
