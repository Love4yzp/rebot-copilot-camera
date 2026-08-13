import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import type { DragEvent, PointerEvent as ReactPointerEvent } from "react";
import type { Block, EventMarker, Pose, ProviderInfo, SeqPlayback, Sequence } from "../types";
import {
  blockStarts,
  DEFAULT_HOLD_S,
  makeHold,
  markerAbsTime,
  MIN_HOLD_S,
  playbackAbsTime,
  sequenceDuration,
} from "./model";
import { markerIcon, markerKindOptions, markerLabel, newMarkerOfKind } from "./markers";
import { TrackBlock } from "./TrackBlock";
import { StationCard } from "./StationCard";
import { StationConnector } from "./StationConnector";
import type { Selection } from "./selection";
import type { PreviewApi } from "../preview/usePreview";
import { Dialog } from "../components/Dialog";
import { POSE_MIME } from "../library/LibraryPanel";

export type { Selection } from "./selection";

/** The two faces of the track: station cards for assembly, the ruler for precision. */
export type TrackDensity = "stations" | "timeline";

interface Props {
  sequence: Sequence | null;
  poses: Pose[];
  playback: SeqPlayback | null;
  /**
   * The *open sequence* is the thing being executed — its ruler is locked and
   * shows the truth (TIMELINE rule 5). A single-pose goto does not lock it:
   * the arm transits, but nothing here is being consumed.
   */
  locked: boolean;
  latched: boolean;
  preview: PreviewApi;
  density: TrackDensity;
  selection: Selection;
  onSelect: (selection: Selection) => void;
  /** Send a block list to PATCH; the server normalizes and answers. */
  onPatch: (blocks: Block[]) => void;
  providers: ProviderInfo[];
}

const HOLD_MIME = "application/x-rebot-hold";

/** Tick spacing candidates (seconds); the zoom picks the sparsest legible one. */
const TICK_STEPS = [0.5, 1, 2, 5, 10, 30];

const snap = (v: number, step: number) => Math.round(v / step) * step;
const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/**
 * The editing track: ONE editor with two render densities.
 *
 * - "stations" (default, assembly): holds render as equal-width station cards
 *   with every affordance visible (duration stepper, action chips,
 *   「＋ 动作」, reorder arrows). No ruler, no scrub, no time proportions —
 *   the ruler is either true (timeline density) or absent, never faked.
 * - "timeline" (precision): proportional blocks + ruler + trim + scrub.
 *
 * Structural rules — do not break:
 * 1. Geometry only ever reads element rects (getBoundingClientRect); never
 *    recompute coordinates from zoom state.
 * 2. Drop-preview insertion is computed in data space (committed blocks plus
 *    the current hint), never from placeholder-shifted layout.
 * 3. Densities are render branches; every edit (append, delete, reorder,
 *    duration, marker) has exactly one implementation, in this file.
 *
 * The physics model is unchanged either way: transitions are rebuilt by the
 * server's normalize after every edit, markers stay pinned inside their
 * parent block, and the ruler locks while a run owns it.
 *
 * Gestures (timeline density): drag a pose in from the library (insert a
 * hold) or tap the library card's 「＋追加」, drag holds to reorder, drag a
 * hold's right edge to trim, pin a marker via the selected block's 「＋ 动作」
 * button (or double-click), drag a marker to move it, Delete removes the
 * selected marker or hold block (the pose in the library is untouched).
 */
