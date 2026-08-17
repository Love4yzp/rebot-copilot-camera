import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { Pose, ProviderInfo, SeqTemplate, Sequence, SequenceSummary } from "../types";

/** Which sequence was open last, so a reload does not cost a tap. */
const LAST_SEQUENCE_KEY = "rebot:last-sequence";

export interface LibraryApi {
  poses: Pose[];
  summaries: SequenceSummary[];
  templates: SeqTemplate[];
  /** True when the v2 sequence API is not deployed (real backend, transition). */
  sequencesUnavailable: boolean;
  providers: ProviderInfo[];
  selectedId: string | null;
  setSelectedId: (id: string | null) => void;
  sequence: Sequence | null;
  /** Replace the open sequence in place (rename, PATCH answers). */
  applySequence: (updated: Sequence) => void;
  refreshLibrary: () => Promise<void>;
  poseName: (id: string) => string;
  /** pose id -> joints, for the preview and the timeline. */
  poseMap: Record<string, Record<string, number>>;
}

/**
 * Everything the workspace loads from the stores and the plugin registry:
 * poses, sequence summaries + the open sequence, templates, providers. Data
 * loading used to live inline in App.tsx; it is one concern and now one hook.
 */
export function useLibrary(): LibraryApi {
  const [poses, setPoses] = useState<Pose[]>([]);
  const [summaries, setSummaries] = useState<SequenceSummary[]>([]);
  const [templates, setTemplates] = useState<SeqTemplate[]>([]);
  const [sequencesUnavailable, setSequencesUnavailable] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [sequence, setSequence] = useState<Sequence | null>(null);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);

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

  const poseName = useCallback(
    (id: string) => poses.find((p) => p.id === id)?.name ?? "已删除位姿",
    [poses],
  );
  const poseMap = useMemo(
    () => Object.fromEntries(poses.map((p) => [p.id, p.joints])),
    [poses],
  );

  const applySequence = useCallback((updated: Sequence) => setSequence(updated), []);

  return {
    poses,
    summaries,
    templates,
    sequencesUnavailable,
    providers,
    selectedId,
    setSelectedId,
    sequence,
    applySequence,
    refreshLibrary,
    poseName,
    poseMap,
  };
}
