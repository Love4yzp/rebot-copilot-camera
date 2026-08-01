import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "./api";
import type { ProviderInfo, Routine, RoutineSummary, Waypoint } from "./types";
import { useControlSocket } from "./useControlSocket";
import { AnchorBoard } from "./components/AnchorBoard";
import type { AnchorCardStatus } from "./components/AnchorCard";
import { AnchorEditSheet } from "./components/AnchorEditSheet";
import { CollectionBar } from "./components/CollectionBar";
import { ControlBar } from "./components/ControlBar";
import { EstopBar } from "./components/EstopBar";
import { LogDrawer } from "./components/LogDrawer";
import { TallyRail } from "./components/TallyRail";
import type { TallyState } from "./components/TallyRail";
import { TeachRail } from "./components/TeachRail";
import { ToastProvider, useToast } from "./components/Toasts";
import { ViewerDrawer } from "./components/ViewerDrawer";

/** The four-corner wizard's step names, recorded as each anchor's note. */
const FOUR_CORNER_NAMES = ["正面", "右 45°", "侧面", "俯拍"];

/** Which collection was open last, so a reload does not cost a tap. */
const LAST_COLLECTION_KEY = "rebot:last-collection";

/**
 * How long a tapped card may claim "出发中" before the arm has said anything.
 * Past this the optimistic state is dropped rather than left hanging — an
 * interface that keeps insisting something is about to happen is worse than
 * one that admits it does not know.
 */
const PENDING_TIMEOUT_MS = 3000;

