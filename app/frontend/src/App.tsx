import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { AppMode, Block, Pose, SeqTemplate } from "./types";
import { useControlSocket } from "./useControlSocket";
import { useLibrary } from "./library/useLibrary";
import { makeHold, maxJointDelta, sequenceDuration } from "./timeline/model";
import { EstopBar } from "./components/EstopBar";
import { TallyRail } from "./components/TallyRail";
import { ModeWarning } from "./components/ModeWarning";
import { ToastProvider, useToast } from "./components/Toasts";
import { TuningPanel } from "./components/TuningPanel";
import { LogDrawer } from "./components/LogDrawer";
import { LibraryPanel } from "./library/LibraryPanel";
import { TeachBar } from "./library/TeachBar";
import { TemplateWizard } from "./library/TemplateWizard";
import { SequenceDialogs } from "./library/SequenceDialogs";
import type { SequenceDialogsHandle } from "./library/SequenceDialogs";
import { SequenceEditor } from "./timeline/SequenceEditor";
import { FeedbackDetails } from "./monitor/FeedbackDetails";

function Workspace() {
  const { state, playback, connected } = useControlSocket();
  const library = useLibrary();
  const {
    poses,
    sequence,
    summaries,
    templates,
    refreshLibrary,
    applySequence,
  } = library;
  const { attempt, show } = useToast();
  const [appMode, setAppMode] = useState<AppMode | null>(null);
  const [warning, setWarning] = useState(false);
  const priorMode = useRef<AppMode | null>(null);
  const [teachOpen, setTeachOpen] = useState(false);
  const [tuningOpen, setTuningOpen] = useState(false);
  const [wizard, setWizard] = useState<SeqTemplate | null>(null);
  const [pending, setPending] = useState(false);
  const [arrived, setArrived] = useState(false);
  const [viewerVisible, setViewerVisible] = useState(true);
  const previousDone = useRef(false);
  const awaitingActivity = useRef(true);
  const dialogs = useRef<SequenceDialogsHandle>(null);
  const latched = state?.estop.latched ?? false;
  const executing = state?.mode === "playback";
  const teaching = state?.mode === "teach";
  const sequencePlaying =
    executing && summaries.some((s) => s.id === playback?.sequence_id);
  const blocked =
    !connected || appMode === null || warning || latched || pending;
  const blocks = sequence?.blocks ?? [];
  const first = blocks.find((b) => b.type === "hold");
  const firstPose =
    first?.type === "hold"
      ? poses.find((p) => p.id === first.pose_id)
      : undefined;
  const far =
    !!firstPose &&
    !!state &&
    maxJointDelta(state.positions, firstPose.joints) > 0.3;
  const total = sequenceDuration(blocks);

  useEffect(() => {
    let disposed = false;
    const poll = async () => {
      try {
        const health = await api.health();
        if (disposed) return;
        const mode = health.mode as AppMode;
        setAppMode(mode);
        if (mode === "prod" && priorMode.current !== "prod") setWarning(true);
        if (priorMode.current !== mode) void refreshLibrary();
        priorMode.current = mode;
      } catch {
        if (!disposed) setAppMode(null);
      }
    };
    void poll();
    const timer = window.setInterval(poll, 3000);
    return () => {
      disposed = true;
      clearInterval(timer);
    };
  }, [refreshLibrary]);

  useEffect(() => {
    const done = playback?.phase === "done";
    if (!connected) awaitingActivity.current = true;
    if (playback && !done) awaitingActivity.current = false;
    if (!connected || executing || teaching || latched) setArrived(false);
    else if (done && !previousDone.current && !awaitingActivity.current)
      setArrived(true);
    previousDone.current = done;
    if (latched) setTeachOpen(false);
  }, [playback, executing, teaching, latched, connected]);

  useEffect(() => {
    const receive = (event: MessageEvent) => {
      const frame =
        document.querySelector<HTMLIFrameElement>(".feedback-frame");
      if (
        event.origin === location.origin &&
        event.source === frame?.contentWindow &&
        event.data?.type === "viewer-estop"
      ) {
        void attempt(() =>
          api.estop.engage("operator pressed Escape in viewer"),
        );
      }
    };
    window.addEventListener("message", receive);
    return () => window.removeEventListener("message", receive);
  }, [attempt]);

  const motion = async (run: () => Promise<unknown>) => {
    setArrived(false);
    awaitingActivity.current = true;
    setPending(true);
    await attempt(run);
    setPending(false);
  };
  const gotoPose = (pose: Pose) => {
    if (!blocked && !teaching && !sequencePlaying)
      void motion(() => api.poses.goto(pose.id));
  };
  const patchBlocks = async (next: Block[]) => {
    if (!sequence || executing || teaching) return;
    setPending(true);
    const result = await attempt(() =>
      api.sequences.patch(sequence.id, { blocks: next }),
    );
    if (result) {
      applySequence(result);
      void refreshLibrary();
    }
    setPending(false);
  };
  const append = (pose: Pose) =>
    void patchBlocks([...blocks, makeHold(pose.id)]);
  const selectSequence = (id: string | null) => library.setSelectedId(id);
  const openTeach = () => {
    setArrived(false);
    setTeachOpen(true);
  };
  return (
    <div className={"app console" + (executing ? " exec" : "")}>
      <TallyRail
        state={
          latched
            ? "latched"
            : executing
              ? "moving"
              : teaching
                ? "teach"
                : arrived
                  ? "arrived"
                  : "idle"
        }
      />
      <EstopBar
        estop={state?.estop ?? null}
        mode={state?.mode ?? null}
        connected={connected}
        appMode={appMode}
        moving={executing}
      />
      <header className="seq-bar">
        <span className="engrave">Teach & Repeat</span>
        <span>示教回放</span>
        <span className="seq-bar__spacer" />
        <button className="ghost" onClick={() => setViewerVisible((v) => !v)}>
          {viewerVisible ? "收起实况" : "展开实况"}
        </button>
      </header>
      <main className="main">
        <LibraryPanel
          poses={poses}
          executing={executing}
          latched={blocked}
          teaching={teaching}
          gotoLocked={blocked || teaching || sequencePlaying}
          wizardOpen={!!wizard}
          sequencesUnavailable={library.sequencesUnavailable}
          canAppend={!!sequence && !blocked && !executing && !teaching}
          onAppendPose={append}
          onGoto={gotoPose}
          onChanged={() => void refreshLibrary()}
          onTeach={openTeach}
        />
        <div className="monitor-area">
          <section className="monitor">
            <div className="monitor__status">
              {appMode === "sim"
                ? "仿真实况 · MuJoCo"
                : appMode === "prod"
                  ? "真机反馈"
                  : "等待后端"}
              <span className="feedback-claim">
                {!connected
                  ? "反馈已断连"
                  : latched
                    ? "已急停 · 保持力矩"
                    : executing
                      ? "移动中"
                      : teaching
                        ? "零重力 · 可推动"
                        : arrived
                          ? "已到位"
                          : "保持"}
              </span>
            </div>
            <div className="monitor__sub">
              {playback && !playback.finished
                ? playback.sequence_name +
                  " · " +
                  (playback.approaching
                    ? "接近起点…"
                    : playback.phase === "wait"
                      ? "等待继续"
                      : "执行中")
                : "选择位姿不会运动；运动请使用明确按钮。"}
            </div>
            {viewerVisible && (
              <iframe
                className="feedback-frame"
                src="/viewer/"
                title="机械臂反馈（只读）"
              />
            )}
            <div className="feedback-tools">
              <button
                className="ghost"
                disabled={blocked || executing || teaching}
                onClick={() => void motion(() => api.rest(!state?.resting))}
              >
                {state?.resting ? "唤醒" : "休息"}
              </button>
              <button
                className="ghost"
                onClick={() => setTuningOpen((v) => !v)}
              >
                调参
              </button>
              <FeedbackDetails
                positions={state?.positions ?? {}}
                simulated={appMode === "sim"}
                teaching={teaching && !blocked}
              />
            </div>
          </section>
          <TuningPanel
            visible={tuningOpen}
            appMode={appMode}
            onClose={() => setTuningOpen(false)}
          />
        </div>
      </main>
      <footer className="foot">
        <div className="seq-bar">
          <span className="engrave">回放编排</span>
          <select
            aria-label="选择序列"
            value={library.selectedId ?? ""}
            disabled={executing || pending}
            onChange={(e) => selectSequence(e.target.value || null)}
          >
            {!summaries.length && <option value="">先新建序列</option>}
            {summaries.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
          <details className="sequence-menu">
            <summary>管理序列与模板</summary>
            <div>
              {(["create", "rename", "template", "delete"] as const).map(
                (kind, i) => (
                  <button
                    key={kind}
                    disabled={
                      executing || pending || (kind !== "create" && !sequence)
                    }
                    onClick={() => dialogs.current?.open(kind)}
                  >
                    {["新建序列", "改名", "存为模板", "删除序列"][i]}
                  </button>
                ),
              )}
            </div>
          </details>
        </div>
        {wizard ? (
          <TemplateWizard
            template={wizard}
            poses={poses}
            positions={state?.positions ?? {}}
            latched={latched}
            executing={executing}
            onPosesChanged={() => void refreshLibrary()}
            onClose={() => setWizard(null)}
            onCreated={(created) => {
              setWizard(null);
              void refreshLibrary();
              selectSequence(created.id);
            }}
          />
        ) : teachOpen ? (
          <TeachBar
            positions={state?.positions ?? {}}
            autoName={"位姿 " + (poses.length + 1)}
            onDone={() => {
              setTeachOpen(false);
              void refreshLibrary();
            }}
          />
        ) : (
          <div className="transport">
            <button
              className="primary"
              disabled={blocked || executing || teaching || !blocks.length}
              onClick={() => {
                if (far && firstPose) gotoPose(firstPose);
                else if (sequence)
                  void motion(() => api.sequences.execute(sequence.id));
              }}
            >
              {executing
                ? "执行中…"
                : far
                  ? "去起点"
                  : appMode === "sim"
                    ? "执行仿真"
                    : "执行（臂会动）"}
            </button>
            <button
              disabled={!connected || (!executing && !pending)}
              onClick={() => {
                setArrived(false);
                void attempt(() => api.execute.stop());
              }}
            >
              停止
            </button>
            {playback?.phase === "wait" && (
              <button
                disabled={blocked}
                onClick={() => void motion(() => api.execute.resume())}
              >
                继续
              </button>
            )}
            <span className="num">
              预估 {total.toFixed(1)} s{" "}
              {playback && !playback.finished
                ? " · 当前 " +
                  Math.min(playback.block_index + 1, playback.block_total) +
                  "/" +
                  playback.block_total
                : ""}
            </span>
          </div>
        )}
        <SequenceEditor
          sequence={sequence}
          poses={poses}
          playback={playback}
          locked={executing || teaching || pending || !connected}
          onPatch={(next) => void patchBlocks(next)}
        />
      </footer>
      <LogDrawer rateHz={state?.rate_hz ?? 0} />
      {warning && <ModeWarning onAcknowledge={() => setWarning(false)} />}
      <SequenceDialogs
        ref={dialogs}
        sequence={sequence}
        templates={templates}
        attempt={attempt}
        show={show}
        refreshLibrary={refreshLibrary}
        applySequence={applySequence}
        selectSequence={selectSequence}
        onWizard={(template) => {
          setArrived(false);
          setWizard(template);
        }}
      />
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
