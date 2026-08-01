import type { CSSProperties } from "react";

export type AnchorCardStatus =
  | "idle"
  /** Tapped; the request is in flight and the arm has not reported yet. */
  | "pending"
  | "moving"
  | "settling"
  | "acting"
  | "arrived";

interface Props {
  name: string;
  /** 1-based position, and the number key / foot pedal that fires this anchor. */
  keyNumber: number;
  triggerCount: number | null;
  status: AnchorCardStatus;
  /** Commanded travel time, used to pace the in-card progress fill. */
  durationS: number;
  /** Motion is unavailable right now — dim, but still tappable so it can explain why. */
  unavailable: boolean;
  /** Config mode; the tap edits instead of moving the arm. */
  config: boolean;
  onActivate: () => void;
}

const STATUS_LABEL: Record<AnchorCardStatus, string | null> = {
  idle: null,
  pending: "出发中",
  moving: "移动中",
  settling: "稳定中",
  acting: "触发中",
  arrived: "已到位",
};

/** The fill only means "travelling"; arrival has its own green treatment. */
const FILLS: AnchorCardStatus[] = ["moving", "settling", "acting"];

/**
 * One anchor, and in use mode the whole product: tap it, the arm goes there,
 * settles, fires, and holds.
 *
 * Feedback lands on the thing that was touched. Travel progress fills the card
 * itself rather than a bar at the opposite edge of the screen, so the object
 * the operator pressed is the object that reports back. The shutter is one
 * white frame across the card, because a shutter is light.
 *
 * The card is one <button>; the tap target is the whole card, far above the
 * 44 px floor. It stays enabled even when motion is unavailable — a dead
 * control that absorbs a tap in silence tells the operator nothing, so the tap
 * is accepted and answered with the reason instead.
 */
export function AnchorCard({
  name,
  keyNumber,
  triggerCount,
  status,
  durationS,
  unavailable,
  config,
  onActivate,
}: Props) {
  const label = STATUS_LABEL[status];

  return (
    <button
      type="button"
      className={[
        "anchor-card",
        status !== "idle" ? `anchor-card--${status}` : "",
        unavailable ? "anchor-card--unavailable" : "",
      ]
        .filter(Boolean)
        .join(" ")}
      onClick={onActivate}
    >
      {FILLS.includes(status) && (
        <span
          className="anchor-card__fill"
          style={{ "--travel": `${durationS}s` } as CSSProperties}
        />
      )}
      {status === "acting" && <span className="anchor-card__flash" />}

      <span className="anchor-card__key">{keyNumber}</span>
      <span className="anchor-card__name">{name}</span>
      <span className="anchor-card__meta">
        {triggerCount !== null && <span className="anchor-card__trigger">×{triggerCount}</span>}
        {label && <span className="anchor-card__state">{label}</span>}
        {config && !label && <span className="anchor-card__state">点击编辑</span>}
      </span>
    </button>
  );
}
