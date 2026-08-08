import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Block } from "../types";
import { markerSchedule, poseAtTime, sequenceDuration } from "../timeline/model";
import type { PoseMap, ScheduledMarker } from "../timeline/model";

export interface PreviewApi {
  /** A preview session is engaged: playhead visible, monitor shows the plan pose. */
  active: boolean;
  playing: boolean;
  /** Suspended on a wait marker until 继续. */
  waiting: boolean;
  /** Playhead position on the plan ruler, seconds. */
  t: number;
  /** The plan pose at `t` — null while no session is engaged. */
  pose: Record<string, number> | null;
  start: () => void;
  pause: () => void;
  resume: () => void;
  stop: () => void;
  /** Scrub: set the playhead directly (engages a paused session). */
  seek: (t: number) => void;
  continueWait: () => void;
}

/**
 * The preview engine: plays the *plan*, never the arm.
 *
 * A requestAnimationFrame loop walks the playhead along the plan ruler while
 * the monitor interpolates the pose itself — the arm does not move a
 * millimetre, which is the entire difference between 预演 and 执行. Wait
 * markers suspend the loop exactly as they suspend a real run, so the
 * operator rehearses the pauses too.
 *
 * Preview is not a machine state: nothing here touches a status colour. The
 * root `previewing` class (applied by App while `active`) keeps the whole
 * interface on the grey ramp for the session's lifetime.
 */
export function usePreview(blocks: Block[], poses: PoseMap): PreviewApi {
  const [active, setActive] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [waiting, setWaiting] = useState(false);
  const [t, setT] = useState(0);

  const total = useMemo(() => sequenceDuration(blocks), [blocks]);
  const waits = useMemo(
    () => markerSchedule(blocks).filter((s) => s.marker.kind === "wait"),
    [blocks],
  );

  // The rAF loop reads through refs so an edit never restarts it mid-run, and
  // tRef mirrors the playhead so the tick computes outside React updaters.
  const tRef = useRef(0);
  const totalRef = useRef(total);
  totalRef.current = total;
  const waitsRef = useRef<ScheduledMarker[]>(waits);
  waitsRef.current = waits;
  /** Wait markers already consumed in this run — resume must not re-trigger. */
  const consumed = useRef(new Set<string>());

  const setPlayhead = useCallback((next: number) => {
    tRef.current = next;
    setT(next);
  }, []);

  const stop = useCallback(() => {
    setActive(false);
    setPlaying(false);
    setWaiting(false);
    setPlayhead(0);
    consumed.current.clear();
  }, [setPlayhead]);

  const start = useCallback(() => {
    if (totalRef.current <= 0) return;
    consumed.current.clear();
    setPlayhead(0);
    setWaiting(false);
    setActive(true);
    setPlaying(true);
  }, [setPlayhead]);

  const pause = useCallback(() => setPlaying(false), []);

  const resume = useCallback(() => {
    if (totalRef.current <= 0) return;
    setActive(true);
    setPlaying(true);
  }, []);

  const continueWait = useCallback(() => {
    setWaiting(false);
    setPlaying(true);
  }, []);

  const seek = useCallback(
    (next: number) => {
      const clamped = Math.min(Math.max(next, 0), totalRef.current);
      setPlayhead(clamped);
      setWaiting(false);
      setActive(true);
      // Scrubbing re-arms the waits ahead of the playhead and consumes the
      // ones behind it, so the pause points still land where the ruler says.
      consumed.current = new Set(
        waitsRef.current.filter((s) => s.t <= clamped + 1e-9).map((s) => s.marker.id),
      );
    },
    [setPlayhead],
  );

  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    let last: number | null = null;

    const tick = (ts: number) => {
      if (last === null) last = ts;
      // A hidden tab stops the clock; coming back must not teleport the
      // playhead past a wait marker it would have stopped at.
      const dt = Math.min((ts - last) / 1000, 0.25);
      last = ts;

      const current = tRef.current;
      const next = current + dt;
      const wait = waitsRef.current.find(
        (s) => !consumed.current.has(s.marker.id) && s.t > current - 1e-9 && s.t <= next,
      );

      if (wait) {
        consumed.current.add(wait.marker.id);
        setPlayhead(wait.t);
        setPlaying(false);
        setWaiting(true);
        return;
      }
      if (next >= totalRef.current) {
        // Ran off the end: full reset to grey, like the prototype.
        setActive(false);
        setPlaying(false);
        setPlayhead(0);
        consumed.current.clear();
        return;
      }
      setPlayhead(next);
      raf = requestAnimationFrame(tick);
    };

    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, setPlayhead]);

  const pose = useMemo(
    () => (active && total > 0 ? poseAtTime(blocks, poses, t) : null),
    [active, total, blocks, poses, t],
  );

  return { active, playing, waiting, t, pose, start, pause, resume, stop, seek, continueWait };
}
