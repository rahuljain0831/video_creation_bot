import { AbsoluteFill, interpolate, random, useCurrentFrame } from "remotion";
import { Backdrop } from "../Backdrop";
import { ApproachingMass, Scanlines, TrackingTear } from "../effects";
import type { Scene } from "../schema";

/**
 * The workhorse beat. No on-screen type — the narration is spoken and the
 * caption band carries the words.
 *
 * Not empty, though: the dark mass at the bottom of the frame keeps rising as
 * the story progresses, and the image tears in brief unpredictable bursts. The
 * quiet scenes are where dread accumulates, so they cannot be static.
 */
export const Line: React.FC<{ scene: Scene; seed: number; intensity: number; index: number }> = ({
  scene,
  seed,
  intensity,
  index,
}) => {
  const frame = useCurrentFrame() - scene.leadInFrames;
  const dur = scene.narrativeDurationInFrames;

  // Rare, short tear bursts — roughly one per scene, never on a fixed beat.
  const burstWindow = Math.floor(frame / 11);
  const bursting = random(`burst${seed}-${burstWindow}`) < 0.12 + intensity * 0.16;
  const tear = bursting ? 0.3 + intensity * 0.5 : 0;

  // Something gets closer as the story goes on.
  const mass = interpolate(
    frame,
    [0, dur],
    [intensity * 0.55, intensity * 0.8],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  return (
    <AbsoluteFill>
      <Backdrop scene={scene} seed={seed} intensity={intensity} index={index} />
      <ApproachingMass progress={mass} />
      <TrackingTear seed={seed} active={tear} />
      <Scanlines opacity={0.1 + intensity * 0.06} />
    </AbsoluteFill>
  );
};
