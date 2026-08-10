import { useEffect, useMemo, useRef, useState } from "react";
import type { DragEvent, PointerEvent as ReactPointerEvent } from "react";
import type { Block, EventMarker, Pose, ProviderInfo, SeqPlayback, Sequence } from "../types";
import {
  blockStarts,
  makeHold,
  markerAbsTime,
  MIN_HOLD_S,
  playbackAbsTime,
  sequenceDuration,
} from "./model";
import { markerIcon, markerKindOptions, markerLabel, newMarkerOfKind } from "./markers";
import type { PreviewApi } from "../preview/usePreview";
import { Dialog } from "../components/Dialog";
import { POSE_MIME } from "../library/LibraryPanel";

export type Selection =
  | { kind: "block"; id: string }
  | { kind: "marker"; blockId: string; markerId: string }
  | null;

interface Props {
  sequence: Sequence | null;
  poses: Pose[];
  playback: SeqPlayback | null;
  /**
   * The *open sequence* is the one being executed — its ruler is locked and
   * shows the truth (TIMELINE rule 5). A single-pose goto does not lock it:
   * the arm transits, but nothing here is being consumed.
   */
  locked: boolean;
  latched: boolean;
  preview: PreviewApi;
  selection: Selection;
  onSelect: (selection: Selection) => void;
  /** Send a block list to PATCH; the server normalizes and answers. */
  onPatch: (blocks: Block[]) => void;
  providers: ProviderInfo[];
}

const HOLD_MIME = "application/x-rebot-hold";

const EASING_LABEL: Record<string, string> = {
  linear: "线性",
  ease_in: "缓入",
  ease_out: "缓出",
  ease_in_out: "缓入缓出",
};

const snap = (v: number, step: number) => Math.round(v / step) * step;
const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/**
 * The timeline: plan ruler + skeleton blocks + pinned markers + playhead.
 *
 * The ruler is a *plan* ruler — block lengths are commanded durations, marker
 * spans are estimates, and the labels say which is which. Transitions never
 * offer a delete affordance: they are not data the user owns, they are the
 * physics between two stations, rebuilt by `normalize` after every edit.
 *
 * Gestures: drag a pose in from the library (insert a hold), drag holds to
 * reorder, drag a hold's right edge to trim, double-click a block to pin a
 * marker, drag a marker to move it, Delete removes the selected marker or
 * hold block (the pose in the library is untouched either way).
 */