export function TimelineView({
  sequence,
  poses,
  playback,
  locked,
  latched,
  preview,
  density,
  selection,
  onSelect,
  onPatch,
  providers,
}: Props) {
  const trackRef = useRef<HTMLDivElement>(null);
  const stripRef = useRef<HTMLDivElement>(null);
  /** Trim/marker pointer work inside a draggable hold must not start an HTML5 drag. */
  const noDragRef = useRef(false);
  /** Live overrides while a trim/marker drag is in flight (PATCHed on release). */
  const [draftDurations, setDraftDurations] = useState<Record<string, number>>({});
  const [draftMarkerAt, setDraftMarkerAt] = useState<Record<string, number>>({});
  const [addMarker, setAddMarker] = useState<{ blockId: string; at: number } | null>(null);

  // ── timeline-density zoom ─────────────────────────────────────────────────
  // Geometry rule: gestures only ever read element rects; `zoom` drives the
  // scale wrapper's width and nothing else, so it can never corrupt them.

  /** 1 = the whole sequence fits the viewport; larger = zoomed in. */
  const [zoom, setZoom] = useState(1);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [viewportW, setViewportW] = useState(0);
  /** scrollLeft to apply after a zoom render, keeping the anchor time put. */
  const pendingScrollRef = useRef<number | null>(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    setViewportW(el.clientWidth);
    const ro = new ResizeObserver(() => setViewportW(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, [density]);

  const blocks = useMemo<Block[]>(() => {
    const base = sequence?.blocks ?? [];
    if (Object.keys(draftDurations).length === 0 && Object.keys(draftMarkerAt).length === 0) {
      return base;
    }
    return base.map((block) => {
      let out = block;
      const duration = draftDurations[block.id];
      if (duration !== undefined) out = { ...out, duration_s: duration };
      if (Object.keys(draftMarkerAt).length > 0) {
        out = {
          ...out,
          markers: out.markers.map((m) =>
            draftMarkerAt[m.id] !== undefined ? { ...m, at: draftMarkerAt[m.id] } : m,
          ),
        };
      }
      return out;
    });
  }, [sequence, draftDurations, draftMarkerAt]);

  const total = sequenceDuration(blocks);
  const starts = useMemo(() => blockStarts(blocks), [blocks]);
  const poseById = useMemo(() => new Map(poses.map((p) => [p.id, p])), [poses]);
  const holdOrder = useMemo(
    () => blocks.filter((b) => b.type === "hold").map((b) => b.id),
    [blocks],
  );

  // The playhead walks the plan ruler in preview, the truth in execution.
  const playheadT = locked && playback ? playbackAbsTime(blocks, playback) : preview.t;
  const playheadVisible = !latched && (locked || preview.active) && total > 0;

  const pxPerSec = total > 0 && viewportW > 0 ? (viewportW * zoom) / total : 0;
  const zoomMax = total > 0 && viewportW > 0 ? Math.max(1, (400 * total) / viewportW) : 1;

  const applyZoom = (next: number, anchorClientX: number | null) => {
    const clamped = clamp(next, 1, zoomMax);
    if (clamped === zoom) return;
    const el = scrollRef.current;
    if (el && total > 0) {
      const rect = el.getBoundingClientRect();
      const ax = anchorClientX ?? rect.left + el.clientWidth / 2;
      // The time under the anchor before the zoom stays under it after.
      const anchorT = ((el.scrollLeft + ax - rect.left) / (el.clientWidth * zoom)) * total;
      pendingScrollRef.current = (anchorT / total) * (el.clientWidth * clamped) - (ax - rect.left);
    }
    setZoom(clamped);
  };

  useEffect(() => {
    if (pendingScrollRef.current !== null && scrollRef.current) {
      scrollRef.current.scrollLeft = pendingScrollRef.current;
      pendingScrollRef.current = null;
    }
  }, [zoom]);

  // Ctrl/Cmd + wheel zooms (anchor = cursor). React attaches wheel listeners
  // passively, so this one goes native to allow preventDefault.
  useEffect(() => {
    const el = scrollRef.current;
    if (density !== "timeline" || !el) return;
    const onWheel = (event: WheelEvent) => {
      if ((!event.ctrlKey && !event.metaKey) || total === 0) return;
      event.preventDefault();
      applyZoom(zoom * (event.deltaY < 0 ? 1.25 : 0.8), event.clientX);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  });

  const patch = (next: Block[]) => {
    setDraftDurations({});
    setDraftMarkerAt({});
    onPatch(next);
  };

  /** Is the block at index i under the playhead right now? */
  const isCurrentBlock = (i: number) =>
    playheadVisible &&
    playheadT >= starts[i] - 1e-9 &&
    (i === blocks.length - 1 || playheadT < starts[i] + blocks[i].duration_s);

  // ── the single set of edit implementations (both densities call these) ────

  const addMarkerOn = (block: Block) => {
    if (locked) return;
    setAddMarker({
      blockId: block.id,
      at:
        block.type === "hold"
          ? snap(clamp(block.duration_s / 2, 0, block.duration_s), 0.1)
          : 0.5,
    });
  };

  const setHoldDuration = (block: Block, seconds: number) => {
    if (locked || block.type !== "hold") return;
    patch(blocks.map((b) => (b.id === block.id ? { ...b, duration_s: seconds } : b)));
  };

  const removeMarkerOfBlock = (blockId: string, markerId: string) => {
    if (locked) return;
    patch(
      blocks.map((b) =>
        b.id === blockId ? { ...b, markers: b.markers.filter((m) => m.id !== markerId) } : b,
      ),
    );
    if (selection?.kind === "marker" && selection.markerId === markerId) {
      onSelect({ kind: "block", id: blockId });
    }
  };

  const removeHold = (block: Block) => {
    // Transitions are physics, not data: no delete affordance, ever.
    if (locked || block.type !== "hold") return;
    patch(blocks.filter((b) => b.id !== block.id));
    if (selection?.kind === "block" && selection.id === block.id) onSelect(null);
  };

  /** Swap a hold with its previous/next neighbour; normalize re-links transitions. */
  const moveHold = (holdId: string, dir: -1 | 1) => {
    if (locked) return;
    const holds = blocks.filter((b): b is Extract<Block, { type: "hold" }> => b.type === "hold");
    const i = holds.findIndex((h) => h.id === holdId);
    const j = i + dir;
    if (i < 0 || j < 0 || j >= holds.length) return;
    const order = [...holds];
    [order[i], order[j]] = [order[j], order[i]];
    let cursor = 0;
    patch(blocks.map((b) => (b.type === "hold" ? order[cursor++] : b)));
  };

  // ── pose drop from the library (timeline density) ─────────────────────────

  /**
   * Drop preview: a dashed gap opens where the pose would land, pushing the
   * committed blocks apart. The insertion index is computed in data space —
   * committed blocks plus the current hint — never from the shifted layout,
   * so the gap cannot oscillate under the cursor.
   */
  const [dropHint, setDropHint] = useState<{ index: number } | null>(null);

  /** Track content duration: the truth plus the preview gap while dragging. */
  const contentTotal = total + (dropHint ? DEFAULT_HOLD_S : 0);

  const timeAtClientX = (clientX: number): number => {
    const rect = trackRef.current?.getBoundingClientRect();
    if (!rect || rect.width === 0 || contentTotal === 0) return 0;
    return clamp(((clientX - rect.left) / rect.width) * contentTotal, 0, contentTotal);
  };

  /** Index (in the blocks array) before which a hold dropped at time t goes. */
  const holdInsertIndex = (t: number): number => {
    for (let i = 0; i < blocks.length; i++) {
      if (blocks[i].type === "hold" && starts[i] > t) return i;
    }
    return blocks.length;
  };

  /** Insertion index for the pose under the cursor, anti-oscillation included. */
  const dropIndexAt = (clientX: number): number => {
    const rawT = timeAtClientX(clientX); // content space (the gap is included)
    if (!dropHint) return holdInsertIndex(rawT);
    const h = starts[dropHint.index] ?? total; // the gap's start in committed space
    if (rawT < h) return holdInsertIndex(rawT);
    if (rawT <= h + DEFAULT_HOLD_S) return dropHint.index; // inside the gap: keep
    return holdInsertIndex(rawT - DEFAULT_HOLD_S);
  };

  const onDragOver = (event: DragEvent) => {
    if (locked) return;
    if (event.dataTransfer.types.includes(POSE_MIME) || event.dataTransfer.types.includes(HOLD_MIME)) {
      event.preventDefault();
      event.dataTransfer.dropEffect = event.dataTransfer.types.includes(POSE_MIME) ? "copy" : "move";
      if (event.dataTransfer.types.includes(POSE_MIME)) {
        const index = dropIndexAt(event.clientX);
        if (index !== dropHint?.index) setDropHint({ index });
      } else if (dropHint) {
        // Hold reorders drop straight away — no preview gap for them.
        setDropHint(null);
      }
    }
  };

  const onDragLeaveTrack = (event: DragEvent) => {
    if (!trackRef.current?.contains(event.relatedTarget as Node | null)) setDropHint(null);
  };

  const onDrop = (event: DragEvent) => {
    if (locked) return;
    const poseId = event.dataTransfer.getData(POSE_MIME);
    if (poseId) {
      event.preventDefault();
      const index = dropHint?.index ?? holdInsertIndex(timeAtClientX(event.clientX));
      setDropHint(null);
      const next = [...blocks];
      next.splice(index, 0, makeHold(poseId));
      patch(next);
      return;
    }
    const holdId = event.dataTransfer.getData(HOLD_MIME);
    if (holdId) {
      event.preventDefault();
      setDropHint(null);
      reorderHold(holdId, timeAtClientX(event.clientX));
    }
  };

  const onBlockDragStart = (event: DragEvent, block: Block) => {
    // A trim/marker/button pointer gesture passing through must not pick the
    // whole block up.
    if (noDragRef.current) {
      event.preventDefault();
      return;
    }
    event.dataTransfer.setData(HOLD_MIME, block.id);
    event.dataTransfer.effectAllowed = "move";
  };

  const reorderHold = (holdId: string, t: number) => {
    const holds = blocks.filter((b): b is Extract<Block, { type: "hold" }> => b.type === "hold");
    const moving = holds.find((h) => h.id === holdId);
    if (!moving) return;
    const rest = holds.filter((h) => h.id !== holdId);
    // Where in the remaining hold order does the drop time land?
    const restStarts: number[] = [];
    {
      let acc = 0;
      const without = blocks.filter((b) => b.id !== holdId);
      for (const b of without) {
        if (b.type === "hold") restStarts.push(acc);
        acc += b.duration_s;
      }
    }
    let target = rest.length;
    for (let i = 0; i < restStarts.length; i++) {
      if (restStarts[i] > t) {
        target = i;
        break;
      }
    }
    const order = [...rest.slice(0, target), moving, ...rest.slice(target)];
    // Re-fill the hold slots in the original layout with the new order; the
    // server-side normalize re-links the transitions.
    let cursor = 0;
    patch(blocks.map((b) => (b.type === "hold" ? order[cursor++] : b)));
  };

  // ── trim a hold's right edge ──────────────────────────────────────────────

  const trimStart = (event: ReactPointerEvent, block: Block) => {
    if (locked || block.type !== "hold") return;
    event.preventDefault();
    event.stopPropagation();
    noDragRef.current = true;
    const startX = event.clientX;
    const startDuration = block.duration_s;
    const width = trackRef.current?.getBoundingClientRect().width ?? 1;
    const secondsPerPixel = total / Math.max(width, 1);

    const onMove = (move: PointerEvent) => {
      const next = clamp(
        snap(startDuration + (move.clientX - startX) * secondsPerPixel, 0.1),
        MIN_HOLD_S,
        60,
      );
      setDraftDurations({ [block.id]: next });
    };
    const onUp = (up: PointerEvent) => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      noDragRef.current = false;
      const next = clamp(
        snap(startDuration + (up.clientX - startX) * secondsPerPixel, 0.1),
        MIN_HOLD_S,
        60,
      );
      if (next !== block.duration_s) {
        patch(blocks.map((b) => (b.id === block.id ? { ...b, duration_s: next } : b)));
      } else {
        setDraftDurations({});
      }
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  // ── drag a marker inside its block ────────────────────────────────────────

  const markerDragStart = (event: ReactPointerEvent, block: Block, marker: EventMarker) => {
    if (locked) return;
    event.preventDefault();
    event.stopPropagation();
    noDragRef.current = true;
    const host = (event.currentTarget as HTMLElement).parentElement;
    const rect = host?.getBoundingClientRect();
    if (!rect || rect.width === 0) return;

    const compute = (clientX: number): number => {
      const fraction = clamp((clientX - rect.left) / rect.width, 0, 1);
      return block.type === "hold"
        ? snap(clamp(fraction * block.duration_s, 0, block.duration_s), 0.1)
        : snap(fraction, 0.05);
    };
    const onMove = (move: PointerEvent) => setDraftMarkerAt({ [marker.id]: compute(move.clientX) });
    const onUp = (up: PointerEvent) => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      noDragRef.current = false;
      const at = compute(up.clientX);
      if (at !== marker.at) {
        patch(
          blocks.map((b) =>
            b.id === block.id
              ? { ...b, markers: b.markers.map((m) => (m.id === marker.id ? { ...m, at } : m)) }
              : b,
          ),
        );
      } else {
        setDraftMarkerAt({});
      }
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  // ── scrub on the ruler ────────────────────────────────────────────────────

  const scrubStart = (event: ReactPointerEvent) => {
    if (locked || total === 0) return;
    event.preventDefault();
    preview.seek(timeAtClientX(event.clientX));
    const onMove = (move: PointerEvent) => preview.seek(timeAtClientX(move.clientX));
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  // ── double-click a block to pin a marker (the shortcut; ＋动作 is the visible way) ──

  const blockDoubleClick = (event: ReactPointerEvent | React.MouseEvent, block: Block) => {
    if (locked) return;
    const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
    if (rect.width === 0) return;
    const fraction = clamp((event.clientX - rect.left) / rect.width, 0, 1);
    const at =
      block.type === "hold"
        ? snap(clamp(fraction * block.duration_s, 0, block.duration_s), 0.1)
        : snap(fraction, 0.05);
    setAddMarker({ blockId: block.id, at });
  };

  const chooseMarkerKind = (kind: string) => {
    if (!addMarker) return;
    const marker = newMarkerOfKind(kind, addMarker.at, providers);
    patch(
      blocks.map((b) => (b.id === addMarker.blockId ? { ...b, markers: [...b.markers, marker] } : b)),
    );
    onSelect({ kind: "marker", blockId: addMarker.blockId, markerId: marker.id });
    setAddMarker(null);
  };

  // ── Delete key: remove the selected marker / hold block ───────────────────

  useEffect(() => {
    if (locked || !selection) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Delete" && event.key !== "Backspace") return;
      const node = event.target as HTMLElement | null;
      if (node && /^(INPUT|TEXTAREA|SELECT)$/.test(node.tagName)) return;
      if (node?.closest("[role='dialog']")) return;
      event.preventDefault();
      if (selection.kind === "marker") {
        removeMarkerOfBlock(selection.blockId, selection.markerId);
      } else {
        const block = blocks.find((b) => b.id === selection.id);
        if (!block) return;
        removeHold(block);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  // ── scroll the station strip to a freshly appended card ───────────────────

  const appendWatchRef = useRef<{ seqId: string | null; length: number }>({
    seqId: null,
    length: 0,
  });
  useEffect(() => {
    const seqId = sequence?.id ?? null;
    const prev = appendWatchRef.current;
    const appendedOne = prev.seqId === seqId && seqId !== null && blocks.length === prev.length + 1;
    if (density === "stations" && appendedOne && stripRef.current) {
      stripRef.current.scrollLeft = stripRef.current.scrollWidth;
    }
    appendWatchRef.current = { seqId, length: blocks.length };
  }, [density, sequence, blocks.length]);

  // ── render ────────────────────────────────────────────────────────────────

  const markerCount = blocks.reduce((n, b) => n + b.markers.length, 0);
  // Tick density follows the zoom: ticks stay at least ~50px apart.
  const tickStep = pxPerSec > 0 ? (TICK_STEPS.find((st) => st * pxPerSec >= 50) ?? 30) : 1;
  const labelEvery = tickStep < 1 ? 1 : tickStep < 5 ? 5 : tickStep;
  const ticks: number[] = [];
  for (let s = 0; s <= Math.floor(total) + 1e-9; s += tickStep) ticks.push(s);

  const spans = useMemo(() => {
    const out: { left: number; width: number; label: string; key: string }[] = [];
    blocks.forEach((block, i) => {
      for (const marker of block.markers) {
        if (marker.estimate_s < 1) continue;
        const absT = markerAbsTime(starts[i], block, marker);
        out.push({
          left: (absT / contentTotal) * 100,
          width: (Math.min(marker.estimate_s, contentTotal - absT) / contentTotal) * 100,
          label: `${markerLabel(marker.kind, providers)}（预估）`,
          key: marker.id,
        });
      }
    });
    return out;
  }, [blocks, starts, contentTotal, providers]);

  const emptyState =
    sequence === null ? (
      <div className="tl-empty">先在顶栏选择或新建一条序列。</div>
    ) : (
      <div className="tl-empty">
        点素材库位姿卡的「＋追加」，或把位姿拖到这里，排出第一个站位
      </div>
    );

  return (
    <div className="tl">
      <div className="tl-meta">
        {density === "timeline" ? (
          <span>
            时间轴 · {blocks.length} 块 · {markerCount} 个动作（过渡自动生成、不可删）
          </span>
        ) : (
          <span>
            站位 · {holdOrder.length} 站 · {markerCount} 个动作（过渡自动生成、不可删）
          </span>
        )}
        <span className="r">
          全长 <span className="num">{total.toFixed(1)}s</span>（指令时长，动作时长为预估）
          {density === "timeline" && total > 0 ? (
            <span className="tl-zoom">
              <button
                type="button"
                title="缩小"
                disabled={zoom <= 1}
                onClick={() => applyZoom(zoom / 1.5, null)}
              >
                −
              </button>
              <button
                type="button"
                title="适配宽度"
                disabled={zoom === 1}
                onClick={() => applyZoom(1, null)}
              >
                适配
              </button>
              <button
                type="button"
                title="放大（也可按住 Ctrl/⌘ 滚轮）"
                disabled={zoom >= zoomMax}
                onClick={() => applyZoom(zoom * 1.5, null)}
              >
                ＋
              </button>
            </span>
          ) : null}
        </span>
      </div>

      {density === "timeline" ? (
        <div className="tl-inner">
          <div className="tl-scroll" ref={scrollRef}>
            <div className="tl-scale" style={{ width: zoom > 1 ? `${zoom * 100}%` : "100%" }}>
              <div className="tl-ruler" onPointerDown={scrubStart}>
                {ticks.map((s) => (
                  <div key={s} className="tl-tick" style={{ left: `${(s / contentTotal) * 100}%` }}>
                    {s % labelEvery < 1e-9 && total - s >= 2 ? (
                      <span className="num">{s}s</span>
                    ) : null}
                  </div>
                ))}
                {total > 0 ? (
                  <div className="tl-tick end" style={{ left: "100%" }}>
                    <span className="num">{total.toFixed(1)}s</span>
                  </div>
                ) : null}
              </div>

              <div
                className="tl-track"
                ref={trackRef}
                onDragOver={onDragOver}
                onDrop={onDrop}
                onDragLeave={onDragLeaveTrack}
              >
                {blocks.length === 0 && !dropHint ? (
                  emptyState
                ) : (
                  <>
                    {blocks.map((block, i) => (
                      <Fragment key={block.id}>
                        {dropHint?.index === i ? (
                          <div
                            className="tl-ghost"
                            style={{ width: `${(DEFAULT_HOLD_S / contentTotal) * 100}%` }}
                          />
                        ) : null}
                        <TrackBlock
                          block={block}
                          start={starts[i]}
                          widthPct={(block.duration_s / contentTotal) * 100}
                          isCurrent={isCurrentBlock(i)}
                          locked={locked}
                          playheadVisible={playheadVisible}
                          playheadT={playheadT}
                          pose={block.type === "hold" ? poseById.get(block.pose_id) : undefined}
                          selection={selection}
                          providers={providers}
                          noDragRef={noDragRef}
                          onSelectBlock={(id) => onSelect({ kind: "block", id })}
                          onSelectMarker={(blockId, markerId) =>
                            onSelect({ kind: "marker", blockId, markerId })
                          }
                          onBlockDragStart={onBlockDragStart}
                          onBlockDoubleClick={blockDoubleClick}
                          onTrimStart={trimStart}
                          onMarkerDragStart={markerDragStart}
                          onAddMarker={addMarkerOn}
                        />
                      </Fragment>
                    ))}
                    {dropHint && dropHint.index >= blocks.length ? (
                      <div
                        className="tl-ghost"
                        style={{ width: `${(DEFAULT_HOLD_S / contentTotal) * 100}%` }}
                      />
                    ) : null}
                  </>
                )}

                {playheadVisible ? (
                  <div className="tl-playhead" style={{ left: `${(playheadT / contentTotal) * 100}%` }} />
                ) : null}

                {locked ? <div className="tl-lock">执行中 · 已锁定</div> : null}
              </div>

              {spans.length > 0 ? (
                <div className="tl-spans">
                  {spans.map((span) => (
                    <span
                      key={span.key}
                      className="tl-span"
                      style={{ left: `${span.left}%`, width: `${span.width}%` }}
                    >
                      {span.label}
                    </span>
                  ))}
                </div>
              ) : null}
            </div>
          </div>
        </div>
      ) : (
        <div className="stn-wrap">
          <div className="stn-strip" ref={stripRef}>
            {blocks.length === 0
              ? emptyState
              : blocks.map((block, i) => {
                  if (block.type === "hold") {
                    const position = holdOrder.indexOf(block.id);
                    return (
                      <StationCard
                        key={block.id}
                        block={block}
                        index={position + 1}
                        pose={poseById.get(block.pose_id)}
                        isCurrent={isCurrentBlock(i)}
                        locked={locked}
                        selection={selection}
                        providers={providers}
                        canMovePrev={position > 0}
                        canMoveNext={position >= 0 && position < holdOrder.length - 1}
                        onSelect={() => onSelect({ kind: "block", id: block.id })}
                        onSelectMarker={(markerId) =>
                          onSelect({ kind: "marker", blockId: block.id, markerId })
                        }
                        onRemoveMarker={(markerId) => removeMarkerOfBlock(block.id, markerId)}
                        onSetDuration={(seconds) => setHoldDuration(block, seconds)}
                        onAddMarker={() => addMarkerOn(block)}
                        onMove={(dir) => moveHold(block.id, dir)}
                        onRemove={() => removeHold(block)}
                      />
                    );
                  }
                  return (
                    <StationConnector
                      key={block.id}
                      block={block}
                      isCurrent={isCurrentBlock(i)}
                      selected={selection?.kind === "block" && selection.id === block.id}
                      onSelect={() => onSelect({ kind: "block", id: block.id })}
                    />
                  );
                })}
          </div>
          {locked ? <div className="tl-lock">执行中 · 已锁定</div> : null}
        </div>
      )}

      {addMarker ? (
        <Dialog label="添加动作" onClose={() => setAddMarker(null)}>
          <div className="sheet__head">
            <h2 className="sheet__title">添加动作</h2>
          </div>
          <p className="hint">
            钉在块内{" "}
            {(() => {
              const block = blocks.find((b) => b.id === addMarker.blockId);
              if (!block) return "";
              return block.type === "hold"
                ? `${addMarker.at.toFixed(1)}s 处`
                : `${Math.round(addMarker.at * 100)}% 处`;
            })()}
            ，随父块移动与修剪。
          </p>
          <div className="sheet__add">
            {markerKindOptions(providers).map((option) => (
              <button
                key={option.kind}
                type="button"
                className="sheet__add-btn"
                disabled={!option.enabled}
                title={option.reason ?? undefined}
                onClick={() => chooseMarkerKind(option.kind)}
              >
                {markerIcon(option.kind)} {option.label}
              </button>
            ))}
          </div>
        </Dialog>
      ) : null}
    </div>
  );
}
