import { useEffect } from "react";
import { api } from "../api";
import type { EstopState, Mode } from "../types";
import { useToast } from "./Toasts";

interface Props {
  estop: EstopState | null;
  mode: Mode | null;
  connected: boolean;
}

/** Machine modes, named for what the operator sees rather than what the backend calls them. */
const MODE_LABEL: Record<Mode, string> = {
  idle: "待命",
  teach: "示教中",
  playback: "运行中",
  estop: "已急停",
};

/**
 * Always on screen, always the first thing in the layout, and always on top of
 * the stacking order (z-index 60, above every dialog backdrop at 40).
 *
 * The stop has to be reachable without looking for it, so it never scrolls
 * away, never moves, never shares its red with anything else in the interface,
 * and — the part that is easy to get wrong — never ends up underneath an
 * overlay. Teach mode is exactly when the operator's hands are on the arm and
 * exactly when a sheet is open.
 */
export function EstopBar({ estop, mode, connected }: Props) {
  const { attempt } = useToast();
  const latched = estop?.latched ?? false;

  // Escape engages. Chosen because it is the one key an operator can find with
  // their eyes on the arm, and because it does nothing destructive if pressed
  // by mistake -- an unnecessary stop costs a few seconds, a missed one costs
  // the arm. Dialogs stop the event before it reaches here (see Dialog.tsx),
  // so with a sheet open Escape closes the sheet instead.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || latched) return;
      const target = event.target as HTMLElement | null;
      if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return;
      void attempt(() => api.estop.engage("operator pressed Escape"));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [latched, attempt]);

  return (
    <div className={`estop-bar ${latched ? "latched" : ""}`}>
      {latched ? (
        <button
          className="estop-button clear"
          onClick={() => attempt(() => api.estop.clear(), "已解除，臂原地待命")}
        >
          解除急停
        </button>
      ) : (
        <button
          className="estop-button"
          onClick={() => attempt(() => api.estop.engage("operator pressed stop"))}
        >
          急停
        </button>
      )}

      <div className="estop-reason">
        {latched ? (
          <>
            <strong>已急停</strong>
            {estop?.reason ? ` — ${estop.reason}` : null}
            {estop?.source ? <span className="num"> [{estop.source}]</span> : null}
            <span> · 解除后原地待命，不会自动继续</span>
          </>
        ) : (
          <>
            <span className="estop-key">Esc</span> 急停 · 臂保持力矩，不掉电
          </>
        )}
      </div>

      <span className="mode-chip" data-mode={mode ?? "idle"}>
        {mode ? MODE_LABEL[mode] : "—"}
      </span>

      {/* The connection state used to live in a title= attribute, which on the
        * iPad this runs on is the same as not existing. */}
      <span className={`link-state ${connected ? "" : "down"}`}>
        <span className="link-dot" />
        <span>{connected ? "已连接" : "重连中"}</span>
      </span>
    </div>
  );
}
