import type { DragEvent, PointerEvent as ReactPointerEvent, RefObject } from "react";
import type { Block, EventMarker, Pose, ProviderInfo } from "../types";
import { markerAbsTime } from "./model";
import { markerIcon, markerLabel } from "./markers";
import { EASING_LABEL } from "./easing";
import type { Selection } from "./selection";

interface Props {
  block: Block;
  /** This block's absolute start time on the plan ruler. */
  start: number;
  /** Width as a percentage of the (possibly zoomed) track. */
  widthPct: number;
  isCurrent: boolean;
  locked: boolean;
  playheadVisible: boolean;
  playheadT: number;
  /** Undefined when the hold links a deleted pose. */
  pose: Pose | undefined;
  selection: Selection;
  providers: ProviderInfo[];
  /** Shared drag guard: inner pointer work must not start an HTML5 drag. */
  noDragRef: RefObject<boolean>;
  onSelectBlock: (id: string) => void;
  onSelectMarker: (blockId: string, markerId: string) => void;
  onBlockDragStart: (event: DragEvent, block: Block) => void;
  onBlockDoubleClick: (event: ReactPointerEvent | React.MouseEvent, block: Block) => void;
  onTrimStart: (event: ReactPointerEvent, block: Block) => void;
  onMarkerDragStart: (event: ReactPointerEvent, block: Block, marker: EventMarker) => void;
  onAddMarker: (block: Block) => void;
}

/**
 * One block on the timeline (precision) density: a hold with its pinned
 * markers and trim handle, or an auto-generated transition. All editing logic
 * lives in the parent — this is a renderer with callbacks.
 */
export function TrackBlock({
  block,
  start,
  widthPct,
  isCurrent,
  locked,
  playheadVisible,
  playheadT,
  pose,
  selection,
  providers,
  noDragRef,
  onSelectBlock,
  onSelectMarker,
  onBlockDragStart,
  onBlockDoubleClick,
  onTrimStart,
  onMarkerDragStart,
  onAddMarker,
}: Props) {
  const selected = selection?.kind === "block" && selection.id === block.id;
  return (
    <div
      className={[
        "blk",
        block.type === "hold" ? "hold" : "trans",
        isCurrent ? "cur" : "",
        selected ? "sel" : "",
        block.type === "hold" && !pose ? "missing" : "",
      ].join(" ")}
      style={{ width: `${widthPct}%` }}
      draggable={!locked && block.type === "hold"}
      onDragStart={(event) => onBlockDragStart(event, block)}
      onClick={() => onSelectBlock(block.id)}
      onDoubleClick={(event) => onBlockDoubleClick(event, block)}
    >
      {block.type === "hold" ? (
        <div className="blk__in">
          <span className="blk__name">{pose?.name ?? "已删除位姿"}</span>
          <span className="blk__dur num">{block.duration_s.toFixed(1)}s</span>
        </div>
      ) : (
        <div className="blk__in trans">
          <span className="num">{block.duration_s.toFixed(1)}s</span> {EASING_LABEL[block.easing]}
        </div>
      )}

      {block.markers.map((marker) => {
        const atSeconds = markerTimeInBlockView(block, marker);
        const absT = markerAbsTime(start, block, marker);
        const fired = playheadVisible && playheadT >= absT - 1e-9 && playheadT > 0;
        const markerSelected = selection?.kind === "marker" && selection.markerId === marker.id;
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
              onSelectMarker(block.id, marker.id);
            }}
            onPointerDown={(event) => onMarkerDragStart(event, block, marker)}
          >
            {markerIcon(marker.kind)}
          </button>
        );
      })}

      {block.type === "hold" && !locked ? (
        <span
          className="blk__trim"
          title="拖拽修剪时长"
          onPointerDown={(event) => onTrimStart(event, block)}
        />
      ) : null}

      {selected && !locked ? (
        <button
          type="button"
          className="blk__add"
          title="在此块添加动作（快门 / 等待…）"
          onPointerDown={(event) => {
            // The button sits inside a draggable hold: swallow the gesture so
            // a press never picks the whole block up.
            event.stopPropagation();
            noDragRef.current = true;
            window.addEventListener(
              "pointerup",
              () => {
                noDragRef.current = false;
              },
              { once: true },
            );
          }}
          onClick={(event) => {
            event.stopPropagation();
            onAddMarker(block);
          }}
        >
          ＋ 动作
        </button>
      ) : null}
    </div>
  );
}

/** Marker position inside its block, in seconds (proportion → seconds). */
function markerTimeInBlockView(block: Block, marker: EventMarker): number {
  return block.type === "hold" ? marker.at : marker.at * block.duration_s;
}
