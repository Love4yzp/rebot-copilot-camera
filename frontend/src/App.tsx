import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "./api";
import type { Block, Pose, ProviderInfo, SeqTemplate, Sequence, SequenceSummary } from "./types";
import { useControlSocket } from "./useControlSocket";
import { usePreview } from "./preview/usePreview";
import { markerSchedule, playbackAbsTime, sequenceDuration } from "./timeline/model";
import { EstopBar } from "./components/EstopBar";
import { LogDrawer } from "./components/LogDrawer";
import { TallyRail } from "./components/TallyRail";
import type { TallyState } from "./components/TallyRail";
import { Dialog } from "./components/Dialog";
import { ToastProvider, useToast } from "./components/Toasts";
import { LibraryPanel } from "./library/LibraryPanel";
import { TeachBar } from "./library/TeachBar";
import { MonitorPanel } from "./monitor/MonitorPanel";
import { TimelineView } from "./timeline/TimelineView";
import type { Selection } from "./timeline/TimelineView";
import { Inspector } from "./timeline/Inspector";
import { TransportBar } from "./transport/TransportBar";

/** Which sequence was open last, so a reload does not cost a tap. */
const LAST_SEQUENCE_KEY = "rebot:last-sequence";

/** A shutter marker reads as a white flash for this long after crossing. */
const SHUTTER_FLASH_S = 0.5;

