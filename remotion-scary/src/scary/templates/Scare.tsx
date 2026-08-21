import { AbsoluteFill, Easing, interpolate, random, useCurrentFrame, useVideoConfig } from "remotion";
import { Backdrop } from "../Backdrop";
import { titleFont } from "../fonts";
import { COLORS, EASE_OUT, beat, span } from "../timing";
import {
  AccentStage,
  BloodWash,
  ChromaText,
  EyesInTheDark,
  Flash,
  GlitchShadow,
  Scanlines,
  TrackingTear,
  accentStyle,
} from "../effects";
import type { Scene } from "../schema";

/**
 * The turn. Everything in the short is built to pay off here.
 *
 * Sequence: the eyes resolve out of the dark during the setup, the image
 * starts tearing, then on the hit — a two-frame white flash, a blood wash, the
 * accent slams in with an RGB split and a glitch double, and the whole frame
 * shakes. The flash and shake use span() rather than beat() on purpose: a
 * scare is a perceptual event. Stretch it across a long scene and it stops
 * being a scare.
 */
export const Scare: React.FC<{ scene: Scene; seed: number; intensity: number; index: number }> = ({
  scene,
  seed,
  intensity,
  index,
}) => {
  const frame = useCurrentFrame() - scene.leadInFrames;
  const { fps } = useVideoConfig();
  const dur = scene.narrativeDurationInFrames;

  const hitAt = beat(0.45, dur);
  const flash = span(0.15, fps, dur, 0.12);
  const settleFor = span(0.9, fps, dur, 0.45);
  const shakeStep = span(0.09, fps, dur, 0.07);

  // Eyes resolve during the setup, then vanish the instant the accent lands —
  // the viewer is left unsure they were ever there.
  const eyes = interpolate(frame, [beat(0.1, dur), hitAt - 2, hitAt], [0, 0.85, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // Two frames, and genuinely only two. Kept well below full white: it sits
  // *behind* the accent to silhouette it, and a longer or brighter flash both
  // washes the reveal out and edges toward photosensitivity territory.
  const flashStrength = interpolate(
    frame,
    [hitAt - 1, hitAt, hitAt + 2],
    [0, 0.5, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  // Spike hard on the hit, then get out of the way. The sustained values used
  // to sit at 0.45 and 0.2, which was fine over the black frames this template
  // was written for but leaves a photographic scene tinted pink for its whole
  // length — and the scare beat is the one shot that most needs to be legible.
  const wash = interpolate(
    frame,
    [hitAt, hitAt + flash, hitAt + flash * 5, dur],
    [0, 0.9, 0.28, 0.1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  const stress = interpolate(
    frame,
    [beat(0.25, dur), hitAt, hitAt + settleFor],
    [0, 1, 0.25],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  // Whole-frame shake, decaying fast.
  const shakeAmt = interpolate(frame, [hitAt, hitAt + shakeStep * 6], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const shakeX = (random(`sx${seed}-${frame}`) - 0.5) * 70 * shakeAmt;
  const shakeY = (random(`sy${seed}-${frame}`) - 0.5) * 46 * shakeAmt;

  const style = accentStyle(scene.accent, 156, {
    fontFamily: titleFont,
    color: COLORS.paper,
    letterSpacing: 3,
    textShadow: "0 0 70px rgba(230,20,20,0.9)",
  });

  return (
    <AbsoluteFill style={{ translate: `${shakeX}px ${shakeY}px` }}>
      <Backdrop scene={scene} seed={seed} intensity={Math.max(intensity, 0.75)} index={index} />
      <EyesInTheDark progress={eyes} seed={seed} />
      <BloodWash strength={wash} />
      {/* Behind the accent on purpose — the flash silhouettes the word. */}
      <Flash strength={flashStrength} />
      <TrackingTear seed={seed} active={stress} />

      {scene.accent ? (
        <AccentStage>
          <div
            style={{
              position: "relative",
              // Two frames, not five. A scare that eases in is not a scare —
              // it has to already be there before the viewer registers it.
              opacity: interpolate(frame, [hitAt, hitAt + 2], [0, 1], {
                extrapolateLeft: "clamp",
                extrapolateRight: "clamp",
                easing: Easing.linear,
              }),
              scale: interpolate(frame, [hitAt, hitAt + settleFor], [1.4, 1], {
                extrapolateLeft: "clamp",
                extrapolateRight: "clamp",
                easing: Easing.bezier(...EASE_OUT),
                output: "perceptual-scale",
              }),
            }}
          >
            <GlitchShadow style={style} amount={shakeAmt} seed={seed}>
              {scene.accent}
            </GlitchShadow>
            {/* The split resolves fast: it exists to make the hit land, and a
                word still tearing apart while the viewer tries to read it just
                costs the reveal. */}
            <ChromaText style={style} amount={Math.min(12 * stress, 7)}>
              {scene.accent}
            </ChromaText>
          </div>
        </AccentStage>
      ) : null}

      <Scanlines opacity={0.1 + stress * 0.14} />
    </AbsoluteFill>
  );
};
