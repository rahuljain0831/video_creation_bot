import { linearTiming, TransitionSeries } from "@remotion/transitions";
import { fade } from "@remotion/transitions/fade";
import { AbsoluteFill, Audio, Sequence, staticFile } from "remotion";
import type { CalculateMetadataFunction } from "remotion";
import { CaptionsLayer } from "./CaptionsLayer";
import { End } from "./templates/End";
import { Hook } from "./templates/Hook";
import { Impact } from "./templates/Impact";
import { Line } from "./templates/Line";
import { Reveal } from "./templates/Reveal";
import { Scare } from "./templates/Scare";
import { totalFrames } from "./schema";
import type { ScaryStoryProps, Scene } from "./schema";

const FPS = 30;

const renderScene = (
  scene: Scene,
  seed: number,
  intensity: number,
  index: number,
  endSubline: string,
) => {
  const p = { scene, seed, intensity, index };
  switch (scene.template) {
    case "hook":
      return <Hook {...p} />;
    case "impact":
      return <Impact {...p} />;
    case "reveal":
      return <Reveal {...p} />;
    case "scare":
      return <Scare {...p} />;
    case "end":
      return <End {...p} subline={endSubline} />;
    case "line":
    default:
      return <Line {...p} />;
  }
};

/**
 * Duration comes from the props, not from a hand-maintained constant.
 *
 * The assertion is deliberate: Python guarantees the scene frames sum back to
 * the narration length, and if that invariant ever breaks we want to know at
 * second zero rather than discover a truncated video at frame 1900.
 */
export const calculateScaryStoryMetadata: CalculateMetadataFunction<ScaryStoryProps> = ({
  props,
}) => {
  const total = totalFrames(props);
  const drift = Math.abs(total - props.audioDurationInFrames);

  if (drift > 1) {
    throw new Error(
      `ScaryStory: scene frames sum to ${total} but the narration is ` +
        `${props.audioDurationInFrames} frames (drift ${drift}). ` +
        `The props builder in pipeline/remotion_renderer.py is wrong.`,
    );
  }

  // Same posture one level down: a beat whose shots don't fill it would leave a
  // black gap mid-scene, and that is far cheaper to catch here than to notice
  // in a finished mp4.
  props.scenes.forEach((scene, i) => {
    if (scene.shots.length === 0) {
      return;
    }
    const shotSum = scene.shots.reduce((acc, s) => acc + s.durationInFrames, 0);
    if (shotSum !== scene.durationInFrames) {
      throw new Error(
        `ScaryStory: scene ${i} is ${scene.durationInFrames} frames but its ` +
          `shots sum to ${shotSum}. plan_shots in ` +
          `pipeline/remotion_renderer.py is wrong.`,
      );
    }
  });

  return {
    durationInFrames: total,
    fps: FPS,
    width: 1080,
    height: 1920,
  };
};

export const ScaryStory: React.FC<ScaryStoryProps> = (props) => {
  const {
    scenes, transitionFrames, seed, audioSrc, captions, captionStyle, endSubline,
    narrationVolume, ambienceSrc, ambienceVolume, ambienceGaps, stings,
  } = props;

  return (
    <AbsoluteFill style={{ backgroundColor: "#000000" }}>
      {/*
        Three audio layers, all outside the TransitionSeries so cross-fades
        never duck them: narration on top, ambience bed well under it, and
        one-shot stings on the reveal / scare / end beats.
      */}
      {audioSrc ? <Audio src={staticFile(audioSrc)} volume={narrationVolume} /> : null}
      {ambienceSrc ? (
        <Audio
          src={staticFile(ambienceSrc)}
          volume={(f) => {
            // Hard silence first: inside a gap's silent window the bed is gone,
            // full stop. Nothing else may reintroduce it.
            const gap = ambienceGaps.find((g) => f >= g.fromFrame && f < g.toFrame);
            if (gap) {
              if (f >= gap.silentFromFrame) {
                return 0;
              }
              // Under the riser, backing off as the rise builds.
              const p = (f - gap.fromFrame) / Math.max(1, gap.silentFromFrame - gap.fromFrame);
              return ambienceVolume * (1 - p * 0.85);
            }

            // Duck the bed around each sting so the hit reads as loud rather
            // than merely adding to the noise floor.
            const near = stings.reduce((acc, s) => {
              const d = Math.abs(f - s.atFrame);
              return d < 30 ? Math.max(acc, 1 - d / 30) : acc;
            }, 0);

            // And duck it under the voice. The caption chunks are exactly the
            // frames where a word is being spoken, so they double as a
            // sidechain key — no separate analysis of the narration needed.
            const speaking = captions.some(
              (c) => f >= c.fromFrame && f < c.toFrame,
            );

            // 0.65 here ducked a bed that was already well under the voice down
            // to roughly -29 dBFS, which on a phone speaker is inaudible — the
            // bed effectively only existed in the gaps between lines. 0.8 keeps
            // the voice clearly on top while leaving the music present under it.
            return ambienceVolume * (1 - near * 0.6) * (speaking ? 0.8 : 1);
          }}
        />
      ) : null}
      {stings.map((sting, i) => (
        <Sequence key={`sting-${i}`} from={sting.atFrame} name={`Sting ${i + 1}`}>
          {/* Constant callback: a sting must keep its transient, so no attack ramp. */}
          <Audio src={staticFile(sting.src)} volume={() => sting.volume} />
        </Sequence>
      ))}

      <TransitionSeries>
        {scenes.flatMap((scene, i) => {
          // Dread ramp: the room closes in as the story goes on. Never starts
          // at zero — even scene one should feel wrong.
          const intensity =
            scenes.length > 1 ? 0.15 + (i / (scenes.length - 1)) * 0.85 : 0.5;

          const sequence = (
            <TransitionSeries.Sequence
              key={`seq-${i}`}
              durationInFrames={scene.durationInFrames}
              name={`${i + 1}. ${scene.template}`}
            >
              {renderScene(scene, seed + i * 977, intensity, i, endSubline)}
            </TransitionSeries.Sequence>
          );

          if (i === scenes.length - 1 || transitionFrames === 0) {
            return [sequence];
          }

          return [
            sequence,
            <TransitionSeries.Transition
              key={`tr-${i}`}
              presentation={fade()}
              timing={linearTiming({ durationInFrames: transitionFrames })}
            />,
          ];
        })}
      </TransitionSeries>

      {/* Above the series so the cross-fades never dim the captions. */}
      <CaptionsLayer captions={captions} style={captionStyle} />
    </AbsoluteFill>
  );
};
