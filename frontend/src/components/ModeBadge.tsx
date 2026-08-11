import type { AppMode } from "../types";

interface Props {
  mode: AppMode | null;
  moving: boolean;
  connected: boolean;
}

const MODE_LABEL: Record<AppMode, string> = {
  sim: "SIM · 演示模式",
  prod: "PROD · 正式运行",
};

/**
 * Persistent mode badge in the top bar, visible on every page.
 *
 * Colour discipline follows the project's four-colour machine-status channel:
 * the badge itself is grey-scale (sim=blue info, prod=grey+bold+icon) and only
 * borrows amber when the arm is actually moving. Disconnected shows grey pulse.
 */
export function ModeBadge({ mode, moving, connected }: Props) {
  if (!connected) {
    return (
      <span className="mode-badge mode-badge--disconnected">
        <span className="mode-badge__pulse" />
        已断连
      </span>
    );
  }

  if (!mode) {
    return (
      <span className="mode-badge mode-badge--unknown">
        <span className="mode-badge__dot" />
        —
      </span>
    );
  }

  const isMoving = moving && mode === "prod";

  // prod gets a ✓ glyph (spec: grey-scale + bold + icon); sim keeps the dot.
  const lead = mode === "prod" ? (
    <span className={`mode-badge__tick ${isMoving ? "mode-badge__tick--moving" : ""}`} aria-hidden="true">
      ✓
    </span>
  ) : (
    <span className={`mode-badge__dot ${isMoving ? "mode-badge__dot--moving" : ""}`} />
  );

  return (
    <span
      className={`mode-badge mode-badge--${mode} ${isMoving ? "mode-badge--moving" : ""}`}
      title={mode === "prod" ? "后端直连机械臂，操作将实际驱动" : "仅前端演示，机械臂不会动"}
    >
      {lead}
      {MODE_LABEL[mode]}
    </span>
  );
}