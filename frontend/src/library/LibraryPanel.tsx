import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { Pose, PoseLinks, SeqTemplate, Sequence } from "../types";
import { sequenceDuration } from "../timeline/model";
import { Dialog } from "../components/Dialog";
import { useToast } from "../components/Toasts";

export const POSE_MIME = "application/x-rebot-pose";

interface Props {
  poses: Pose[];
  templates: SeqTemplate[];
  executing: boolean;
  latched: boolean;
  teaching: boolean;
  /** True when the sequence/pose API is not there (real backend before v2). */
  sequencesUnavailable: boolean;
  onGoto: (pose: Pose) => void;
  /** Anything in the library changed — the parent refetches. */
  onChanged: () => void;
  onSequenceCreated: (sequence: Sequence) => void;
  onSelectSequence: (id: string) => void;
  onTeach: () => void;
}

/**
 * The library: poses and templates, always on screen.
 *
 * The card board's 点哪去哪 did not die — it moved in here as 去这里. Poses
 * are *links* from every sequence that uses them, so deleting one first asks
 * the server who would be affected and says the names out loud before
 * anything is thrown away (先告知再动手).
 */
export function LibraryPanel({
  poses,
  templates,
  executing,
  latched,
  teaching,
  sequencesUnavailable,
  onGoto,
  onChanged,
  onSequenceCreated,
  onSelectSequence,
  onTeach,
}: Props) {
  const { attempt, show } = useToast();
  const [tab, setTab] = useState<"poses" | "templates">("poses");
  /** pose id → who links it, fetched once per library refresh. */
  const [links, setLinks] = useState<Record<string, PoseLinks>>({});
  const [expanded, setExpanded] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const [confirmDelete, setConfirmDelete] = useState<{ pose: Pose; links: PoseLinks } | null>(null);
  const [useTemplate, setUseTemplate] = useState<SeqTemplate | null>(null);

  const refreshLinks = useCallback(async () => {
    const entries = await Promise.all(
      poses.map(async (pose) => {
        try {
          return [pose.id, await api.poses.links(pose.id)] as const;
        } catch {
          return null;
        }
      }),
    );
    const map: Record<string, PoseLinks> = {};
    for (const entry of entries) if (entry) map[entry[0]] = entry[1];
    setLinks(map);
  }, [poses]);

  useEffect(() => {
    void refreshLinks();
  }, [refreshLinks]);

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
      <div className="lib__tabs" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "poses"}
          className={`lib__tab ${tab === "poses" ? "active" : ""}`}
          onClick={() => setTab("poses")}
        >
          位姿
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "templates"}
          className={`lib__tab ${tab === "templates" ? "active" : ""}`}
          onClick={() => setTab("templates")}
        >
          模板
        </button>
      </div>

      {sequencesUnavailable ? (
        <p className="lib__note">序列接口不可用（v2 后端未部署）—— 监视器、急停、日志仍可用。</p>
      ) : null}

      {tab === "poses" ? (
        <div className="lib__pane">
          <p className="lib__note">
            位姿被序列<b>链接</b>：保持块只存位姿名，不存关节角。拖到时间轴上排出站位；数字键 1–9 直达前九个位姿。
          </p>
          {poses.map((pose) => {
            const info = links[pose.id];
            return (
              <div
                key={pose.id}
                className="lib__pose"
                draggable={!executing}
                onDragStart={(event) => {
                  event.dataTransfer.setData(POSE_MIME, pose.id);
                  event.dataTransfer.effectAllowed = "copy";
                }}
              >
                <div className="lib__pose-top">
                  {renaming === pose.id ? (
                    <input
                      autoFocus
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
                    <button
                      type="button"
                      className="lib__pose-name"
                      title="点击改名"
                      onClick={() => {
                        setRenaming(pose.id);
                        setRenameDraft(pose.name);
                      }}
                    >
                      {pose.name}
                    </button>
                  )}
                  <button
                    type="button"
                    className="lib__chip"
                    onClick={() => setExpanded(expanded === pose.id ? null : pose.id)}
                  >
                    被 {info?.count ?? 0} 条序列链接
                  </button>
                </div>
                {expanded === pose.id && info ? (
                  <div className="lib__links">
                    {info.count === 0 ? (
                      <span className="hint">没有序列用到它</span>
                    ) : (
                      info.links.map((link) => (
                        <button
                          key={link.sequence_id}
                          type="button"
                          className="lib__link"
                          onClick={() => onSelectSequence(link.sequence_id)}
                        >
                          {link.sequence_name}（{link.block_count} 块）
                        </button>
                      ))
                    )}
                  </div>
                ) : null}
                <div className="lib__pose-actions">
                  <button type="button" onClick={() => onGoto(pose)}>
                    去这里
                  </button>
                  <button
                    type="button"
                    className="ghost"
                    disabled={executing}
                    onClick={() => void removePose(pose)}
                  >
                    删除
                  </button>
                </div>
              </div>
            );
          })}
          <button
            type="button"
            className="lib__record"
            disabled={latched || executing || teaching}
            onClick={onTeach}
          >
            + 录位姿
          </button>
        </div>
      ) : (
        <div className="lib__pane">
          <p className="lib__note">
            模板 = 结构配方（站位数 / 时长 / 标记配方 / 过渡参数），<b>不含关节角</b>。
            「用它」生成一条脱钩的普通序列 —— 之后改模板、删模板，已生成的序列纹丝不动。
          </p>
          {templates.length === 0 ? (
            <p className="hint">还没有模板。在顶栏序列菜单里「存为模板」。</p>
          ) : (
            templates.map((template) => (
              <div key={template.id} className="lib__tpl">
                <div className="lib__tpl-name">{template.name}</div>
                <div className="lib__tpl-desc">
                  {template.station_count} 站位 · 预估{" "}
                  <span className="num">{sequenceDuration(template.recipe).toFixed(1)}s</span>
                </div>
                <div className="lib__pose-actions">
                  <button type="button" onClick={() => setUseTemplate(template)}>
                    用它
                  </button>
                  <button
                    type="button"
                    className="ghost"
                    onClick={() =>
                      void attempt(async () => {
                        await api.templates.remove(template.id);
                      }, `已删除模板「${template.name}」`).then(onChanged)
                    }
                  >
                    删除
                  </button>
                </div>
              </div>
            ))
          )}
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

      {useTemplate ? (
        <InstantiateDialog
          template={useTemplate}
          poses={poses}
          onClose={() => setUseTemplate(null)}
          onCreated={(sequence) => {
            setUseTemplate(null);
            onSequenceCreated(sequence);
          }}
        />
      ) : null}
    </aside>
  );
}

