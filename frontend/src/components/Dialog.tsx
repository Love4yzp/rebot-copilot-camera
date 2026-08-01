import { useEffect, useRef } from "react";
import type { ReactNode } from "react";

const FOCUSABLE =
  'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

interface Props {
  /** Accessible name for the dialog. */
  label: string;
  onClose: () => void;
  children: ReactNode;
}

/**
 * Modal shell: backdrop, focus trap, Escape, restore focus.
 *
 * Two things here are load-bearing beyond the usual dialog hygiene.
 *
 * The backdrop sits at z-index 40 and the emergency stop bar at 60, so the
 * stop stays clickable while a dialog is open. It is not decorative layering:
 * an operator with a dialog on screen is an operator who may still need to
 * stop the arm, and a backdrop that eats that click is a backdrop that eats
 * the only control that matters.
 *
 * Escape is caught with a *native* listener on the panel and stopped there.
 * Escape is also the emergency stop's shortcut (a window-level listener), and
 * React's synthetic stopPropagation would not reach it — the native event
 * bubbles past React's root to window regardless. Stopping it on the panel
 * means: dialog open, Escape closes the dialog; no dialog, Escape stops the
 * arm. The focus trap guarantees the event really does originate inside.
 */
export function Dialog({ label, onClose, children }: Props) {
  const panel = useRef<HTMLDivElement>(null);
  const restore = useRef<HTMLElement | null>(null);

  useEffect(() => {
    restore.current = document.activeElement as HTMLElement | null;
    const node = panel.current;
    if (!node) return;

    const focusables = () =>
      [...node.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => !el.hasAttribute("disabled"));

    focusables()[0]?.focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab") return;

      const items = focusables();
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];

      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    node.addEventListener("keydown", onKeyDown);
    return () => {
      node.removeEventListener("keydown", onKeyDown);
      restore.current?.focus?.();
    };
  }, [onClose]);

  return (
    <div
      className="sheet__backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="sheet" role="dialog" aria-modal="true" aria-label={label} ref={panel}>
        {children}
      </div>
    </div>
  );
}
