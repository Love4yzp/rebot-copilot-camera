import type { Routine, ShutterAction } from "../types";
import { AnchorCard } from "./AnchorCard";
import type { AnchorCardStatus } from "./AnchorCard";

interface Props {
  routine: Routine | null;
  /** True when there are no collections at all, not merely none selected. */
  noCollections: boolean;
  /** Config mode: taps edit, cards grow reorder and test-run handles. */
  config: boolean;
  /** Per-index status, resolved by App from the live playback broadcast. */
  statusAt: (index: number) => AnchorCardStatus;
  /** Motion is unavailable (latched / busy) — cards dim but stay tappable. */
  motionBlocked: boolean;
  onGoto: (index: number) => void;
  onEditAnchor: (index: number) => void;
  onMove: (index: number, dir: -1 | 1) => void;
  onCreateCollection: () => void;
  onRecordFirst: () => void;
}

/**
 * The anchor board — the whole use layer.
 *
 * Owns no playback state and issues no API calls: App resolves each card's
 * status from the /ws broadcast and owns goto / reorder / edit through the
 * callbacks. The board's only jobs are the grid, the empty states, and the
 * config-mode handles.
 */
export function AnchorBoard({
  routine,
  noCollections,
  config,
  statusAt,
  motionBlocked,
  onGoto,
  onEditAnchor,
  onMove,
  onCreateCollection,
  onRecordFirst,
}: Props) {
  if (!routine) {
    // An empty screen is an invitation, so the action lives here rather than
    // being pointed at somewhere else on the page.
    return (
      <div className={`board${config ? " config" : ""}`}>
        <div className="empty">
          <h2>{noCollections ? "还没有集合" : "选一个集合"}</h2>
          <p>
            {noCollections
              ? "集合是一组拍摄机位。新建一个，把臂拖到每个位置存下来，之后点卡片就能回到那里。"
              : "上方的胶囊是你的集合，点一个开始。"}
          </p>
          {noCollections && (
            <button className="primary touch-target" onClick={onCreateCollection}>
              新建集合
            </button>
          )}
        </div>
      </div>
    );
  }

  if (routine.waypoints.length === 0) {
    return (
      <div className={`board${config ? " config" : ""}`}>
        <div className="empty">
          <h2>「{routine.name}」还没有锚点</h2>
          <p>用手把臂拖到想拍的位置，存下来。存好的位置会变成一张卡片，点一下臂就回到那里。</p>
          <button className="primary touch-target" onClick={onRecordFirst}>
            录第一个锚点
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className={`board${config ? " config" : ""}`}>
      {config && (
        <div className="board-banner">
          <span className="engrave">配置模式</span>
          <span>点卡片改名和设触发，臂不会动。用「试跑」单独验证一个锚点。</span>
        </div>
      )}

      <div className="anchor-grid">
        {routine.waypoints.map((waypoint, index) => {
          const shutter = waypoint.actions.find(
            (a): a is ShutterAction => a.type === "shutter",
          );
          const status = statusAt(index);

          return (
            <div key={waypoint.id} className="anchor-slot">
              <AnchorCard
                name={waypoint.note.trim() || `锚点 ${index + 1}`}
                keyNumber={index + 1}
                triggerCount={shutter ? shutter.count : null}
                status={status}
                durationS={waypoint.duration_s}
                // The card the operator just touched is never the dimmest
                // thing on screen — only the genuinely idle ones go quiet.
                unavailable={!config && motionBlocked && status === "idle"}
                config={config}
                onActivate={() => (config ? onEditAnchor(index) : onGoto(index))}
              />

              {config && (
                <div className="anchor-slot__tools">
                  <button
                    className="ghost"
                    aria-label={`把「${waypoint.note.trim() || `锚点 ${index + 1}`}」左移`}
                    disabled={index === 0}
                    onClick={() => onMove(index, -1)}
                  >
                    ←
                  </button>
                  {/* Recording an anchor and checking it should not cost a mode
                    * switch each way — a four-anchor session would pay eight. */}
                  <button
                    className="ghost try"
                    disabled={motionBlocked}
                    onClick={() => onGoto(index)}
                  >
                    试跑
                  </button>
                  <button
                    className="ghost"
                    aria-label={`把「${waypoint.note.trim() || `锚点 ${index + 1}`}」右移`}
                    disabled={index === routine.waypoints.length - 1}
                    onClick={() => onMove(index, 1)}
                  >
                    →
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
