import { createContext, useCallback, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";

type Kind = "info" | "success" | "error";
interface Toast {
  id: number;
  kind: Kind;
  text: string;
}

const DISMISS_MS = { info: 3500, success: 3000, error: 9000 } as const;

interface ToastApi {
  show: (kind: Kind, text: string) => void;
  /** Run something, reporting failure as a toast rather than an unhandled rejection. */
  attempt: <T>(action: () => Promise<T>, success?: string) => Promise<T | undefined>;
}

const Context = createContext<ToastApi | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const show = useCallback((kind: Kind, text: string) => {
    const id = Date.now() + Math.random();
    setToasts((current) => [...current, { id, kind, text }]);
    // Errors linger: the operator may be at the arm rather than the screen when
    // one appears, and a message they never saw is the same as no message.
    window.setTimeout(
      () => setToasts((current) => current.filter((t) => t.id !== id)),
      DISMISS_MS[kind],
    );
  }, []);

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
            {toast.text}
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
