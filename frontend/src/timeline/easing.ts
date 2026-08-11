import type { Easing } from "../types";

/** User-facing easing names, shared by the track, the inspector and station cards. */
export const EASING_LABEL: Record<string, string> = {
  linear: "线性",
  ease_in: "缓入",
  ease_out: "缓出",
  ease_in_out: "缓入缓出",
};

export const EASINGS: { value: Easing; label: string }[] = [
  { value: "linear", label: "线性" },
  { value: "ease_in", label: "缓入" },
  { value: "ease_out", label: "缓出" },
  { value: "ease_in_out", label: "缓入缓出" },
];
