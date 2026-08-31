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
 * borrows amber when the arm is actually moving.
 *
 * The mode badge and the connection state are two independent dimensions and
 * must not overwrite each other: "this is the simulator" stays true whether or
 * not the websocket is up. Disconnection renders as its own grey pulse beside
 * the mode, not instead of it. (Spec: rebot-arm thread fdf6a140, acceptance.)
 */
export function ModeBadge({ mode, moving, connected }: Props) {
  const isMoving = moving && mode === "prod";

  // prod gets a ✓ glyph (spec: grey-scale + bold + icon); sim keeps the dot.
  const lead = mode === "prod" ? (
    <span className={`mode-badge__tick ${isMoving ? "mode-badge__tick--moving" : ""}`} aria-hidden="true">
      ✓
    </span>
  ) : (
    <span className="mode-badge__dot" />
  );

  const label = mode === "prod" ? "后端直连机械臂，操作将实际驱动" : "模拟器/演示模式，机械臂不会动";

  if (!mode) {
    return (
      <span className="mode-badge mode-badge--unknown" title={label}>
        <span className="mode-badge__dot" />
        —
        {connected ? null : <DisconnectedMark />}
      </span>
    );
  }

  return (
    <span className="mode-badge-group" title={label}>
      <span className={`mode-badge mode-badge--${mode} ${isMoving ? "mode-badge--moving" : ""}`}>
        {lead}
        {MODE_LABEL[mode]}
      </span>
      {connected ? null : <DisconnectedMark />}
    </span>
  );
}

/** Grey pulse beside the mode when the websocket is down. No colour: red is
 * reserved for the estop, amber for motion. */
function DisconnectedMark() {
  return (
    <span className="mode-badge__disconnected">
      <span className="mode-badge__pulse" />
      已断连
    </span>
  );
}