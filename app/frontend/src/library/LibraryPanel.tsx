import { useEffect, useState } from "react";
import { api } from "../api";
import type { Pose, PoseLinks, SeqTemplate } from "../types";
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
  /** Sequence tape is running — the card dims and goto is refused. */
  gotoLocked: boolean;
  /** A sequence is open and not running — pose cards may offer 「＋追加」. */
  canAppend: boolean;
  /** Tap-to-go face: the 追加 button stays hidden (no sequence on screen). */
  hideAppend?: boolean;
  /** Template material for the edit face — absent in tap-to-go, so no tab bar. */
  templates?: SeqTemplate[] | null;
  /** Instantiate a template through the station wizard. */
  onUseTemplate?: (template: SeqTemplate) => void;
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
 * The card IS the destination — the name is the button, same goto as the
 * monitor ghosts. Rename and delete live in the ⋯ menu. 「＋追加」 is the
 * only extra verb, and only while a sequence can take a station.
 */
export function LibraryPanel({
  poses,
  executing,
  latched,
  teaching,
  gotoLocked,
  wizardOpen,
  sequencesUnavailable,
  canAppend,
  hideAppend,
  templates,
  onUseTemplate,
  onAppendPose,
  onGoto,
  onChanged,
  onTeach,
}: Props) {
  const { attempt, show } = useToast();
  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<{ pose: Pose; links: PoseLinks } | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [tab, setTab] = useState<"poses" | "templates">("poses");

  useEffect(() => {
    if (!menuFor) return;
    const close = () => setMenuFor(null);
    window.addEventListener("pointerdown", close);
    return () => window.removeEventListener("pointerdown", close);
  }, [menuFor]);

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
      show("info", `已删除位姿「${pose.name}」`);
      onChanged();
    } catch (error) {
      show("error", error instanceof Error ? error.message : String(error));
    }
  };

  return (
    <aside className="lib" aria-label="素材库">
      <div className="lib__head">
        {onUseTemplate ? (
          <div className="lib__sub-tabs" role="tablist" aria-label="素材类型">
            <button
              type="button"
              role="tab"
              aria-selected={tab === "poses"}
              onClick={() => setTab("poses")}
            >
              位姿
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === "templates"}
              onClick={() => setTab("templates")}
            >
              模板
            </button>
          </div>
        ) : (
          <div className="lib__title">位姿</div>
        )}
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

      {collapsed ? null : onUseTemplate && tab === "templates" ? (
        <div className="lib__pane">
          {templates && templates.length > 0 ? (
            templates.map((tpl) => (
              <div key={tpl.id} className="lib__tpl">
                <div className="lib__tpl-name">{tpl.name}</div>
                <div className="lib__tpl-desc">
                  {tpl.station_count} 站位 · 只存结构，不存关节角
                </div>
                <button
                  type="button"
                  className="lib__tpl-use"
                  onClick={() => onUseTemplate(tpl)}
                >
                  用它 → 逐站位绑定位姿
                </button>
              </div>
            ))
          ) : (
            <div className="lib__empty">还没有模板</div>
          )}
        </div>
      ) : (
        <div className={`lib__pane ${poses.length === 0 ? "lib__pane--empty" : ""}`}>
          {poses.map((pose) => {
            const open = menuFor === pose.id;
            const showAppend = !hideAppend && canAppend;
            return (
              <div
                key={pose.id}
                className={`lib__pose${gotoLocked ? " lib__pose--locked" : ""}`}
                draggable={!executing && !teaching}
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
                {showAppend ? (
                  <button
                    type="button"
                    className="lib__append"
                    disabled={executing || teaching || wizardOpen}
                    onClick={(event) => {
                      event.stopPropagation();
                      onAppendPose(pose);
                    }}
                  >
                    ＋追加
                  </button>
                ) : null}
                <button
                  type="button"
                  className="lib__more"
                  aria-label="更多"
                  aria-expanded={open}
                  onPointerDown={(event) => event.stopPropagation()}
                  onClick={(event) => {
                    event.stopPropagation();
                    setMenuFor(open ? null : pose.id);
                  }}
                >
                  ⋯
                </button>
                {open ? (
                  <div
                    className="lib__menu"
                    onPointerDown={(event) => event.stopPropagation()}
                    onClick={(event) => event.stopPropagation()}
                  >
                    <button
                      type="button"
                      onClick={() => {
                        setMenuFor(null);
                        setRenaming(pose.id);
                        setRenameDraft(pose.name);
                      }}
                    >
                      改名
                    </button>
                    <button
                      type="button"
                      className="danger"
                      onClick={() => {
                        setMenuFor(null);
                        void removePose(pose);
                      }}
                    >
                      删除
                    </button>
                  </div>
                ) : null}
              </div>
            );
          })}
          <button
            type="button"
            className={poses.length === 0 ? "lib__record primary" : "lib__record ghost"}
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
            正被 {confirmDelete.links.count} 条序列用着。删掉后那些序列执行前会被拒绝。
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
