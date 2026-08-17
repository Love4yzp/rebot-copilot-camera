import { useEffect, useRef } from "react";
import type { Pose } from "./types";

/**
 * Number keys fire the first nine poses: the operator's hands are usually on
 * the camera or the arm, and the same binding takes a foot pedal.
 */
export function useNumberKeys(poses: Pose[], goto: (pose: Pose) => void): void {
  const posesRef = useRef(poses);
  posesRef.current = poses;
  const gotoRef = useRef(goto);
  gotoRef.current = goto;
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const node = event.target as HTMLElement | null;
      if (node && /^(INPUT|TEXTAREA|SELECT)$/.test(node.tagName)) return;
      if (node?.closest("[role='dialog']")) return;
      const digit = Number(event.key);
      if (!Number.isInteger(digit) || digit < 1 || digit > 9) return;
      const pose = posesRef.current[digit - 1];
      if (!pose) return;
      event.preventDefault();
      void gotoRef.current(pose);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}
