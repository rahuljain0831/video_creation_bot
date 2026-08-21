/**
 * Scene animations have to work at both 2.5s and 9s, because scene length now
 * comes from how long the narrator actually spoke.
 *
 * Two different things are being timed and they scale differently:
 *
 *   beat() — WHEN something happens. Dramatic structure. Stretches with the
 *            scene, so a long scene doesn't sit motionless waiting.
 *   span() — HOW LONG an effect runs. Perceptual. Stays roughly wall-clock,
 *            because a 0.15s jump-scare flash stretched to 0.5s stops reading
 *            as a flash. Clamped so it can never swallow a short scene.
 */

/** Frame at `frac` through a scene of `dur` frames. */
export const beat = (frac: number, dur: number): number =>
  Math.round(frac * dur);

/**
 * A duration in seconds, converted to frames, but never longer than `maxFrac`
 * of the scene and never shorter than 2 frames.
 */
export const span = (
  seconds: number,
  fps: number,
  dur: number,
  maxFrac = 0.3,
): number =>
  Math.max(2, Math.min(Math.round(seconds * fps), Math.round(dur * maxFrac)));

/** Standard ease used across every scene. */
export const EASE_OUT = [0.16, 1, 0.3, 1] as const;

/** Palette, kept in one place so the templates stay consistent. */
export const COLORS = {
  bone: "#e8e2d6",
  boneDim: "#cdc5b7",
  boneFaint: "#948c80",
  paper: "#f2e9da",
  blood: "#c8242a",
  bloodWash: "#8e0f14",
} as const;