function Workspace() {
  const { state, playback, connected } = useControlSocket();
  const { attempt, show } = useToast();

  const [poses, setPoses] = useState<Pose[]>([]);
  const [summaries, setSummaries] = useState<SequenceSummary[]>([]);
  const [templates, setTemplates] = useState<SeqTemplate[]>([]);
  /** True when the v2 sequence API is not deployed (real backend, transition). */
  const [sequencesUnavailable, setSequencesUnavailable] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [sequence, setSequence] = useState<Sequence | null>(null);
  const [selection, setSelection] = useState<Selection>(null);
  const [teachOpen, setTeachOpen] = useState(false);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [dialog, setDialog] = useState<"create" | "rename" | "delete" | "template" | null>(null);
  const [nameDraft, setNameDraft] = useState("");

  const blocks = useMemo<Block[]>(() => sequence?.blocks ?? [], [sequence]);
  const poseMap = useMemo(
    () => Object.fromEntries(poses.map((p) => [p.id, p.joints])),
    [poses],
  );
  const poseName = useCallback(
    (id: string) => poses.find((p) => p.id === id)?.name ?? "已删除位姿",
    [poses],
  );

  const preview = usePreview(blocks, poseMap);

  const mode = state?.mode ?? null;
  const latched = state?.estop.latched ?? false;
  const executing = mode === "playback" && !latched;
  const teaching = mode === "teach";
  const total = sequenceDuration(blocks);

  // ── data loading ──────────────────────────────────────────────────────────

  const refreshLibrary = useCallback(async () => {
    // The pose/template lists ride along; the sequence list is the one that
    // may not exist yet against the real backend, and it must fail soft —
    // monitor, estop and logs keep working either way.
    try {
      const list = await api.sequences.list();
      setSummaries(list);
      setSequencesUnavailable(false);
    } catch {
      setSummaries([]);
      setSequencesUnavailable(true);
    }
    try {
      setPoses(await api.poses.list());
    } catch {
      setPoses([]);
    }
    try {
      setTemplates(await api.templates.list());
    } catch {
      setTemplates([]);
    }
  }, []);

  useEffect(() => {
    void refreshLibrary();
  }, [refreshLibrary]);

  // A provider list that fails to load must not take the bench with it.
  useEffect(() => {
    api.plugins
      .list()
      .then(setProviders)
      .catch(() => setProviders([]));
  }, []);

  // Land on something usable: the sequence that was open last, else the first.
  useEffect(() => {
    if (selectedId !== null || summaries.length === 0) return;
    const remembered = localStorage.getItem(LAST_SEQUENCE_KEY);
    const wanted = summaries.find((s) => s.id === remembered) ?? summaries[0];
    setSelectedId(wanted.id);
  }, [summaries, selectedId]);

  useEffect(() => {
    if (!selectedId) {
      setSequence(null);
      return;
    }
    localStorage.setItem(LAST_SEQUENCE_KEY, selectedId);
    api
      .sequences
      .get(selectedId)
      .then(setSequence)
      .catch(() => setSequence(null));
  }, [selectedId]);

  // Switching sequences closes whatever pointed into the old one.
  const selectSequence = useCallback(
    (id: string | null) => {
      setSelectedId(id);
      setSelection(null);
      preview.stop();
    },
    [preview],
  );

  // A reload can drop the block/marker the inspector was editing.
  useEffect(() => {
    if (!selection || !sequence) return;
    if (selection.kind === "block" && !sequence.blocks.some((b) => b.id === selection.id)) {
      setSelection(null);
    } else if (
      selection.kind === "marker" &&
      !sequence.blocks.some(
        (b) => b.id === selection.blockId && b.markers.some((m) => m.id === selection.markerId),
      )
    ) {
      setSelection(null);
    }
  }, [sequence, selection]);

  // ── machine-state effects ─────────────────────────────────────────────────

  // The estop force-closes teach (unmounting exits teach mode) and stops the
  // preview — one button stops everything on screen, simulation included.
  useEffect(() => {
    if (latched) {
      setTeachOpen(false);
      preview.stop();
    }
  }, [latched, preview]);

  // ── verbs ─────────────────────────────────────────────────────────────────

  const patchBlocks = useCallback(
    async (next: Block[]) => {
      if (!selectedId) return;
      const updated = await attempt(() => api.sequences.patch(selectedId, { blocks: next }));
      if (updated) {
        setSequence(updated);
        void refreshLibrary();
      }
    },
    [selectedId, attempt, refreshLibrary],
  );

  const execute = useCallback(async () => {
    if (!sequence) return;
    if (sequence.blocks.length === 0) {
      show("info", "空序列 — 先从素材库拖位姿上轴");
      return;
    }
    // 点执行 = 停预演进执行。
    preview.stop();
    try {
      await api.sequences.execute(sequence.id);
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        show("info", latched ? "已急停 — 先解除急停" : "臂正忙 — 等当前动作完成");
      } else {
        show("error", error instanceof Error ? error.message : String(error));
      }
    }
  }, [sequence, preview, show, latched]);

  const stop = useCallback(() => {
    if (preview.active) preview.stop();
    if (executing) void attempt(() => api.execute.stop(), "已停止");
  }, [preview, executing, attempt]);

  const resume = useCallback(() => {
    if (preview.waiting) preview.continueWait();
    else void attempt(() => api.execute.resume());
  }, [preview, attempt]);

  const gotoPose = useCallback(
    async (pose: Pose) => {
      if (latched) {
        show("info", "已急停 — 先解除急停");
        return;
      }
      try {
        await api.poses.goto(pose.id);
      } catch (error) {
        if (error instanceof ApiError && error.status === 409) {
          show("info", executing ? "执行中 — 等当前序列完成" : "臂正忙 — 等当前动作完成");
        } else {
          show("error", error instanceof Error ? error.message : String(error));
        }
      }
    },
    [latched, executing, show],
  );

  // Number keys fire the first nine poses: the operator's hands are usually
  // on the camera or the arm, and the same binding takes a foot pedal.
  const posesRef = useRef(poses);
  posesRef.current = poses;
  const gotoRef = useRef(gotoPose);
  gotoRef.current = gotoPose;
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const node = event.target as HTMLElement | null;
      if (node && /^(INPUT|TEXTAREA|SELECT)$/.test(node.tagName)) return;
      if (node?.closest("[role='dialog']")) return;
      const digit = Number(event.key);
      if (!Number.isInteger(digit) || digit < 1 || digit > 9) return;
      const pose = posesRef.current[digit - 1];
      if (!pose) return;
      event.preventDefault();
      void gotoRef.current(pose);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // ── sequence menu dialogs ─────────────────────────────────────────────────

  const openDialog = (kind: NonNullable<typeof dialog>) => {
    setNameDraft(kind === "create" ? "" : (sequence?.name ?? ""));
    setDialog(kind);
  };

  const submitDialog = async () => {
    const name = nameDraft.trim();
    if (dialog === "create" && name) {
      const created = await attempt(() => api.sequences.create(name));
      if (created) {
        await refreshLibrary();
        selectSequence(created.id);
      }
    } else if (dialog === "rename" && sequence && name && name !== sequence.name) {
      const updated = await attempt(() => api.sequences.patch(sequence.id, { name }));
      if (updated) {
        setSequence(updated);
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

  // ── display state ─────────────────────────────────────────────────────────

  const clockT = executing && playback ? playbackAbsTime(blocks, playback) : preview.active ? preview.t : 0;

  // A shutter marker crossed in the last half-second reads as one white frame.
  const shutterJustFired = useMemo(() => {
    if (!executing || !playback || blocks.length === 0) return false;
    const t = playbackAbsTime(blocks, playback);
    return markerSchedule(blocks).some(
      (s) => s.marker.kind === "shutter" && s.t <= t && t - s.t < SHUTTER_FLASH_S,
    );
  }, [executing, blocks, playback]);

  const tally: TallyState = latched
    ? "latched"
    : teaching
      ? "teach"
      : executing
        ? shutterJustFired
          ? "acting"
          : "moving"
        : playback?.finished && playback.phase === "done"
          ? "arrived"
          : "idle";

  const canEditSequences = !sequencesUnavailable;
  const meta = sequence
    ? `${summaries.find((s) => s.id === sequence.id)?.station_count ?? 0} 站位 · 预估 ${total.toFixed(1)}s`
    : null;

  return (
    <div className={`app ${preview.active ? "previewing" : ""} ${executing ? "exec" : ""}`}>
      <TallyRail state={tally} />
      <EstopBar estop={state?.estop ?? null} mode={mode} connected={connected} />

      <header className="seq-bar">
        <span className="engrave seq-bar__tag">序列</span>
        <select
          value={selectedId ?? ""}
          disabled={!canEditSequences || summaries.length === 0}
          onChange={(event) => selectSequence(event.target.value || null)}
          aria-label="选择序列"
        >
          {summaries.length === 0 ? <option value="">—</option> : null}
          {summaries.map((summary) => (
            <option key={summary.id} value={summary.id}>
              {summary.name}
            </option>
          ))}
        </select>
        {meta ? <span className="seq-bar__meta num">{meta}</span> : null}
        <span className="seq-bar__spacer" />
        <button type="button" className="ghost" disabled={!canEditSequences} onClick={() => openDialog("create")}>
          新建
        </button>
        <button type="button" className="ghost" disabled={!sequence} onClick={() => openDialog("rename")}>
          改名
        </button>
        <button
          type="button"
          className="ghost"
          disabled={!sequence || sequence.blocks.length === 0}
          onClick={() => openDialog("template")}
        >
          存为模板
        </button>
        <button type="button" className="ghost" disabled={!sequence || executing} onClick={() => openDialog("delete")}>
          删除
        </button>
      </header>

      <main className="main">
        <LibraryPanel
          poses={poses}
          templates={templates}
          executing={executing}
          latched={latched}
          teaching={teaching}
          sequencesUnavailable={sequencesUnavailable}
          onGoto={(pose) => void gotoPose(pose)}
          onChanged={() => void refreshLibrary()}
          onSequenceCreated={(created) => {
            void refreshLibrary();
            selectSequence(created.id);
          }}
          onSelectSequence={selectSequence}
          onTeach={() => setTeachOpen(true)}
        />
        <MonitorPanel
          state={state}
          playback={playback}
          preview={preview}
          blocks={blocks}
          poseName={poseName}
          sequenceName={sequence?.name ?? null}
        />
      </main>

      <footer className="foot">
        {/* Teaching replaces the transport rather than covering it — the run
          * controls mean nothing while a human is pushing the arm around. */}
        {teachOpen ? (
          <TeachBar positions={state?.positions ?? {}} onDone={() => setTeachOpen(false)} />
        ) : (
          <TransportBar
            preview={preview}
            executing={executing}
            playback={playback}
            latched={latched}
            total={total}
            clockT={clockT}
            canExecute={canEditSequences && sequence !== null}
            onExecute={() => void execute()}
            onStop={stop}
            onResume={resume}
          />
        )}
        <div className="foot__timeline">
          <TimelineView
            sequence={sequence}
            poses={poses}
            playback={playback}
            executing={executing}
            latched={latched}
            preview={preview}
            selection={selection}
            onSelect={setSelection}
            onPatch={(next) => void patchBlocks(next)}
            providers={providers}
          />
          {selection && sequence ? (
            <Inspector
              sequence={sequence}
              poses={poses}
              providers={providers}
              selection={selection}
              executing={executing}
              onPatch={(next) => void patchBlocks(next)}
              onClose={() => setSelection(null)}
            />
          ) : null}
        </div>
      </footer>

      <LogDrawer rateHz={state?.rate_hz ?? 0} />

      {dialog ? (
        <Dialog
          label={
            dialog === "create"
              ? "新建序列"
              : dialog === "rename"
                ? "序列改名"
                : dialog === "template"
                  ? "存为模板"
                  : "删除序列"
          }
          onClose={() => setDialog(null)}
        >
          <div className="sheet__head">
            <h2 className="sheet__title">
              {dialog === "create"
                ? "新建序列"
                : dialog === "rename"
                  ? "序列改名"
                  : dialog === "template"
                    ? "存为模板"
                    : `删除序列「${sequence?.name}」？`}
            </h2>
          </div>
          {dialog === "delete" ? (
            <p className="hint">序列删除不可撤销；它引用的位姿都保留在素材库里。</p>
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
              disabled={dialog !== "delete" && nameDraft.trim() === ""}
              onClick={() => void submitDialog()}
            >
              {dialog === "delete" ? "确认删除" : "确定"}
            </button>
          </div>
        </Dialog>
      ) : null}
    </div>
  );
}

export default function App() {
  return (
    <ToastProvider>
      <Workspace />
    </ToastProvider>
  );
}
