import { useRef, useState } from "react";
import { api } from "../api";
import type { Routine, RoutineSummary } from "../types";
import { useToast } from "./Toasts";

interface Props {
  routines: RoutineSummary[];
  selectedId: string | null;
  /** 配置模式下，选中集合旁出现「重命名」「删除」。 */
  config: boolean;
  /** 新建菜单由 App 持有，好让空状态里的按钮也能把它打开。 */
  menuOpen: boolean;
  onMenuOpen: (open: boolean) => void;
  onSelect: (id: string) => void;
  onChanged: () => void;
  /** 新建后把集合交给向导：blank 从空开始，four 走四方位。 */
  onStartWizard: (kind: "blank" | "four", routine: Routine) => void;
}

/**
 * 横向集合切换条。每个集合一个 chip（名字 + 锚点数），末尾「+ 新建」展开
 * 内联菜单（空白集合 / 四方位向导）。配置模式下选中集合可内联重命名、
 * 二次确认删除 —— 全程内联表单，不用 prompt/confirm。
 */
export function CollectionBar({
  routines,
  selectedId,
  config,
  menuOpen,
  onMenuOpen,
  onSelect,
  onChanged,
  onStartWizard,
}: Props) {
  const { attempt, show } = useToast();
  const [renaming, setRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  // Esc 取消重命名后会触发 blur，用这个标记挡住 blur 的提交。
  const renameCancelled = useRef(false);

  const selected = routines.find((r) => r.id === selectedId) ?? null;

  const create = async (kind: "blank" | "four") => {
    onMenuOpen(false);
    const routine = await attempt(() => api.routines.create(kind === "four" ? "四方位" : "新集合"));
    if (routine) {
      onChanged();
      onStartWizard(kind, routine);
    }
  };

  const submitRename = async () => {
    if (!selected) return;
    const name = renameValue.trim();
    setRenaming(false);
    if (!name || name === selected.name) return;
    if (await attempt(() => api.routines.rename(selected.id, name))) onChanged();
  };

  const remove = async () => {
    if (!selected) return;
    setConfirmingDelete(false);
    // 集合是手把手示教出来的：每一个都代表有人站在臂旁拖过一遍，删除必须二次确认。
    // remove 返回 void，用哨兵值区分成功和失败。
    const ok = await attempt(async () => {
      await api.routines.remove(selected.id);
      return true;
    });
    if (ok) {
      show("info", `已删除「${selected.name}」`);
      onChanged();
    }
  };

  return (
    <div className="collection-bar">
      <div className="collection-chips" role="tablist" aria-label="集合">
        {routines.map((routine) => (
          <button
            key={routine.id}
            role="tab"
            aria-selected={routine.id === selectedId}
            className={`collection-chip${routine.id === selectedId ? " selected" : ""}`}
            onClick={() => {
              onSelect(routine.id);
              setRenaming(false);
              setConfirmingDelete(false);
            }}
          >
            {routine.name}
            <span className="count">{routine.waypoint_count}</span>
          </button>
        ))}
        <button
          className="collection-chip add"
          aria-expanded={menuOpen}
          onClick={() => {
            onMenuOpen(!menuOpen);
            setRenaming(false);
            setConfirmingDelete(false);
          }}
        >
          + 新建
        </button>
      </div>

      {menuOpen && (
        <div className="collection-create">
          <button className="primary" onClick={() => void create("blank")}>
            空白集合
            <span className="hint">自己一个个录锚点</span>
          </button>
          <button onClick={() => void create("four")}>
            四方位向导
            <span className="hint">正面 / 右 45° / 侧面 / 俯拍，跟着向导录</span>
          </button>
          <button className="ghost" onClick={() => onMenuOpen(false)}>
            取消
          </button>
        </div>
      )}

      {config && selected && !menuOpen && (
        <div className="collection-tools">
          {renaming ? (
            <form
              className="inline-form"
              onSubmit={(event) => {
                event.preventDefault();
                void submitRename();
              }}
            >
              <input
                autoFocus
                value={renameValue}
                onChange={(event) => setRenameValue(event.target.value)}
                placeholder="集合名称"
                onBlur={() => {
                  if (renameCancelled.current) {
                    renameCancelled.current = false;
                    return;
                  }
                  void submitRename();
                }}
                onKeyDown={(event) => {
                  if (event.key === "Escape") {
                    renameCancelled.current = true;
                    setRenaming(false);
                  }
                }}
              />
              <button type="submit" className="primary">
                保存
              </button>
              <button type="button" className="ghost" onClick={() => setRenaming(false)}>
                取消
              </button>
            </form>
          ) : confirmingDelete ? (
            <span className="inline-form">
              <span className="hint">确认删除「{selected.name}」？{selected.waypoint_count} 个锚点会一起没。</span>
              <button className="danger" onClick={() => void remove()}>
                确认删除
              </button>
              <button className="ghost" onClick={() => setConfirmingDelete(false)}>
                取消
              </button>
            </span>
          ) : (
            <>
              <button
                className="ghost"
                onClick={() => {
                  setRenameValue(selected.name);
                  setRenaming(true);
                  setConfirmingDelete(false);
                }}
              >
                重命名
              </button>
              <button className="ghost" onClick={() => setConfirmingDelete(true)}>
                删除集合
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
}
