export type TallyState =
  | "idle"
  | "moving"
  | "settling"
  | "acting"
  | "arrived"
  | "teach"
  | "latched";

/**
 * The machine's state, as a light bar.
 *
 * The operator stands at the arm, not at the browser. They cannot read a 12 px
 * status word from two metres away, but they can read a full-width bar of
 * colour in peripheral vision without turning their head — which is the entire
 * reason tally lights exist in a studio in the first place. This borrows the
 * convention rather than inventing one:
 *
 *   dark          nothing is happening
 *   amber sweep   the arm is travelling — do not reach in
 *   amber solid   teach mode: the arm is compliant and can be pushed by hand
 *   white         the shutter fired
 *   green         arrived and holding
 *   red pulse     latched
 *
 * Movement and stop never share a colour or a rhythm: travel sweeps, the stop
 * pulses. That difference survives red/amber colour blindness, and both states
 * are also spelled out in words elsewhere on screen.
 *
 * Hidden from screen readers: every state it shows is already announced by the
 * mode chip and the card status, and a bar that re-announces on every phase
 * change would be pure noise.
 */
export function TallyRail({ state }: { state: TallyState }) {
  return <div className="tally" data-state={state} aria-hidden />;
}
