import { z } from "zod";

/**
 * Props contract between the Python pipeline and this composition.
 *
 * Python owns all the arithmetic — tick-to-frame conversion, scene boundaries,
 * caption chunking, transition padding. Everything here is already in frames.
 * Keep it that way: duplicating the timing maths in TS is how the ffmpeg and
 * Remotion renderers would drift apart.
 */

export const TEMPLATES = [
  "hook",
  "line",
  "impact",
  "reveal",
  "scare",
  "end",
] as const;

/** Camera moves SceneImage knows how to draw. */
export const MOVES = [
  "zoomIn",
  "zoomOut",
  "panLeft",
  "panRight",
  "punch",
  "driftClose",
] as const;

/**
 * One picture on screen.
 *
 * A narration beat is the unit of meaning, but it is the wrong unit for the
 * picture — a sentence takes as long as it takes, and one image held for its
 * whole length puts a visual change on screen only every five seconds. So the
 * beat is split into shots, and this is one of them.
 */
export const shotSchema = z.object({
  /** Filename inside the --public-dir. null renders the procedural look. */
  imageSrc: z.string().nullable().default(null),
  durationInFrames: z.number().int().positive(),
  move: z.enum(MOVES).default("zoomIn"),
});

export const sceneSchema = z.object({
  /** Which look to draw. See templates/. */
  template: z.enum(TEMPLATES),
  /** Length of this TransitionSeries.Sequence, transition padding included. */
  durationInFrames: z.number().int().positive(),
  /**
   * Length of the narration beat this scene covers, padding excluded.
   * Animations key off this, not off the padded value, so beats don't land late.
   */
  narrativeDurationInFrames: z.number().int().positive(),
  /**
   * Frames of cross-fade lead-in before the narration beat actually starts.
   * Every sequence after the first begins one transition early so the fade
   * completes exactly on the cut; templates subtract this so their beats stay
   * locked to the voice rather than to the fade.
   */
  leadInFrames: z.number().int().min(0).default(0),
  /**
   * Generated backdrop for this scene (filename inside the --public-dir).
   * null falls back to the pure-procedural look.
   */
  imageSrc: z.string().nullable().default(null),
  /**
   * The pictures this beat cuts through. Durations sum to durationInFrames
   * (the lead-in is carried by the first shot, so the backdrop covers the
   * cross-fade). Python guarantees the sum; calculateMetadata asserts it.
   */
  shots: z.array(shotSchema).default([]),
  /** Huge on-screen type. Empty string = template falls back to atmosphere only. */
  accent: z.string().default(""),
  /** How many times `impact` punches the accent onto screen. */
  repeat: z.number().int().min(1).max(3).default(1),
});

export const captionWordSchema = z.object({
  text: z.string(),
  fromFrame: z.number().int().min(0),
  toFrame: z.number().int().min(0),
});

export const captionChunkSchema = z.object({
  fromFrame: z.number().int().min(0),
  toFrame: z.number().int().min(0),
  words: z.array(captionWordSchema),
});

export const stingSchema = z.object({
  /** Filename inside the --public-dir. */
  src: z.string(),
  /** Absolute composition frame the hit lands on. */
  atFrame: z.number().int().min(0),
  volume: z.number().min(0).max(1).default(0.85),
});

/**
 * A window where the ambience bed steps out of the way.
 *
 * Written by the soundtrack builder around a riser: the bed ducks across
 * [fromFrame, silentFromFrame) so the rise is audible, then goes fully silent
 * from silentFromFrame to toFrame — the few frames before the hit. That silence
 * is what turns a loud noise into a scare.
 */
export const ambienceGapSchema = z.object({
  fromFrame: z.number().int().min(0),
  toFrame: z.number().int().min(0),
  silentFromFrame: z.number().int().min(0),
});

export const captionStyleSchema = z.object({
  fontSizePx: z.number().positive().default(96),
  /**
   * Vertical centre of the caption band, as a percentage of frame height.
   *
   * Anchored from the top, not the bottom, on purpose: the bottom ~15% of a
   * vertical video is covered by the host platform's own UI, and a caption
   * pinned to a bottom margin lands underneath it.
   */
  anchorYPercent: z.number().min(0).max(100).default(62),
  activeColor: z.string().default("#f4ece0"),
  idleColor: z.string().default("#9c948a"),
});

export const scaryStorySchema = z.object({
  schemaVersion: z.literal(2),
  title: z.string(),
  /**
   * Filename inside the --public-dir, resolved with staticFile().
   * null renders silent, which is what sample-props.json uses so the sample
   * needs no binary asset in git.
   */
  audioSrc: z.string().nullable(),
  /** Narration gain. The voice must always sit on top of the bed. */
  narrationVolume: z.number().min(0).max(1).default(1),
  /** Full-length synthesized horror bed, or null for a dry render. */
  ambienceSrc: z.string().nullable().default(null),
  ambienceVolume: z.number().min(0).max(1).default(0.22),
  /** Windows where the bed ducks and then drops out. */
  ambienceGaps: z.array(ambienceGapSchema).default([]),
  /** One-shot hits placed on reveal / scare / end beats. */
  stings: z.array(stingSchema).default([]),
  /** Expected total. calculateMetadata asserts the scene sum matches this. */
  audioDurationInFrames: z.number().int().positive(),
  transitionFrames: z.number().int().min(0),
  seed: z.number().int().default(0),
  /** Small line under the closing sting. */
  endSubline: z.string().default("a new story every night"),
  scenes: z.array(sceneSchema).min(1),
  captions: z.array(captionChunkSchema),
  captionStyle: captionStyleSchema,
});

export type Scene = z.infer<typeof sceneSchema>;
export type Shot = z.infer<typeof shotSchema>;
export type CaptionChunk = z.infer<typeof captionChunkSchema>;
export type CaptionStyle = z.infer<typeof captionStyleSchema>;
export type ScaryStoryProps = z.infer<typeof scaryStorySchema>;

/**
 * Total frames of a TransitionSeries: adjacent sequences overlap by the
 * transition length, so each of the n-1 transitions is consumed once.
 */
export const totalFrames = (props: ScaryStoryProps): number => {
  const sum = props.scenes.reduce((acc, s) => acc + s.durationInFrames, 0);
  return sum - (props.scenes.length - 1) * props.transitionFrames;
};
