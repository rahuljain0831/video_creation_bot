import { AbsoluteFill, Easing, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import { Backdrop } from "../Backdrop";
import { titleFont } from "../fonts";
import { EASE_OUT, beat, span } from "../timing";
import { AccentStage, ChromaText, Scanlines, TrackingTear, accentStyle } from "../effects";
import type { Scene } from "../schema";

/**
 * Opening beat. The accent swells out of black already slightly broken —
 * the RGB split settles over the first second, so the very first thing the
 * viewer sees is an image failing to hold itself together.
 */
export const Hook: React.FC<{ scene: Scene; seed: number; intensity: number; index: number }> = ({
  scene,
  seed,
  intensity,
  index,
}) => {
  const frame = useCurrentFrame() - scene.leadInFrames;
  const { fps } = useVideoConfig();
  const dur = scene.narrativeDurationInFrames;

  // Deliberately short. The opening beat used to fade the accent up over 0.8s
  // from an empty frame, which spends the only part of a short that decides
  // whether anyone watches the rest of it showing nothing. The picture is
  // already on screen at frame 0 and the accent is legible within ~4 frames.
  const inFor = span(0.14, fps, dur, 0.12);
  const settleFor = span(1.2, fps, dur, 0.5);

  // Starts misregistered, resolves — then drifts apart again at the end.
  //
  // The opening peak used to be 26px of split, which on the very first frame of
  // the video read as a cheap filter rather than as an image under stress. Half
  // that still says "something is wrong with this recording" without being the
  // first thing the viewer notices.
  const chroma = interpolate(
    frame,
    [0, settleFor, beat(0.85, dur), dur],
    [13, 1.5, 2, 7],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );
  const tear = interpolate(frame, [0, inFor, beat(0.4, dur)], [0.9, 0.35, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const style = accentStyle(scene.accent, 200, {
    fontFamily: titleFont,
    letterSpacing: 6,
    textShadow: "0 0 44px rgba(200,30,30,0.6)",
  });

  return (
    <AbsoluteFill>
      <Backdrop scene={scene} seed={seed} intensity={intensity} index={index} />
      <TrackingTear seed={seed} active={tear} />

      {scene.accent ? (
        <AccentStage>
          <div
            style={{
              opacity: interpolate(frame, [0, inFor], [0.35, 1], {
                extrapolateLeft: "clamp",
                extrapolateRight: "clamp",
                easing: Easing.bezier(...EASE_OUT),
              }),
              scale: interpolate(frame, [0, settleFor], [1.25, 1], {
                extrapolateLeft: "clamp",
                extrapolateRight: "clamp",
                easing: Easing.bezier(...EASE_OUT),
                output: "perceptual-scale",
              }),
            }}
          >
            <ChromaText style={style} amount={chroma}>
              {scene.accent}
            </ChromaText>
          </div>
        </AccentStage>
      ) : null}

      <Scanlines opacity={0.12} />
    </AbsoluteFill>
  );
};
