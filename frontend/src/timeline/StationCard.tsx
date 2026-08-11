import type { Block, Pose, ProviderInfo } from "../types";
import { MIN_HOLD_S } from "./model";
import { markerIcon, markerLabel } from "./markers";
import type { Selection } from "./selection";

type Hold = Extract<Block, { type: "hold" }>;

interface Props {
  block: Hold;
  /** 1-based number among the stations (holds only). */
  index: number;
  /** Undefined when the station links a deleted pose. */
  pose: Pose | undefined;
  isCurrent: boolean;
  locked: boolean;
  selection: Selection;
  providers: ProviderInfo[];
  canMovePrev: boolean;
  canMoveNext: boolean;
  onSelect: () => void;
  onSelectMarker: (markerId: string) => void;
  onRemoveMarker: (markerId: string) => void;
  onSetDuration: (seconds: number) => void;
  onAddMarker: () => void;
  onMove: (dir: -1 | 1) => void;
  onRemove: () => void;
}

const snap = (v: number, step: number) => Math.round(v / step) * step;
const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/**
 * A station card: the station-density face of a hold block — pose name, a
 * hold-duration stepper, action chips, and an explicit 「＋ 动作」. A station
 * is the user's task-flow unit (站位 = hold + its actions + the outgoing
 * transition); every edit goes back to the parent's single patch path.
 */
export function StationCard({
  block,
  index,
  pose,
  isCurrent,
  locked,
  selection,
  providers,
  canMovePrev,
  canMoveNext,
  onSelect,
  onSelectMarker,
  onRemoveMarker,
  onSetDuration,
  onAddMarker,
  onMove,
  onRemove,
}: Props) {
  const selected = selection?.kind === "block" && selection.id === block.id;
  const duration = block.duration_s;
  const setDuration = (next: number) =>
    onSetDuration(clamp(snap(next, 0.1), MIN_HOLD_S, 60));

  return (
    <div
      className={[
        "stn",
        isCurrent ? "cur" : "",
        selected ? "sel" : "",
        !pose ? "missing" : "",
      ].join(" ")}
      onClick={onSelect}
      role="button"
      tabIndex={0}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelect();
        }
      }}
    >
      <div className="stn__head">
        <span className="stn__idx num">{index}</span>
        <span className="stn__name">{pose?.name ?? "已删除位姿"}</span>
      </div>

      <div className="stn__dur">
        <span className="stn__dur-label">停留</span>
        <button
          type="button"
          disabled={locked || duration <= MIN_HOLD_S}
          title="减少停留"
          onClick={(event) => {
            event.stopPropagation();
            setDuration(duration - 0.1);
          }}
        >
          −
        </button>
        <span className="num">{duration.toFixed(1)}s</span>
        <button
          type="button"
          disabled={locked || duration >= 60}
          title="增加停留"
          onClick={(event) => {
            event.stopPropagation();
            setDuration(duration + 0.1);
          }}
        >
          ＋
        </button>
      </div>

      <div className="stn__markers">
        {block.markers.map((marker) => {
          const markerSelected =
            selection?.kind === "marker" && selection.markerId === marker.id;
          return (
            <span key={marker.id} className={`chip ${markerSelected ? "sel" : ""}`}>
              <button
                type="button"
                className="chip__main"
                title="点选后在检查器里调参数"
                onClick={(event) => {
                  event.stopPropagation();
                  onSelectMarker(marker.id);
                }}
              >
                {markerIcon(marker.kind)} {markerLabel(marker.kind, providers)}
                <span className="num">@{marker.at.toFixed(1)}s</span>
              </button>
              {!locked ? (
                <button
                  type="button"
                  className="chip__x"
                  title="移除该动作"
                  onClick={(event) => {
                    event.stopPropagation();
                    onRemoveMarker(marker.id);
                  }}
                >
                  ×
                </button>
              ) : null}
            </span>
          );
        })}
        {!locked ? (
          <button
            type="button"
            className="stn__add"
            onClick={(event) => {
              event.stopPropagation();
              onAddMarker();
            }}
          >
            ＋ 动作
          </button>
        ) : null}
      </div>

      {selected && !locked ? (
        <div className="stn__ops">
          <button
            type="button"
            disabled={!canMovePrev}
            title="前移一站"
            onClick={(event) => {
              event.stopPropagation();
              onMove(-1);
            }}
          >
            ‹
          </button>
          <button
            type="button"
            disabled={!canMoveNext}
            title="后移一站"
            onClick={(event) => {
              event.stopPropagation();
              onMove(1);
            }}
          >
            ›
          </button>
          <button
            type="button"
            className="ghost"
            onClick={(event) => {
              event.stopPropagation();
              onRemove();
            }}
          >
            删除
          </button>
        </div>
      ) : null}
    </div>
  );
}