function Workspace() {
  const { state, playback, connected } = useControlSocket();
  const { attempt, show } = useToast();

  const [summaries, setSummaries] = useState<RoutineSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [routine, setRoutine] = useState<Routine | null>(null);
  // Config mode is opt-in; the board boots into the use layer.
  const [config, setConfig] = useState(false);
  /** The anchor the arm is running against, or holding at. */
  const [target, setTarget] = useState<number | null>(null);
  /** Tapped, request in flight, arm has not reported back yet. */
  const [pending, setPending] = useState<number | null>(null);
  const [editingAnchor, setEditingAnchor] = useState<number | null>(null);
  /** null names = record a single anchor; an array runs the template wizard. */
  const [teachFlow, setTeachFlow] = useState<{ names: string[] | null } | null>(null);
  const [viewerOpen, setViewerOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  // Installed action providers. Loaded once: plugins arrive by installing a
  // package and restarting the service, so the list cannot change under a
  // running page. The edit sheet draws its trigger controls from this.
  const [providers, setProviders] = useState<ProviderInfo[]>([]);

  const refreshList = useCallback(async () => {
    const list = await attempt(() => api.routines.list());
    if (list) setSummaries(list);
  }, [attempt]);

  useEffect(() => {
    void refreshList();
  }, [refreshList]);

  // A provider list that fails to load must not take the board with it: the
  // anchors and the arm still work, only the trigger editor is poorer.
  useEffect(() => {
    api.plugins
      .list()
      .then(setProviders)
      .catch(() => setProviders([]));
  }, []);

  // Land on something usable. Reopening the app on the collection that was
  // last in use is right far more often than landing on an empty board, and a
  // first-run operator gets the first collection rather than a dead end.
  useEffect(() => {
    if (selectedId !== null || summaries.length === 0) return;
    const remembered = localStorage.getItem(LAST_COLLECTION_KEY);
    const wanted = summaries.find((s) => s.id === remembered) ?? summaries[0];
    setSelectedId(wanted.id);
  }, [summaries, selectedId]);

  useEffect(() => {
    if (!selectedId) {
      setRoutine(null);
      return;
    }
    localStorage.setItem(LAST_COLLECTION_KEY, selectedId);
    void attempt(() => api.routines.get(selectedId)).then((loaded) => setRoutine(loaded ?? null));
  }, [selectedId, attempt]);

  const reloadRoutine = useCallback(async () => {
    if (!selectedId) return;
    const loaded = await attempt(() => api.routines.get(selectedId));
    if (loaded) setRoutine(loaded);
    void refreshList();
  }, [selectedId, attempt, refreshList]);

  const mode = state?.mode ?? null;
  const teaching = mode === "teach";
  const latched = state?.estop.latched ?? false;
  const phase = playback?.phase ?? null;

  // The estop force-closes the teach rail: unmounting TeachRail runs its
  // cleanup, which exits teach mode. Dragging against a latched arm is the one
  // combination that must be impossible to leave on screen.
  useEffect(() => {
    if (latched) setTeachFlow(null);
  }, [latched]);

  // Anything that moves the arm without the board's knowledge invalidates
  // "已到位". A latched stop freezes it somewhere else; teach mode means a
  // human is about to push it somewhere else. Leaving a card lit through
  // either is the interface claiming to know where the arm is when it does
  // not — which is the one claim this board exists to make truthfully.
  useEffect(() => {
    if (latched || teaching) {
      setTarget(null);
      setPending(null);
    }
  }, [latched, teaching]);

  // Hand the card over to the arm's own report the moment there is one.
  //
  // Only a *running* phase counts. The controller keeps the last finished run
  // on the wire, so at the instant of a tap the socket is still repeating
  // `done` from the previous anchor — treating that as this anchor's answer
  // would light the new card 已到位 before the arm had moved a millimetre,
  // which is the exact failure this state machine exists to prevent.
  useEffect(() => {
    if (pending === null) return;

    const running =
      playback?.phase === "moving" ||
      playback?.phase === "settling" ||
      playback?.phase === "acting";

    if (running) {
      setTarget(pending);
      setPending(null);
      return;
    }

    // Nothing ever reported. Drop the claim rather than promoting it: the
    // board does not know where the arm is, and saying so is the honest
    // answer. `target` is deliberately left alone.
    const timer = window.setTimeout(() => setPending(null), PENDING_TIMEOUT_MS);
    return () => window.clearTimeout(timer);
  }, [playback, pending]);

  // A finished whole-collection run leaves the arm holding its last anchor.
  useEffect(() => {
    if (playback?.finished && playback.waypoint_total > 1) setTarget(playback.waypoint_total - 1);
  }, [playback]);

  const motionBlocked = latched || mode === "playback" || mode === "teach";

  /**
   * Which card the board should be reporting on. A tap wins immediately, then
   * a whole-collection run follows the executor, and otherwise it is whatever
   * anchor the arm last went to.
   */
  const boardActive =
    pending !== null
      ? pending
      : playback && playback.waypoint_total > 1
        ? // The executor increments past the last waypoint before it notices
          // it is done, so a finished run reports an index one out of range.
          // Clamp, or the card the arm is holding goes dark at the finish.
          Math.min(playback.waypoint_index, playback.waypoint_total - 1)
        : target;

  const statusAt = useCallback(
    (index: number): AnchorCardStatus => {
      if (index !== boardActive) return "idle";
      if (pending !== null) return "pending";
      switch (phase) {
        case "moving":
        case "settling":
        case "acting":
          return phase;
        case "done":
          return "arrived";
        default:
          // No live phase means nothing is known — not that the arm arrived.
          return "idle";
      }
    },
    [boardActive, pending, phase],
  );

  const tally: TallyState = latched
    ? "latched"
    : teaching
      ? "teach"
      : phase === "moving" || phase === "settling" || phase === "acting"
        ? phase
        : boardActive !== null && phase === "done"
          ? "arrived"
          : "idle";

  const goto = useCallback(
    async (index: number) => {
      if (!routine) return;

      // A dead control that swallows the tap in silence teaches nothing. The
      // two reasons motion is unavailable are different problems with
      // different fixes, so they get different sentences.
      if (latched) {
        show("info", "已急停 — 先解除急停");
        return;
      }

      setPending(index);
      try {
        await api.goto(routine.id, index);
      } catch (error) {
        setPending(null);
        if (error instanceof ApiError && error.status === 409) {
          show("info", "臂正忙 — 等当前动作完成");
        } else {
          show("error", error instanceof Error ? error.message : String(error));
        }
      }
    },
    [routine, latched, show],
  );

  // Number keys fire anchors. The operator's hands are usually on the camera
  // or the arm, so the card's corner number is also the key that runs it — and
  // the same binding takes a foot pedal that sends digits.
  const gotoRef = useRef(goto);
  gotoRef.current = goto;
  useEffect(() => {
    if (config) return;

    const onKey = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const node = event.target as HTMLElement | null;
      if (node && /^(INPUT|TEXTAREA|SELECT)$/.test(node.tagName)) return;
      if (node?.closest("[role='dialog']")) return;

      const digit = Number(event.key);
      if (!Number.isInteger(digit) || digit < 1 || digit > 9) return;
      event.preventDefault();
      void gotoRef.current(digit - 1);
    };

    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [config]);

  const move = async (index: number, dir: -1 | 1) => {
    if (!routine) return;
    const to = index + dir;
    if (to < 0 || to >= routine.waypoints.length) return;
    // The backend demands a full permutation — anything else silently drops or
    // duplicates a waypoint — so build it here rather than sending a pair.
    const order = routine.waypoints.map((_, i) => i);
    order.splice(to, 0, ...order.splice(index, 1));
    const updated = await attempt(() => api.waypoints.reorder(routine.id, order));
    if (updated) {
      setRoutine(updated);
      // The swap moved the lit card with its anchor.
      setTarget((current) => (current === index ? to : current === to ? index : current));
      void refreshList();
    }
  };

  const select = (id: string) => {
    setSelectedId(id);
    setTarget(null);
    setPending(null);
    setEditingAnchor(null);
    setTeachFlow(null);
  };

  const startWizard = (kind: "blank" | "four", created: Routine) => {
    setSelectedId(created.id);
    setRoutine(created);
    setTarget(null);
    setPending(null);
    setEditingAnchor(null);
    setConfig(true);
    if (kind === "four") setTeachFlow({ names: FOUR_CORNER_NAMES });
  };

  /** Deleting an anchor throws away a pose somebody walked over and set by hand. */
  const undoRemove = (removed: Waypoint, index: number) => {
    if (!routine) return;
    void attempt(async () => {
      const restored = await api.waypoints.add(routine.id, {
        joints: removed.joints,
        duration_s: removed.duration_s,
        settle_ms: removed.settle_ms,
        actions: removed.actions,
        note: removed.note,
        index,
      });
      setRoutine(restored);
      void refreshList();
      return restored;
    }, "已恢复");
  };

  // The 3D view previews whichever anchor is open for editing. Selection, not
  // hover: the device this runs on has no hover, and the old binding left a
  // stale pose on screen after the pointer wandered off.
  const previewWaypoint =
    editingAnchor !== null ? (routine?.waypoints[editingAnchor] ?? null) : null;

  return (
    <div className="app">
      <TallyRail state={tally} />

      <EstopBar estop={state?.estop ?? null} mode={mode} connected={connected} />

      <CollectionBar
        routines={summaries}
        selectedId={selectedId}
        config={config}
        menuOpen={menuOpen}
        onMenuOpen={setMenuOpen}
        onSelect={select}
        onChanged={refreshList}
        onStartWizard={startWizard}
      />

      {/* The drawer shares this row so it can only ever cover the cards —
        * never the emergency stop above it or the run controls below. */}
      <div className="board-row">
        <AnchorBoard
          routine={routine}
          noCollections={summaries.length === 0}
          config={config}
          statusAt={statusAt}
          motionBlocked={motionBlocked}
          onGoto={(index) => void goto(index)}
          onEditAnchor={setEditingAnchor}
          onMove={(index, dir) => void move(index, dir)}
          onCreateCollection={() => setMenuOpen(true)}
          onRecordFirst={() => {
            setConfig(true);
            setTeachFlow({ names: null });
          }}
        />

        <ViewerDrawer
          open={viewerOpen}
          onClose={() => setViewerOpen(false)}
          positions={state?.positions ?? {}}
          preview={mode === "playback" ? null : (previewWaypoint?.joints ?? null)}
          previewName={
            previewWaypoint && mode !== "playback"
              ? previewWaypoint.note.trim() || `锚点 ${(editingAnchor ?? 0) + 1}`
              : null
          }
        />
      </div>

      <LogDrawer rateHz={state?.rate_hz ?? 0} />

      {/* Teaching replaces the bottom bar rather than covering it: the run
        * controls mean nothing while a human is pushing the arm around, and
        * leaving them visible-but-unclickable underneath an overlay is the
        * same trap the old teach modal set with the emergency stop. */}
      {teachFlow && routine ? (
        <TeachRail
          routine={routine}
          names={teachFlow.names}
          onDone={() => {
            setTeachFlow(null);
            void reloadRoutine();
          }}
        />
      ) : (
        <ControlBar
          routine={routine}
          playback={playback}
          mode={mode}
          teaching={teaching}
          latched={latched}
          config={config}
          viewerOpen={viewerOpen}
          onToggleConfig={() => setConfig((value) => !value)}
          onToggleViewer={() => setViewerOpen((value) => !value)}
        />
      )}

      {config && routine && !teachFlow && (
        <button
          className="record-fab primary"
          disabled={latched}
          onClick={() => setTeachFlow({ names: null })}
        >
          + 录锚点
        </button>
      )}

      {editingAnchor !== null && routine && routine.waypoints[editingAnchor] && (
        <AnchorEditSheet
          routine={routine}
          index={editingAnchor}
          providers={providers}
          onClose={(updated) => {
            if (updated) {
              setRoutine(updated);
              void refreshList();
            }
            setEditingAnchor(null);
          }}
          onRemoved={(updated, removed, index) => {
            setRoutine(updated);
            void refreshList();
            setTarget(null);
            setEditingAnchor(null);
            show("info", `已删除「${removed.note.trim() || `锚点 ${index + 1}`}」`, {
              label: "撤销",
              run: () => undoRemove(removed, index),
            });
          }}
        />
      )}
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
