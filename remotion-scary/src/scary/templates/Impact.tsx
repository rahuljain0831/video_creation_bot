import { AbsoluteFill, Easing, interpolate, random, useCurrentFrame, useVideoConfig } from "remotion";
import { Backdrop } from "../Backdrop";
import { bodyFont } from "../fonts";
import { COLORS, EASE_OUT, beat, span } from "../timing";
import { AccentStage, ChromaText, Flash, Scanlines, accentStyle } from "../effects";
import type { Scene } from "../schema";

/**
 * One short word punched onto screen `repeat` times, each hit later and harder.
 * Every hit carries a one-frame flash and a kick of camera shake; the last one
 * goes blood-red. The escalation is the whole point — three identical hits
 * read as a list, three growing hits read as something getting closer.
 */
export const Impact: React.FC<{ scene: Scene; seed: number; intensity: number; index: number }> = ({
  scene,
  seed,
  intensity,
  index,
}) => {
  const frame = useCurrentFrame() - scene.leadInFrames;
  const { fps } = useVideoConfig();
  const dur = scene.narrativeDurationInFrames;

  const hits = Array.from({ length: scene.repeat }, (_, k) => k);
  const settleFor = span(0.4, fps, dur, 0.25);
  const hitAt = (k: number) => beat(0.1 + (k * 0.55) / scene.repeat, dur);

  // Flash and shake fire on whichever hit is closest behind us.
  let flash = 0;
  let shake = 0;
  hits.forEach((k) => {
    const at = hitAt(k);
    const weight = 0.5 + (k / Math.max(1, scene.repeat - 1)) * 0.5;
    flash = Math.max(
      flash,
      interpolate(frame, [at - 1, at, at + 2], [0, 0.3 * weight, 0], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      }),
    );
    shake = Math.max(
      shake,
      interpolate(frame, [at, at + span(0.35, fps, dur, 0.2)], [weight, 0], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      }),
    );
  });

  const shakeX = (random(`ix${seed}-${frame}`) - 0.5) * 36 * shake;
  const shakeY = (random(`iy${seed}-${frame}`) - 0.5) * 24 * shake;

  return (
    <AbsoluteFill style={{ translate: `${shakeX}px ${shakeY}px` }}>
      <Backdrop scene={scene} seed={seed} intensity={Math.max(intensity, 0.4)} index={index} />
      {/* Behind the words, so each hit is silhouetted rather than washed out. */}
      <Flash strength={flash} />
      <AccentStage>
        {scene.accent
          ? hits.map((k) => {
              const at = hitAt(k);
              const isLast = k === scene.repeat - 1;
              const style = accentStyle(scene.accent, 128 + k * 14, {
                fontFamily: bodyFont,
                color: isLast ? COLORS.blood : "#e6ded0",
                textShadow: isLast
                  ? "0 0 56px rgba(210,30,30,0.75)"
                  : "0 0 26px rgba(0,0,0,0.9)",
              });

              return (
                <div
                  key={k}
                  style={{
                    marginTop: k === 0 ? 0 : 18,
                    opacity: interpolate(
                      frame,
                      [at, at + span(0.18, fps, dur, 0.12)],
                      [0, 1],
                      {
                        extrapolateLeft: "clamp",
                        extrapolateRight: "clamp",
                        easing: Easing.linear,
                      },
                    ),
                    scale: interpolate(frame, [at, at + settleFor], [1.7, 1], {
                      extrapolateLeft: "clamp",
                      extrapolateRight: "clamp",
                      easing: Easing.bezier(...EASE_OUT),
                      output: "perceptual-scale",
                    }),
                  }}
                >
                  <ChromaText style={style} amount={isLast ? 9 * shake : 4 * shake}>
                    {scene.accent}
                  </ChromaText>
                </div>
              );
            })
          : null}
      </AccentStage>
      <Scanlines opacity={0.12} />
    </AbsoluteFill>
  );
};
