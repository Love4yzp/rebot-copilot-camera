import { useEffect, useRef, useState } from "react";
import { OPEN_LOG_EVENT } from "./Toasts";

interface LogResponse {
  available: boolean;
  lines: string[];
  note: string | null;
}

const POLL_MS = 3000;

/**
 * Service log, in the UI.
 *
 * The operator is on the far end of an SSH tunnel with a browser, not a
 * terminal. This is the difference between "the arm stopped and I do not know
 * why" and reading the watchdog's reason. Error toasts carry a "看日志" button
 * that opens this drawer, so the question and the answer are one tap apart.
 *
 * Only polls while open, because it shells out to journalctl on the device.
 */
export function LogDrawer({ rateHz }: { rateHz: number }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<LogResponse | null>(null);
  const body = useRef<HTMLPreElement>(null);

  useEffect(() => {
    const onOpen = () => setOpen(true);
    window.addEventListener(OPEN_LOG_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_LOG_EVENT, onOpen);
  }, []);

  useEffect(() => {
    if (!open) return;

    let cancelled = false;
    const load = async () => {
      try {
        const response = await fetch("/api/logs?lines=300");
        const payload: LogResponse = await response.json();
        if (!cancelled) setData(payload);
      } catch {
        if (!cancelled) setData({ available: false, lines: [], note: "无法读取日志" });
      }
    };

    void load();
    const timer = window.setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [open]);

  // Follow the tail. The interesting line is almost always the newest one.
  useEffect(() => {
    if (body.current) body.current.scrollTop = body.current.scrollHeight;
  }, [data]);

  return (
    <div className="log-drawer">
      <div className="log-head">
        <button className="log-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? "收起日志" : "日志"}
        </button>
        {/* The control-loop rate is a diagnostic, not something the operator
          * acts on, so it lives here rather than beside the emergency stop. */}
        <span className="log-rate">{rateHz.toFixed(0)} Hz</span>
      </div>

      {open && (
        <pre className="log-body" ref={body}>
          {data === null
            ? "读取中…"
            : data.available
              ? data.lines.join("\n")
              : (data.note ?? "无日志")}
        </pre>
      )}
    </div>
  );
}
