import { AbsoluteFill, Easing, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import { Backdrop } from "../Backdrop";
import { bodyFont } from "../fonts";
import { EASE_OUT, beat, span } from "../timing";
import { AccentStage, ChromaText, EyesInTheDark, Scanlines, TrackingTear, accentStyle } from "../effects";
import type { Scene } from "../schema";

/**
 * A slit of light widening across the frame, like a door being opened —
 * and, for a few frames while the gap is at its widest, something standing
 * in it. The eyes fade before the light does, so the viewer is never given a
 * clean look and cannot be sure.
 */
export const Reveal: React.FC<{ scene: Scene; seed: number; intensity: number; index: number }> = ({
  scene,
  seed,
  intensity,
  index,
}) => {
  const frame = useCurrentFrame() - scene.leadInFrames;
  const { fps } = useVideoConfig();
  const dur = scene.narrativeDurationInFrames;

  const openAt = beat(0.12, dur);
  const openEnd = beat(0.8, dur);
  const peak = beat(0.38, dur);

  const eyes = interpolate(
    frame,
    [beat(0.34, dur), beat(0.46, dur), beat(0.6, dur), beat(0.68, dur)],
    [0, 0.9, 0.9, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  const tear = interpolate(frame, [openAt, peak, openEnd], [0, 0.45, 0.1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const style = accentStyle(scene.accent, 108, {
    fontFamily: bodyFont,
    lineHeight: 1.3,
    textShadow: "0 0 40px rgba(0,0,0,0.96)",
  });

  return (
    <AbsoluteFill>
      <Backdrop scene={scene} seed={seed} intensity={Math.max(intensity, 0.5)} index={index} />

      {/* The widening gap */}
      <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
        <div
          style={{
            width: 26,
            height: 1500,
            background:
              "linear-gradient(180deg, rgba(255,240,210,0) 0%, rgba(255,240,210,0.6) 45%, rgba(255,240,210,0) 100%)",
            filter: "blur(14px)",
            scale: interpolate(frame, [openAt, openEnd], [0.2, 4.5], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
              easing: Easing.bezier(0.33, 0, 0.67, 1),
              output: "perceptual-scale",
            }),
            opacity: interpolate(frame, [openAt, peak, openEnd], [0, 0.9, 0.22], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
              easing: Easing.linear,
            }),
          }}
        />
      </AbsoluteFill>

      <EyesInTheDark progress={eyes} seed={seed} y="34%" />
      <TrackingTear seed={seed} active={tear} />

      {scene.accent ? (
        <AccentStage>
          <div
            style={{
              opacity: interpolate(
                frame,
                [beat(0.1, dur), beat(0.1, dur) + span(0.8, fps, dur)],
                [0, 1],
                {
                  extrapolateLeft: "clamp",
                  extrapolateRight: "clamp",
                  easing: Easing.bezier(...EASE_OUT),
                },
              ),
            }}
          >
            <ChromaText style={style} amount={5 * tear}>
              {scene.accent}
            </ChromaText>
          </div>
        </AccentStage>
      ) : null}

      <Scanlines opacity={0.12} />
    </AbsoluteFill>
  );
};
