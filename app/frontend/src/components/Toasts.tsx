import { createContext, useCallback, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";

type Kind = "info" | "success" | "error";

/** An optional single action offered alongside the message, e.g. 撤销. */
interface ToastAction {
  label: string;
  run: () => void;
}

interface Toast {
  id: number;
  kind: Kind;
  text: string;
  action?: ToastAction;
}

/** Info and success expire. Errors do not — see below. */
const DISMISS_MS: Record<Kind, number | null> = { info: 3500, success: 3000, error: null };

/**
 * Fired when an error toast's "看日志" is pressed. The log drawer listens for
 * it. A custom event rather than a prop because "any failure anywhere should
 * be able to open the log" is genuinely cross-cutting, and threading a
 * callback through the toast provider would put the drawer's state above the
 * provider that every component needs.
 */
export const OPEN_LOG_EVENT = "rebot:open-log";

interface ToastApi {
  show: (kind: Kind, text: string, action?: ToastAction) => void;
  /** Run something, reporting failure as a toast rather than an unhandled rejection. */
  attempt: <T>(action: () => Promise<T>, success?: string) => Promise<T | undefined>;
}

const Context = createContext<ToastApi | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((t) => t.id !== id));
  }, []);

  const show = useCallback<ToastApi["show"]>(
    (kind, text, action) => {
      const id = Date.now() + Math.random();
      setToasts((current) => [...current, { id, kind, text, action }]);

      // Errors stay until dismissed. The operator is frequently standing at
      // the arm rather than at the screen, and a message that timed out
      // before they looked up is the same as no message at all.
      // An offer to undo has to outlive the glance that notices it.
      const ttl = action ? 8000 : DISMISS_MS[kind];
      if (ttl !== null) window.setTimeout(() => dismiss(id), ttl);
    },
    [dismiss],
  );

  const attempt = useCallback<ToastApi["attempt"]>(
    async (action, success) => {
      try {
        const result = await action();
        if (success) show("success", success);
        return result;
      } catch (error) {
        show("error", error instanceof Error ? error.message : String(error));
        return undefined;
      }
    },
    [show],
  );

  const api = useMemo(() => ({ show, attempt }), [show, attempt]);

  return (
    <Context.Provider value={api}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((toast) => (
          <div key={toast.id} className={`toast ${toast.kind}`}>
            <span className="toast__text">{toast.text}</span>
            {toast.action ? (
              <button
                className="toast__action"
                onClick={() => {
                  toast.action?.run();
                  dismiss(toast.id);
                }}
              >
                {toast.action.label}
              </button>
            ) : (
              toast.kind === "error" && (
                <button
                  className="toast__action"
                  onClick={() => window.dispatchEvent(new CustomEvent(OPEN_LOG_EVENT))}
                >
                  看日志
                </button>
              )
            )}
            <button className="toast__close" aria-label="关闭提示" onClick={() => dismiss(toast.id)}>
              ✕
            </button>
          </div>
        ))}
      </div>
    </Context.Provider>
  );
}

export function useToast(): ToastApi {
  const api = useContext(Context);
  if (!api) throw new Error("useToast outside ToastProvider");
  return api;
}
