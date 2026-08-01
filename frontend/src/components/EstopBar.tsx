import { useEffect } from "react";
import { api } from "../api";
import type { EstopState, Mode } from "../types";
import { useToast } from "./Toasts";

interface Props {
  estop: EstopState | null;
  mode: Mode | null;
  rateHz: number;
  connected: boolean;
}

/**
 * Always on screen, always the first thing in the layout.
 *
 * The stop is the one control that has to be reachable without looking for it,
 * so it never scrolls away, never moves, and never shares its red with
 * anything else in the interface.
 */
export function EstopBar({ estop, mode, rateHz, connected }: Props) {
  const { attempt } = useToast();
  const latched = estop?.latched ?? false;

  // Escape engages. Chosen because it is the one key an operator can find with
  // their eyes on the arm, and because it does nothing destructive if pressed
  // by mistake -- an unnecessary stop costs a few seconds, a missed one costs
  // the arm.
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
          title="Esc"
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
            <span className="hint"> · 解除后原地待命，不会自动继续</span>
          </>
        ) : (
          <span style={{ color: "var(--text-faint)" }}>Esc 急停 · 臂保持力矩，不掉电</span>
        )}
      </div>

      <span className="mode-chip" data-mode={mode ?? "idle"}>
        {mode ?? "—"}
      </span>
      <span className="num" style={{ color: "var(--text-faint)", fontSize: 11 }}>
        {rateHz.toFixed(0)} Hz
      </span>
      <span
        className={`link-dot ${connected ? "" : "down"}`}
        title={connected ? "已连接控制循环" : "已断开，正在重连"}
      />
    </div>
  );
}
