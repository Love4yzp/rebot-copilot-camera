import { forwardRef, useImperativeHandle, useState } from "react";
import { api } from "../api";
import type { SeqTemplate, Sequence } from "../types";
import { Dialog } from "../components/Dialog";

export type SequenceDialogKind = "create" | "rename" | "delete" | "template";

export interface SequenceDialogsHandle {
  open: (kind: SequenceDialogKind) => void;
}

interface Props {
  sequence: Sequence | null;
  templates: SeqTemplate[];
  /** Toast helpers from the workspace, so dialogs report through one channel. */
  attempt: <T>(fn: () => Promise<T>, okMessage?: string) => Promise<T | undefined>;
  show: (kind: "info" | "error" | "success", message: string) => void;
  refreshLibrary: () => Promise<void>;
  /** Rename answers with the updated sequence; the workspace swaps it in. */
  applySequence: (updated: Sequence) => void;
  selectSequence: (id: string | null) => void;
  stopPreview: () => void;
  /** From-template creation hands off to the station wizard. */
  onWizard: (tpl: SeqTemplate) => void;
}

/**
 * The sequence menu dialogs (新建 / 改名 / 存为模板 / 删除) and their state
 * machine. Extracted from App.tsx so the workspace stops owning a second
 * mini-application; the workspace opens a dialog through the handle.
 */
export const SequenceDialogs = forwardRef<SequenceDialogsHandle, Props>(function SequenceDialogs(
  { sequence, templates, attempt, show, refreshLibrary, applySequence, selectSequence, stopPreview, onWizard },
  ref,
) {
  const [dialog, setDialog] = useState<SequenceDialogKind | null>(null);
  const [nameDraft, setNameDraft] = useState("");
  /** 新建序列的起点：空白 / 从模板（直接进逐站位向导）/ 复制现有。 */
  const [createStart, setCreateStart] = useState<"blank" | "tpl" | "copy">("blank");
  const [createTemplateId, setCreateTemplateId] = useState("");

  const open = (kind: SequenceDialogKind) => {
    setNameDraft(kind === "create" ? "" : (sequence?.name ?? ""));
    if (kind === "create") {
      setCreateStart("blank");
      setCreateTemplateId(templates[0]?.id ?? "");
    }
    setDialog(kind);
  };
  useImperativeHandle(ref, () => ({ open }), [sequence, templates]);

  const submitDialog = async () => {
    const name = nameDraft.trim();
    if (dialog === "create") {
      // 从模板：名称由向导接管，直接进入逐站位示教。
      if (createStart === "tpl") {
        const tpl = templates.find((t) => t.id === createTemplateId);
        if (tpl) {
          // 手上在臂上时监视器不该播模拟画面。
          stopPreview();
          onWizard(tpl);
          setDialog(null);
          return;
        }
      }
      if (name) {
        const created = await attempt(() => api.sequences.create(name));
        if (created) {
          if (createStart === "copy" && sequence) {
            await attempt(() => api.sequences.patch(created.id, { blocks: sequence.blocks }));
          }
          await refreshLibrary();
          selectSequence(created.id);
        }
      }
    } else if (dialog === "rename" && sequence && name && name !== sequence.name) {
      const updated = await attempt(() => api.sequences.patch(sequence.id, { name }));
      if (updated) {
        applySequence(updated);
        void refreshLibrary();
      }
    } else if (dialog === "delete" && sequence) {
      const removed = await attempt(async () => {
        await api.sequences.remove(sequence.id);
        return true;
      }, `已删除序列「${sequence.name}」`);
      if (removed) {
        setDialog(null);
        await refreshLibrary();
        selectSequence(null);
        return;
      }
    } else if (dialog === "template" && sequence) {
      const created = await attempt(() =>
        api.templates.create(sequence.id, name || sequence.name),
      );
      if (created) {
        show("success", `已存为模板「${created.name}」—— 复印即脱钩，两不相干`);
        void refreshLibrary();
      }
    }
    setDialog(null);
  };

  if (!dialog) return null;

  const label =
    dialog === "create"
      ? "新建序列"
      : dialog === "rename"
        ? "序列改名"
        : dialog === "template"
          ? "存为模板"
          : "删除序列";

  return (
    <Dialog label={label} onClose={() => setDialog(null)}>
      <div className="sheet__head">
        <h2 className="sheet__title">
          {dialog === "delete" ? `删除序列「${sequence?.name}」？` : label}
        </h2>
      </div>
      {dialog === "delete" ? (
        <p className="hint">序列删除不可撤销；它引用的位姿都保留在素材库里。</p>
      ) : dialog === "create" ? (
        <>
          {createStart !== "tpl" ? (
            <div className="sheet__field">
              <span className="sheet__label">名称</span>
              <input
                autoFocus
                value={nameDraft}
                onChange={(event) => setNameDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") void submitDialog();
                }}
              />
            </div>
          ) : null}
          <span className="sheet__label">从什么开始：</span>
          <div className="create-opts">
            <button
              type="button"
              className={createStart === "blank" ? "sel" : undefined}
              onClick={() => setCreateStart("blank")}
            >
              空白
            </button>
            <button
              type="button"
              className={createStart === "tpl" ? "sel" : undefined}
              disabled={templates.length === 0}
              onClick={() => setCreateStart("tpl")}
            >
              从模板
            </button>
            <button
              type="button"
              className={createStart === "copy" ? "sel" : undefined}
              disabled={!sequence}
              title={sequence ? "复制当前序列的结构与动作" : "先打开一条序列才能复制"}
              onClick={() => setCreateStart("copy")}
            >
              复制现有
            </button>
          </div>
          {createStart === "tpl" ? (
            <select
              value={createTemplateId}
              onChange={(event) => setCreateTemplateId(event.target.value)}
              aria-label="选择模板"
            >
              {templates.length === 0 ? (
                <option value="">（还没有模板 — 在「⋯」里存一个）</option>
              ) : (
                templates.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name} · {t.station_count} 站位
                  </option>
                ))
              )}
            </select>
          ) : null}
        </>
      ) : (
        <div className="sheet__field">
          <span className="sheet__label">名称</span>
          <input
            autoFocus
            value={nameDraft}
            onChange={(event) => setNameDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void submitDialog();
            }}
          />
        </div>
      )}
      {dialog === "template" ? (
        <p className="hint">模板只存结构（站位 / 时长 / 标记 / 过渡），不存关节角。</p>
      ) : null}
      <div className="sheet__actions">
        <button
          type="button"
          className={dialog === "delete" ? "danger primary" : "primary"}
          disabled={
            dialog === "delete"
              ? false
              : dialog === "create"
                ? createStart === "tpl"
                  ? templates.length === 0
                  : nameDraft.trim() === ""
                : nameDraft.trim() === ""
          }
          onClick={() => void submitDialog()}
        >
          {dialog === "delete" ? "确认删除" : createStart === "tpl" && dialog === "create" ? "开始向导" : "确定"}
        </button>
        <button type="button" className="ghost" onClick={() => setDialog(null)}>
          取消
        </button>
      </div>
    </Dialog>
  );
});
