import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { RefObject } from "react";
import type { Block, HoldBlock } from "../types";
import {
  DEFAULT_APPROACH_S,
  FIRST_APPROACH_MAX_SPEED,
  lerpPose,
  markerSchedule,
  maxJointDelta,
  poseAtTime,
  sequenceDuration,
} from "../timeline/model";
import type { PoseMap, ScheduledMarker } from "../timeline/model";

export interface PreviewApi {
  /** A preview session is engaged: playhead visible, monitor shows the plan pose. */
  active: boolean;
  playing: boolean;
  /** Suspended on a wait marker until 继续. */
  waiting: boolean;
  /** Approaching the first station from the arm's current position. */
  approaching: boolean;
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
export function usePreview(
  blocks: Block[],
  poses: PoseMap,
  approachFromRef?: RefObject<Record<string, number> | null>,
): PreviewApi {
  const [active, setActive] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [waiting, setWaiting] = useState(false);
  const [approaching, setApproaching] = useState(false);
  /** Approach progress 0..1, drives linear interpolation for the pose. */
  const [approachP, setApproachP] = useState(0);
  const [t, setT] = useState(0);

  const total = useMemo(() => sequenceDuration(blocks), [blocks]);
  const waits = useMemo(
    () => markerSchedule(blocks).filter((s) => s.marker.kind === "wait"),
    [blocks],
  );

  // Snapshots captured at start() time so edits don't change the approach mid-run.
  const approachFrom_ = useRef<Record<string, number> | null>(null);
  const approachDuration_ = useRef(0);
  const approachElapsed_ = useRef(0);

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
    setApproaching(false);
    setApproachP(0);
    setPlayhead(0);
    consumed.current.clear();
    approachElapsed_.current = 0;
  }, [setPlayhead]);

  const start = useCallback(() => {
    if (totalRef.current <= 0) return;
    consumed.current.clear();
    setPlayhead(0);
    setWaiting(false);

    // Check whether a visible approach (arm → first station) is needed.
    // Only start() triggers approach; seek/resume skip it.
    const raw = approachFromRef?.current ?? null;
    const firstHold = blocks.find((b): b is HoldBlock => b.type === "hold");
    if (raw && firstHold) {
      const to = poses[firstHold.pose_id];
      if (to) {
        const delta = maxJointDelta(raw, to);
        if (delta > 0.01) {
          const duration = Math.max(DEFAULT_APPROACH_S, delta / FIRST_APPROACH_MAX_SPEED);
          approachFrom_.current = raw;
          approachDuration_.current = duration;
          approachElapsed_.current = 0;
          setApproachP(0);
          setApproaching(true);
          setActive(true);
          setPlaying(false);
          return;
        }
      }
    }

    // No approach needed — start normal playback immediately.
    setActive(true);
    setPlaying(true);
  }, [setPlayhead, approachFromRef, blocks, poses]);

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
      // Scrubbing lands on the plan ruler directly — the approach pre-roll is
      // a start-of-run thing, not a point on the ruler, so a seek cancels it.
      setApproaching(false);
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

  // ── approach rAF loop ──────────────────────────────────────────────────────
  // Runs when approaching is true.  Steps the elapsed time forward and updates
  // approachP; when the duration is reached, transitions to normal playback.
  useEffect(() => {
    if (!approaching) return;
    let raf = 0;
    let last: number | null = null;

    const tick = (ts: number) => {
      if (last === null) last = ts;
      const dt = Math.min((ts - last) / 1000, 0.25);
      last = ts;

      const elapsed = approachElapsed_.current + dt;
      approachElapsed_.current = elapsed;
      const dur = approachDuration_.current;

      if (elapsed >= dur) {
        // Approach complete — start normal playback from t=0.
        setApproaching(false);
        setApproachP(0);
        setPlayhead(0);
        setPlaying(true);
        return;
      }

      setApproachP(elapsed / dur);
      raf = requestAnimationFrame(tick);
    };

    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [approaching, setPlayhead]);

  const pose = useMemo(() => {
    if (!active || total <= 0) return null;
    if (approaching) {
      const from = approachFrom_.current;
      const firstHold = blocks.find((b): b is HoldBlock => b.type === "hold");
      if (from && firstHold) {
        const to = poses[firstHold.pose_id];
        if (to) return lerpPose(from, to, approachP);
      }
    }
    return poseAtTime(blocks, poses, t);
  }, [active, total, blocks, poses, t, approaching, approachP]);

  return { active, playing, waiting, approaching, t, pose, start, pause, resume, stop, seek, continueWait };
}