/** Bind every slot of a recipe to a library pose, generating a detached sequence. */
function InstantiateDialog({
  template,
  poses,
  onClose,
  onCreated,
}: {
  template: SeqTemplate;
  poses: Pose[];
  onClose: () => void;
  onCreated: (sequence: Sequence) => void;
}) {
  const { attempt } = useToast();
  const [name, setName] = useState(`${template.name} · 副本`);
  const [slots, setSlots] = useState<string[]>(
    Array.from({ length: template.station_count }, (_, i) => poses[i % Math.max(poses.length, 1)]?.id ?? ""),
  );
  const ready = name.trim().length > 0 && slots.every((id) => id !== "");

  const create = async () => {
    const sequence = await attempt(() =>
      api.templates.instantiate(template.id, { name: name.trim(), pose_ids: slots }),
    );
    if (sequence) onCreated(sequence);
  };

  return (
    <Dialog label="用模板" onClose={onClose}>
      <div className="sheet__head">
        <h2 className="sheet__title">用它：{template.name}</h2>
      </div>
      <p className="hint">逐槽位选一个已有位姿。复印即脱钩：生成的是普通序列，与模板两不相干。</p>
      <div className="sheet__field">
        <span className="sheet__label">序列名</span>
        <input value={name} onChange={(event) => setName(event.target.value)} />
      </div>
      {slots.map((poseId, i) => (
        <div className="sheet__field" key={i}>
          <span className="sheet__label">站位 {i + 1}</span>
          <select
            value={poseId}
            onChange={(event) =>
              setSlots((current) => current.map((v, j) => (j === i ? event.target.value : v)))
            }
          >
            {poses.map((pose) => (
              <option key={pose.id} value={pose.id}>
                {pose.name}
              </option>
            ))}
          </select>
        </div>
      ))}
      <div className="sheet__actions">
        <button type="button" className="primary" disabled={!ready} onClick={() => void create()}>
          生成序列
        </button>
      </div>
    </Dialog>
  );
}
