import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import type { Routine, RoutineSummary } from "./types";
import { useControlSocket } from "./useControlSocket";
import { ArmView3D } from "./components/ArmView3D";
import { EstopBar } from "./components/EstopBar";
import { JointReadout } from "./components/JointReadout";
import { PlaybackBar } from "./components/PlaybackBar";
import { RoutineList } from "./components/RoutineList";
import { WaypointEditor } from "./components/WaypointEditor";
import { ToastProvider, useToast } from "./components/Toasts";

function Workspace() {
  const { state, playback, connected } = useControlSocket();
  const { attempt } = useToast();

  const [summaries, setSummaries] = useState<RoutineSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [routine, setRoutine] = useState<Routine | null>(null);
  const [previewIndex, setPreviewIndex] = useState<number | null>(null);

  const refreshList = useCallback(async () => {
    const list = await attempt(() => api.routines.list());
    if (list) setSummaries(list);
  }, [attempt]);

  useEffect(() => {
    void refreshList();
  }, [refreshList]);

  useEffect(() => {
    if (!selectedId) {
      setRoutine(null);
      return;
    }
    void attempt(() => api.routines.get(selectedId)).then((loaded) => setRoutine(loaded ?? null));
  }, [selectedId, attempt]);

  const onRoutineChanged = useCallback(
    (updated: Routine) => {
      setRoutine(updated);
      void refreshList();
    },
    [refreshList],
  );

  const mode = state?.mode ?? null;
  const teaching = mode === "teach";
  const latched = state?.estop.latched ?? false;

  // While playing, follow the arm rather than whatever was last clicked: the
  // operator is watching the run, not editing.
  const playingIndex = mode === "playback" ? playback?.waypoint_index ?? null : null;
  const previewPose =
    previewIndex !== null && routine?.waypoints[previewIndex]
      ? routine.waypoints[previewIndex].joints
      : null;

  return (
    <div className="app">
      <EstopBar estop={state?.estop ?? null} mode={mode} rateHz={state?.rate_hz ?? 0} connected={connected} />

      <div className="workspace">
        <RoutineList
          routines={summaries}
          selectedId={selectedId}
          onSelect={(id) => {
            setSelectedId(id);
            setPreviewIndex(null);
          }}
          onChanged={refreshList}
        />

        <WaypointEditor
          routine={routine}
          teaching={teaching}
          currentIndex={playingIndex ?? previewIndex}
          onChanged={onRoutineChanged}
          onPreview={setPreviewIndex}
        />

        <div className="pane viewer-pane">
          <ArmView3D
            positions={state?.positions ?? {}}
            preview={mode === "playback" ? null : previewPose}
          />
          <div className="pane-header">
            <h2>关节</h2>
            <span className="num" style={{ fontSize: 11, color: "var(--text-faint)" }}>
              rad
            </span>
          </div>
          <JointReadout positions={state?.positions ?? {}} velocities={state?.velocities ?? {}} />
        </div>
      </div>

      <PlaybackBar
        routine={routine}
        playback={playback}
        mode={mode}
        teaching={teaching}
        latched={latched}
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
