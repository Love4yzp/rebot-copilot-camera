import { useMemo } from "react";
import type { Block, ControlState, Pose, SeqPlayback } from "../types";
import { ArmView3D } from "../components/ArmView3D";
import { blockIndexAt } from "../timeline/model";
import type { PreviewApi } from "../preview/usePreview";

interface Props {
  state: ControlState | null;
  playback: SeqPlayback | null;
  preview: PreviewApi;
  /** Blocks of the sequence on the bench, for the current-block line. */
  blocks: Block[];
  poseName: (id: string) => string;
  sequenceName: string | null;
  /** The open sequence is the thing running — only then is a block "current". */
  runningSequence: boolean;
  /** Rest: zero torque, the arm lying on its stops. */
  resting?: boolean;
  onToggleRest?: () => void;
  /** The pose library, drawn as ghost arms where they live in space. */
  poses: Pose[];
  /** Ghost tap — routes to the exact same goto as a card tap. */
  onGhostClick?: (pose: Pose) => void;
  /** Toggle the tuning panel. */
  onToggleTuning?: () => void;
  tuningOpen?: boolean;
}

function blockLabel(block: Block | undefined, poseName: (id: string) => string): string {
  if (!block) return "—";
  return block.type === "hold" ? `保持「${poseName(block.pose_id)}」` : "过渡";
}

const EASING_LABEL: Record<string, string> = {
  linear: "线性",
  ease_in: "缓入",
  ease_out: "缓出",
  ease_in_out: "缓入缓出",
};

/**
 * The monitor: one full-size 3D view, two mutually exclusive feeds.
 *
 * Preview and live are never on screen together — the whole panel flips as
 * one. During a preview it plays the simulated plan pose (grey, bannered
 * 预演中·臂未动, and labelled a simulation: the plan path is a joint-space
 * interpolation, close to but not guaranteed to be the arm's real path);
 * executing or idle it shows the arm's live pose. "Is this the simulation or
 * is it real" needs no tag — position is the semantics.
 */
export function MonitorPanel({
  state,
  playback,
  preview,
  blocks,
  poseName,
  sequenceName,
  runningSequence,
  resting,
  onToggleRest,
  poses,
  onGhostClick,
  onToggleTuning,
  tuningOpen,
}: Props) {
  const latched = state?.estop.latched ?? false;
  const executing = state?.mode === "playback" && !latched;
  const teaching = state?.mode === "teach";

  /** Ghost list is derived and referentially stable — the viewer's load
   * effect must not re-run on every 20 Hz broadcast. */
  const ghostPoses = useMemo(
    () => poses.map((p) => ({ id: p.id, name: p.name, joints: p.joints })),
    [poses],
  );

  /**
   * The pose the playhead is at — the ghost to highlight. During execution
   * the highlight may be amber (the arm really is moving there); during
   * preview it stays greyscale, because preview is not a machine state.
   */
  const targetPoseId: string | null = executing && runningSequence && playback
    ? (() => {
        const block = blocks[Math.min(playback.block_index, blocks.length - 1)];
        return block?.type === "hold" ? block.pose_id : null;
      })()
    : preview.active && preview.playing
      ? (() => {
          const block = blocks[blockIndexAt(blocks, preview.t)];
          return block?.type === "hold" ? block.pose_id : null;
        })()
      : null;

  const bannerState = latched
    ? "estop"
    : executing
      ? "exec"
      : teaching
        ? "teach"
        : resting
          ? "rest"
          : preview.active
            ? "preview"
            : "idle";

  let banner: string;
  let status: string;
  let sub: string;
  if (latched) {
    banner = "已急停";
    status = "已急停 · 臂钉在原地";
    sub = "解除急停后原地待命，不会自动继续";
  } else if (resting) {
    banner = "休息中 · 已卸力";
    status = "电机已卸力 · 臂搁在止点上";
    sub = "点「唤醒」或任何位姿卡自动恢复保持 · 急停随时有效";
  } else if (teaching) {
    banner = "零重力 · 臂可推动";
    status = "零重力 · 已卸力";
    sub = "松手自动锁定 · 直接保存";
  } else if (executing) {
    banner = "执行中 · 臂在动";
    status = "实况 · 臂在动";
    if (playback?.phase === "wait") {
      sub = "等待标记 · 执行暂停，点「继续」";
    } else if (playback && runningSequence) {
      const index = Math.min(playback.block_index, blocks.length - 1);
      const block = blocks[index];
      const label =
        block?.type === "transition" ? `过渡 · ${EASING_LABEL[block.easing]}` : blockLabel(block, poseName);
      sub = `${playback.sequence_name} · 当前：${label}（真实进度）`;
    } else if (playback) {
      // A single-pose goto: the run belongs to a pose, not to this sequence —
      // there is no block on this ruler to call current.
      sub = `${playback.sequence_name} · 移动中（真实进度）`;
    } else {
      sub = "（真实进度）";
    }
  } else if (preview.active) {
    banner = "预演中 · 臂未动";
    status = "预演回放 · 模拟姿态";
    if (preview.waiting) {
      sub = "等待标记 · 预演暂停，点「继续」";
    } else if (preview.playing) {
      const block = blocks[blockIndexAt(blocks, preview.t)];
      const label =
        block?.type === "transition" ? `过渡 · ${EASING_LABEL[block.easing]}` : blockLabel(block, poseName);
      sub = `当前：${label}（模拟 · 计划路径，真臂路径以实际执行为准）`;
    } else {
      sub = "预演已暂停 · 模拟姿态";
    }
  } else {
    banner = "";
    status = "实况 · 臂静止";
    sub = sequenceName ?? "";
  }

  return (
    <section className="monitor" data-state={bannerState} aria-label="监视器">
      <div className="monitor__banner" hidden={!banner}>
        {banner}
      </div>
      <div className="monitor__status">{status}</div>
      <div className="monitor__sub">{sub}</div>
      <div className="monitor__tuning-btn">
        {onToggleRest ? (
          <button type="button" className="ghost" onClick={onToggleRest}>
            {resting ? "唤醒" : "休息"}
          </button>
        ) : null}
        {onToggleTuning ? (
          <button type="button" className="ghost" onClick={onToggleTuning}>
            {tuningOpen ? "关闭调参" : "调参"}
          </button>
        ) : null}
      </div>
      <div className="monitor__view">
        <ArmView3D
          positions={state?.positions ?? {}}
          preview={preview.pose}
          ghosts={ghostPoses}
          targetPoseId={targetPoseId}
          targetAmber={executing}
          onGhostClick={(ghost) => {
            const pose = poses.find((p) => p.id === ghost.id);
            if (pose) onGhostClick?.(pose);
          }}
        />
      </div>
    </section>
  );
}