export function TimelineView({
  sequence,
  poses,
  playback,
  locked,
  latched,
  preview,
  selection,
  onSelect,
  onPatch,
  providers,
}: Props) {
  const trackRef = useRef<HTMLDivElement>(null);
  /** Trim/marker pointer work inside a draggable hold must not start an HTML5 drag. */
  const noDragRef = useRef(false);
  /** Live overrides while a trim/marker drag is in flight (PATCHed on release). */
  const [draftDurations, setDraftDurations] = useState<Record<string, number>>({});
  const [draftMarkerAt, setDraftMarkerAt] = useState<Record<string, number>>({});
  const [addMarker, setAddMarker] = useState<{ blockId: string; at: number } | null>(null);

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

  // The playhead walks the plan ruler in preview, the truth in execution.
  const playheadT = locked && playback ? playbackAbsTime(blocks, playback) : preview.t;
  const playheadVisible = !latched && (locked || preview.active) && total > 0;

  const patch = (next: Block[]) => {
    setDraftDurations({});
    setDraftMarkerAt({});
    onPatch(next);
  };

  // ── pose drop from the library ────────────────────────────────────────────

  const timeAtClientX = (clientX: number): number => {
    const rect = trackRef.current?.getBoundingClientRect();
    if (!rect || rect.width === 0 || total === 0) return 0;
    return clamp(((clientX - rect.left) / rect.width) * total, 0, total);
  };

  /** Index (in the blocks array) before which a hold dropped at time t goes. */
  const holdInsertIndex = (t: number): number => {
    for (let i = 0; i < blocks.length; i++) {
      if (blocks[i].type === "hold" && starts[i] > t) return i;
    }
    return blocks.length;
  };

  const onDragOver = (event: DragEvent) => {
    if (locked) return;
    if (event.dataTransfer.types.includes(POSE_MIME) || event.dataTransfer.types.includes(HOLD_MIME)) {
      event.preventDefault();
      event.dataTransfer.dropEffect = event.dataTransfer.types.includes(POSE_MIME) ? "copy" : "move";
    }
  };

  const onDrop = (event: DragEvent) => {
    if (locked) return;
    const poseId = event.dataTransfer.getData(POSE_MIME);
    if (poseId) {
      event.preventDefault();
      const next = [...blocks];
      next.splice(holdInsertIndex(timeAtClientX(event.clientX)), 0, makeHold(poseId));
      patch(next);
      return;
    }
    const holdId = event.dataTransfer.getData(HOLD_MIME);
    if (holdId) {
      event.preventDefault();
      reorderHold(holdId, timeAtClientX(event.clientX));
    }
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

  // ── double-click a block to pin a marker ──────────────────────────────────

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
        patch(
          blocks.map((b) =>
            b.id === selection.blockId
              ? { ...b, markers: b.markers.filter((m) => m.id !== selection.markerId) }
              : b,
          ),
        );
      } else {
        const block = blocks.find((b) => b.id === selection.id);
        // Transitions are physics, not data: no delete affordance, ever.
        if (!block || block.type !== "hold") return;
        patch(blocks.filter((b) => b.id !== selection.id));
      }
      onSelect(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  // ── render ────────────────────────────────────────────────────────────────

  const markerCount = blocks.reduce((n, b) => n + b.markers.length, 0);
  const tickStep = total > 60 ? 5 : 1;
  const ticks: number[] = [];
  for (let s = 0; s <= Math.floor(total); s += tickStep) ticks.push(s);

  const spans = useMemo(() => {
    const out: { left: number; width: number; label: string; key: string }[] = [];
    blocks.forEach((block, i) => {
      for (const marker of block.markers) {
        if (marker.estimate_s < 1) continue;
        const absT = markerAbsTime(starts[i], block, marker);
        out.push({
          left: (absT / total) * 100,
          width: (Math.min(marker.estimate_s, total - absT) / total) * 100,
          label: `${markerLabel(marker.kind, providers)}（预估）`,
          key: marker.id,
        });
      }
    });
    return out;
  }, [blocks, starts, total, providers]);

  return (
    <div className="tl">
      <div className="tl-meta">
        <span>
          时间轴 · 骨架块 {blocks.length} · 块内标记 {markerCount}（过渡块自动生成、不可删）
        </span>
        <span className="r">
          适配宽度 · 全长 <span className="num">{total.toFixed(1)}s</span>（指令时长，动作时长为预估）
        </span>
      </div>

      <div className="tl-inner">
        <div className="tl-ruler" onPointerDown={scrubStart}>
          {ticks.map((s) => (
            <div key={s} className="tl-tick" style={{ left: `${(s / total) * 100}%` }}>
              {s % 5 === 0 && total - s >= 2 ? <span className="num">{s}s</span> : null}
            </div>
          ))}
          {total > 0 ? (
            <div className="tl-tick end" style={{ left: "100%" }}>
              <span className="num">{total.toFixed(1)}s</span>
            </div>
          ) : null}
        </div>

        <div className="tl-track" ref={trackRef} onDragOver={onDragOver} onDrop={onDrop}>
          {blocks.length === 0 ? (
            <div className="tl-empty">从素材库把位姿拖到这里，排出第一个站位</div>
          ) : (
            blocks.map((block, i) => {
              const width = `${(block.duration_s / total) * 100}%`;
              const isCurrent =
                playheadVisible &&
                playheadT >= starts[i] - 1e-9 &&
                (i === blocks.length - 1 || playheadT < starts[i] + block.duration_s);
              const selected = selection?.kind === "block" && selection.id === block.id;
              const pose = block.type === "hold" ? poseById.get(block.pose_id) : undefined;
              return (
                <div
                  key={block.id}
                  className={[
                    "blk",
                    block.type === "hold" ? "hold" : "trans",
                    isCurrent ? "cur" : "",
                    selected ? "sel" : "",
                    block.type === "hold" && !pose ? "missing" : "",
                  ].join(" ")}
                  style={{ width }}
                  draggable={!locked && block.type === "hold"}
                  onDragStart={(event) => {
                    // A trim/marker pointer gesture passing through must not
                    // pick the whole block up.
                    if (noDragRef.current) {
                      event.preventDefault();
                      return;
                    }
                    event.dataTransfer.setData(HOLD_MIME, block.id);
                    event.dataTransfer.effectAllowed = "move";
                  }}
                  onClick={() => onSelect({ kind: "block", id: block.id })}
                  onDoubleClick={(event) => blockDoubleClick(event, block)}
                >
                  {block.type === "hold" ? (
                    <div className="blk__in">
                      <span className="blk__name">{pose?.name ?? "已删除位姿"}</span>
                      <span className="blk__dur num">{block.duration_s.toFixed(1)}s</span>
                    </div>
                  ) : (
                    <div className="blk__in trans">
                      <span className="num">{block.duration_s.toFixed(1)}s</span>{" "}
                      {EASING_LABEL[block.easing]}
                    </div>
                  )}

                  {block.markers.map((marker) => {
                    const atSeconds = markerTimeInBlockView(block, marker);
                    const absT = markerAbsTime(starts[i], block, marker);
                    const fired = playheadVisible && playheadT >= absT - 1e-9 && playheadT > 0;
                    const markerSelected =
                      selection?.kind === "marker" && selection.markerId === marker.id;
                    return (
                      <button
                        key={marker.id}
                        type="button"
                        className={`mk ${fired ? "fired" : ""} ${markerSelected ? "sel" : ""}`}
                        style={{ left: `${(atSeconds / block.duration_s) * 100}%` }}
                        title={`${markerLabel(marker.kind, providers)} · t=${absT.toFixed(1)}s${
                          marker.kind === "wait" ? " · 播放到此暂停" : ""
                        }`}
                        onClick={(event) => {
                          event.stopPropagation();
                          onSelect({ kind: "marker", blockId: block.id, markerId: marker.id });
                        }}
                        onPointerDown={(event) => markerDragStart(event, block, marker)}
                      >
                        {markerIcon(marker.kind)}
                      </button>
                    );
                  })}

                  {block.type === "hold" && !locked ? (
                    <span
                      className="blk__trim"
                      title="拖拽修剪时长"
                      onPointerDown={(event) => trimStart(event, block)}
                    />
                  ) : null}
                </div>
              );
            })
          )}

          {playheadVisible ? (
            <div className="tl-playhead" style={{ left: `${(playheadT / total) * 100}%` }} />
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

      {addMarker ? (
        <Dialog label="添加标记" onClose={() => setAddMarker(null)}>
          <div className="sheet__head">
            <h2 className="sheet__title">添加标记</h2>
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

/** Marker position inside its block, in seconds (proportion → seconds). */
function markerTimeInBlockView(block: Block, marker: EventMarker): number {
  return block.type === "hold" ? marker.at : marker.at * block.duration_s;
}
