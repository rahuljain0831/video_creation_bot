import { AbsoluteFill, Easing, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import { Backdrop } from "../Backdrop";
import { bodyFont, titleFont } from "../fonts";
import { COLORS, EASE_OUT, beat, span } from "../timing";
import { AccentStage, BloodWash, ChromaText, EyesInTheDark, Scanlines, accentStyle } from "../effects";
import type { Scene } from "../schema";

/**
 * Closing card. The sting lands with the title, the channel line fades in
 * under it — and the eyes come back one last time and stay, so the final
 * frame the viewer holds is being watched.
 */
export const End: React.FC<{
  scene: Scene;
  seed: number;
  intensity: number;
  index: number;
  subline: string;
}> = ({ scene, seed, index, subline }) => {
  const frame = useCurrentFrame() - scene.leadInFrames;
  const { fps } = useVideoConfig();
  const dur = scene.narrativeDurationInFrames;

  const subAt = beat(0.35, dur);
  const subFor = span(0.8, fps, dur);

  const wash = interpolate(frame, [0, span(0.4, fps, dur, 0.2), dur], [0.75, 0.3, 0.45], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // Return, and do not leave.
  const eyes = interpolate(frame, [beat(0.55, dur), beat(0.8, dur)], [0, 0.8], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const titleStyle = accentStyle(scene.accent, 168, {
    fontFamily: titleFont,
    color: COLORS.blood,
    letterSpacing: 4,
    textShadow: "0 0 74px rgba(210,30,30,0.65)",
  });

  return (
    <AbsoluteFill>
      <Backdrop scene={scene} seed={seed} intensity={1} index={index} />
      <BloodWash strength={wash} />
      <EyesInTheDark progress={eyes} seed={seed} y="70%" />

      <AccentStage>
        {scene.accent ? (
          <div
            style={{
              opacity: interpolate(frame, [0, span(0.9, fps, dur, 0.4)], [0, 1], {
                extrapolateLeft: "clamp",
                extrapolateRight: "clamp",
                easing: Easing.bezier(...EASE_OUT),
              }),
              scale: interpolate(frame, [0, span(1.6, fps, dur, 0.6)], [1.18, 1], {
                extrapolateLeft: "clamp",
                extrapolateRight: "clamp",
                easing: Easing.bezier(...EASE_OUT),
                output: "perceptual-scale",
              }),
            }}
          >
            <ChromaText style={titleStyle} amount={6}>
              {scene.accent}
            </ChromaText>
          </div>
        ) : null}
        <div
          style={{
            fontFamily: bodyFont,
            fontSize: 48,
            color: COLORS.boneFaint,
            marginTop: 54,
            letterSpacing: 6,
            textAlign: "center",
            opacity: interpolate(frame, [subAt, subAt + subFor], [0, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
              easing: Easing.bezier(...EASE_OUT),
            }),
          }}
        >
          {subline}
        </div>
      </AccentStage>

      <Scanlines opacity={0.16} />
    </AbsoluteFill>
  );
};
