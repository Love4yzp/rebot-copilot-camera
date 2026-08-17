/**
 * Full-screen "you are now in PROD mode" warning.
 *
 * Shown once per transition into prod (from sim or from unknown). The spec
 * (rebot-arm thread fdf6a140) calls for a *blocking* warning: an operator who
 * believes they are still driving the simulator is the most dangerous human
 * factor in this app, and a toast that expires in 3.5s is not enough.
 *
 * Deliberately NOT a Dialog: Dialog intercepts Escape and closes, but Escape
 * is the emergency stop's shortcut (window-level listener). While this warning
 * is up, Escape must keep stopping the arm — so it renders its own overlay at
 * z-index 55, below the estop bar (60), and does not touch the event.
 */
interface Props {
  onAcknowledge: () => void;
}

export function ModeWarning({ onAcknowledge }: Props) {
  return (
    <div className="mode-warning">
      <div className="mode-warning__panel" role="alertdialog" aria-modal="true" aria-label="PROD 模式警告">
        <h2 className="mode-warning__title">
          <span className="mode-warning__icon" aria-hidden="true">
            ⚠
          </span>
          现在处于 PROD 模式
        </h2>
        <p className="mode-warning__body">
          操作将直接驱动机械臂。请确认机械臂周围没有人员和障碍物，再继续操作。
        </p>
        <button type="button" className="primary" onClick={onAcknowledge}>
          我已了解，继续
        </button>
      </div>
    </div>
  );
}
