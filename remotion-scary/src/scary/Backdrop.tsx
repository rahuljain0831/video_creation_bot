import { AbsoluteFill, Sequence } from "remotion";
import { Atmosphere } from "./Atmosphere";
import { SceneImage } from "./SceneImage";
import type { Scene } from "./schema";

/**
 * Everything behind the typography: the beat's shots, cut one after another,
 * plus the shared fog/grain/vignette pass on top of them.
 *
 * Single change point — templates render this rather than reaching for
 * Atmosphere or SceneImage directly, so the picture layer can be turned on or
 * off for the whole niche in one place.
 *
 * Shots are hard cuts by design. A beat that cross-faded between its own shots
 * would read as one soft drift, which is the thing this is meant to fix.
 */
export const Backdrop: React.FC<{
  scene: Scene;
  seed: number;
  intensity: number;
  index: number;
}> = ({ scene, seed, intensity }) => {
  const shots = scene.shots.filter((s) => s.imageSrc);
  const hasImage = shots.length > 0;

  let from = 0;
  const sequences = scene.shots.map((shot, i) => {
    const start = from;
    from += shot.durationInFrames;
    if (!shot.imageSrc) {
      return null;
    }
    return (
      <Sequence
        key={`shot-${i}`}
        from={start}
        durationInFrames={shot.durationInFrames}
        name={`Shot ${i + 1}`}
      >
        <SceneImage shot={shot} intensity={intensity} />
      </Sequence>
    );
  });

  return (
    <AbsoluteFill>
      {sequences}
      <Atmosphere seed={seed} intensity={intensity} transparent={hasImage} />
    </AbsoluteFill>
  );
};
