import { useState } from "react";
import { api } from "../api";
import type { Pose, PoseLinks } from "../types";
import { Dialog } from "../components/Dialog";
import { useToast } from "../components/Toasts";

export const POSE_MIME = "application/x-rebot-pose";

interface Props {
  poses: Pose[];
  executing: boolean;
  latched: boolean;
  teaching: boolean;
  /** The template wizard owns the footer — no new flows while it runs. */
  wizardOpen: boolean;
  /** True when the sequence/pose API is not there (real backend before v2). */
  sequencesUnavailable: boolean;
  /** A sequence is open and not running — pose cards may offer 「＋追加」. */
  canAppend: boolean;
  /** Name of the open sequence — the 追加 target, stated once, in one place. */
  appendTarget: string | null;
  /** Append the pose as a new station at the open sequence's tail. */
  onAppendPose: (pose: Pose) => void;
  onGoto: (pose: Pose) => void;
  /** Anything in the library changed — the parent refetches. */
  onChanged: () => void;
  onTeach: () => void;
}

/**
 * The library: poses, always on screen.
 *
 * The card IS the destination — tapping it sends the arm there (the same goto
 * as the ghost arms in the monitor); there is no 「去这里」 label because the
 * label needed a subject ("who goes?") and the card is the answer. The only
 * always-visible button is ＋追加; rename and delete live in the ⋯ menu.
 * Poses are *links* from every sequence that uses them, so deleting one first
 * asks the server who would be affected and says the names out loud before
 * anything is thrown away (先告知再动手).
 */
export function LibraryPanel({
  poses,
  executing,
  latched,
  teaching,
  wizardOpen,
  sequencesUnavailable,
  canAppend,
  appendTarget,
  onAppendPose,
  onGoto,
  onChanged,
  onTeach,
}: Props) {
  const { attempt, show } = useToast();
  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const [confirmDelete, setConfirmDelete] = useState<{ pose: Pose; links: PoseLinks } | null>(null);
  const [collapsed, setCollapsed] = useState(false);

  const rename = async (pose: Pose) => {
    const name = renameDraft.trim();
    setRenaming(null);
    if (!name || name === pose.name) return;
    if (await attempt(() => api.poses.patch(pose.id, { name }))) onChanged();
  };

  const removePose = async (pose: Pose) => {
    // Tell first, then act: silently rewriting the physical path of every
    // sequence that links this pose is the empty-frames class of failure.
    const info = await attempt(() => api.poses.links(pose.id));
    if (!info) return;
    if (info.count === 0) {
      // DELETE answers 204 — `attempt` surfaces that as undefined, so flag it.
      const removed = await attempt(async () => {
        await api.poses.remove(pose.id);
        return true;
      }, `已删除位姿「${pose.name}」`);
      if (removed) onChanged();
      return;
    }
    setConfirmDelete({ pose, links: info });
  };

  const confirmRemove = async () => {
    if (!confirmDelete) return;
    const { pose } = confirmDelete;
    setConfirmDelete(null);
    // DELETE returns 204, which `attempt` surfaces as undefined — check the
    // failure path instead of the success value.
    try {
      await api.poses.remove(pose.id);
      show("info", `已删除位姿「${pose.name}」—— 引用它的保持块会保持臂当时姿态`);
      onChanged();
    } catch (error) {
      show("error", error instanceof Error ? error.message : String(error));
    }
  };

  return (
    <aside className="lib" aria-label="素材库">
      <div className="lib__head">
        <div className="lib__title">位姿</div>
        <button
          type="button"
          className="lib__collapse"
          onClick={() => setCollapsed((v) => !v)}
          aria-label={collapsed ? "展开素材库" : "收起素材库"}
        >
          {collapsed ? "展开" : "收起"}
        </button>
      </div>

      {sequencesUnavailable ? (
        <p className="lib__note">序列接口不可用（v2 后端未部署）—— 监视器、急停、日志仍可用。</p>
      ) : null}

      {collapsed ? null : (
        <div className="lib__pane">
          {canAppend && appendTarget ? (
            <p className="lib__context">
              ＋追加 → 排到 <b>{appendTarget}</b> 末尾
            </p>
          ) : null}
          {poses.length === 0 ? (
            <div className="lib__empty">
              <p className="lib__empty-title">还没有位姿</p>
              <p className="hint">
                点下方「+ 录位姿」—— 手掰臂到想要的姿态、松手、点保存。
                录完它出现在这里，再「＋追加」进序列。
              </p>
            </div>
          ) : null}
          {poses.map((pose) => (
            <div
              key={pose.id}
              className="lib__pose"
              title="点击 → 臂开过去"
              draggable={!executing}
              onClick={() => onGoto(pose)}
              onDragStart={(event) => {
                event.dataTransfer.setData(POSE_MIME, pose.id);
                event.dataTransfer.effectAllowed = "copy";
              }}
            >
              <div className="lib__pose-top">
                {renaming === pose.id ? (
                  <input
                    autoFocus
                    onClick={(event) => event.stopPropagation()}
                    value={renameDraft}
                    onChange={(event) => setRenameDraft(event.target.value)}
                    onBlur={() => void rename(pose)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") void rename(pose);
                      if (event.key === "Escape") setRenaming(null);
                    }}
                    aria-label="位姿名称"
                  />
                ) : (
                  <span className="lib__pose-name">{pose.name}</span>
                )}
              </div>
              <div className="lib__pose-actions">
                <button
                  type="button"
                  disabled={!canAppend || executing || teaching || wizardOpen}
                  title={canAppend ? "排到序列末尾，成为一个新站位" : "先在顶栏打开一条序列"}
                  onClick={(event) => {
                    event.stopPropagation();
                    onAppendPose(pose);
                  }}
                >
                  ＋追加
                </button>
                <button
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    setRenaming(pose.id);
                    setRenameDraft(pose.name);
                  }}
                >
                  改名
                </button>
                <button
                  type="button"
                  className="ghost"
                  onClick={(event) => {
                    event.stopPropagation();
                    void removePose(pose);
                  }}
                >
                  删除
                </button>
              </div>
            </div>
          ))}
          <button
            type="button"
            className="lib__record"
            disabled={latched || executing || teaching || wizardOpen}
            onClick={onTeach}
          >
            + 录位姿
          </button>
        </div>
      )}

      {confirmDelete ? (
        <Dialog label="删除位姿" onClose={() => setConfirmDelete(null)}>
          <div className="sheet__head">
            <h2 className="sheet__title">删除位姿「{confirmDelete.pose.name}」？</h2>
          </div>
          <p className="hint">
            正被 {confirmDelete.links.count} 条序列用着。删除后这些序列的对应保持块会失去位姿
            —— 执行到那里时臂保持当时姿态，不报错。
          </p>
          <ul className="lib__affected">
            {confirmDelete.links.links.map((link) => (
              <li key={link.sequence_id}>
                {link.sequence_name}（{link.block_count} 个保持块）
              </li>
            ))}
          </ul>
          <div className="sheet__actions">
            <button type="button" className="danger primary" onClick={() => void confirmRemove()}>
              确认删除
            </button>
            <button type="button" className="ghost" onClick={() => setConfirmDelete(null)}>
              取消
            </button>
          </div>
        </Dialog>
      ) : null}
    </aside>
  );
}
