import { useEffect, useState } from "react";

/**
 * Track a media query as React state.
 *
 * Used for the phone breakpoint: narrow viewports force the tap-to-go face
 * (clip editing is a desktop workflow), drop the 3D viewer (the operator is
 * standing next to the real arm, and the renderer costs GPU/battery), and
 * shrink the tuning panel to float gains only.
 */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches);

  useEffect(() => {
    const mql = window.matchMedia(query);
    const onChange = () => setMatches(mql.matches);
    onChange();
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, [query]);

  return matches;
}
