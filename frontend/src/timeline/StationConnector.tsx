import type { Block } from "../types";
import { EASING_LABEL } from "./easing";

type Transition = Extract<Block, { type: "transition" }>;

interface Props {
  block: Transition;
  isCurrent: boolean;
  selected: boolean;
  onSelect: () => void;
}

/**
 * The connector between two stations: the station-density face of a
 * transition block. It shows duration and easing and opens the inspector on
 * tap — it can never be deleted by hand (that is the physics talking).
 */
export function StationConnector({ block, isCurrent, selected, onSelect }: Props) {
  return (
    <button
      type="button"
      className={`stn-link ${isCurrent ? "cur" : ""} ${selected ? "sel" : ""}`}
      title="过渡 — 点选后在检查器里改时长与缓动；自动生成、不可删"
      onClick={(event) => {
        event.stopPropagation();
        onSelect();
      }}
    >
      <span className="stn-link__arrow">→</span>
      <span className="num">{block.duration_s.toFixed(1)}s</span>
      <span className="stn-link__easing">{EASING_LABEL[block.easing]}</span>
    </button>
  );
}
