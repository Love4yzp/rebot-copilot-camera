import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { Pose, SeqTemplate, Sequence, SequenceSummary } from "../types";

/** Which sequence was open last, so a reload does not cost a tap. */
const LAST_SEQUENCE_KEY = "rebot:last-sequence";

export interface LibraryApi {
  poses: Pose[];
  summaries: SequenceSummary[];
  templates: SeqTemplate[];
  /** True when the v2 sequence API is not deployed (real backend, transition). */
  sequencesUnavailable: boolean;
  selectedId: string | null;
  setSelectedId: (id: string | null) => void;
  sequence: Sequence | null;
  /** Replace the open sequence in place (rename, PATCH answers). */
  applySequence: (updated: Sequence) => void;
  refreshLibrary: () => Promise<void>;
}

/** Load pose, sequence and template records; no plugin registry or execution. */
export function useLibrary(): LibraryApi {
  const [poses, setPoses] = useState<Pose[]>([]);
  const [summaries, setSummaries] = useState<SequenceSummary[]>([]);
  const [templates, setTemplates] = useState<SeqTemplate[]>([]);
  const [sequencesUnavailable, setSequencesUnavailable] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [sequence, setSequence] = useState<Sequence | null>(null);

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
    let disposed = false;
    setSequence(null);
    localStorage.setItem(LAST_SEQUENCE_KEY, selectedId);
    api.sequences.get(selectedId).then(value => { if (!disposed) setSequence(value); })
      .catch(() => { if (!disposed) setSequence(null); });
    return () => { disposed = true; };
  }, [selectedId]);

  const applySequence = useCallback(
    (updated: Sequence) => setSequence(updated),
    [],
  );

  return {
    poses,
    summaries,
    templates,
    sequencesUnavailable,
    selectedId,
    setSelectedId,
    sequence,
    applySequence,
    refreshLibrary,
  };
}
