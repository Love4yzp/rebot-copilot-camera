import { api } from "../api";
import type { Mode, PlaybackProgress, Routine } from "../types";
import { useToast } from "./Toasts";

interface Props {
  routine: Routine | null;
  playback: PlaybackProgress | null;
  mode: Mode | null;
  teaching: boolean;
  latched: boolean;
}

const PHASE_LABEL: Record<PlaybackProgress["phase"], string> = {
  idle: "待命",
  moving: "移动中",
  settling: "稳定中",
  acting: "执行中",
  done: "完成",
  aborted: "已中止",
};

export function PlaybackBar({ routine, playback, mode, teaching, latched }: Props) {
  const { attempt } = useToast();
  const playing = mode === "playback";

  const total = playback?.waypoint_total ?? routine?.waypoints.length ?? 0;
  const done = playback ? (playback.finished ? total : playback.waypoint_index) : 0;
  const percent = total > 0 ? (done / total) * 100 : 0;

  return (
    <div className="playback-bar">
      <button
        onClick={() => attempt(() => api.teach(!teaching), teaching ? "已退出示教" : "示教中 — 可以用手拖动机械臂")}
        disabled={latched}
        title={latched ? "急停中" : "零力拖动"}
      >
        {teaching ? "退出示教" : "开始示教"}
      </button>

      {playing ? (
        <button onClick={() => attempt(() => api.stopPlayback(), "已停止")}>停止</button>
      ) : (
        <button
          className="primary"
          disabled={!routine || routine.waypoints.length === 0 || latched || teaching}
          onClick={() => routine && attempt(() => api.play(routine.id))}
          title={teaching ? "先退出示教" : ""}
        >
          播放
        </button>
      )}

      <button
        className="ghost"
        onClick={() =>
          attempt(async () => {
            const result = await api.testShutter(false);
            if (!result.ok) throw new Error(result.error ?? "快门链路不通");
            return result;
          }, "快门链路正常")
        }
      >
        测快门
      </button>

      <div className="progress-track" title={`${done} / ${total}`}>
        <div
          className={`progress-fill ${playback?.phase === "aborted" ? "aborted" : ""} ${
            playback?.phase === "done" ? "done" : ""
          }`}
          style={{ width: `${percent}%` }}
        />
      </div>

      <span className="num" style={{ fontSize: 12, color: "var(--text-dim)", minWidth: "9em" }}>
        {playback
          ? `${PHASE_LABEL[playback.phase]} ${Math.min(playback.waypoint_index + 1, total)}/${total}`
          : `${total} 个点位`}
      </span>

      {playback?.error && (
        <span style={{ color: "var(--stop)", fontSize: 12 }} title={playback.error}>
          {playback.error}
        </span>
      )}
    </div>
  );
}
