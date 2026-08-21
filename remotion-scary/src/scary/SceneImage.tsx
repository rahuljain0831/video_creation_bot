import { AbsoluteFill, Easing, Img, interpolate, staticFile, useCurrentFrame } from "remotion";
import { EASE_OUT } from "./timing";
import type { Shot } from "./schema";

/**
 * One shot: a generated frame with a camera move and the horror grade.
 *
 * The grade lives here and *only* here. It used to be applied in four places —
 * a brightness crush here, a split-tone here, a caption scrim here, then the
 * whole Atmosphere pass on top — and four multiplications of "make it darker"
 * turned the bottom third of every frame to solid black. One pass, one place.
 *
 * The move comes from props rather than from the scene index: the shot planner
 * in Python decides when a beat cuts wide-to-tight, and the move has to agree
 * with that decision.
 */

export const SceneImage: React.FC<{
  shot: Shot;
  intensity: number;
}> = ({ shot, intensity }) => {
  const frame = useCurrentFrame();
  const dur = shot.durationInFrames;

  const p = interpolate(frame, [0, dur], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // `punch` is the exception: it is not a drift across the shot, it is a fast
  // settle in the first third of a second. That snap is what makes a cut to the
  // same picture read as a cut rather than as a zoom.
  const punchP = interpolate(frame, [0, Math.max(2, Math.round(dur * 0.12))], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(...EASE_OUT),
  });

  // Always overscanned, so a pan can never expose an edge.
  let scale = 1.14;
  let x = 0;
  let y = interpolate(p, [0, 1], [10, -10]);

  switch (shot.move) {
    case "zoomIn":
      scale = 1.08 + p * 0.14;
      break;
    case "zoomOut":
      scale = 1.22 - p * 0.14;
      break;
    case "panLeft":
      scale = 1.18;
      x = interpolate(p, [0, 1], [40, -40]);
      break;
    case "panRight":
      scale = 1.18;
      x = interpolate(p, [0, 1], [-40, 40]);
      break;
    case "punch":
      // Lands tight and keeps creeping, so the shot never freezes.
      //
      // Kept modest: at 1.39x this cropped to 72% of the frame, and on a
      // centre-weighted composition — a lit doorway at the middle of a dark
      // corridor — that fills the screen with the brightest region and loses
      // the subject entirely. A 0.12 scale step still reads as a cut.
      scale = interpolate(punchP, [0, 1], [1.08, 1.20]) + p * 0.03;
      y = interpolate(p, [0, 1], [4, -4]);
      break;
    case "driftClose":
      scale = 1.16 + p * 0.08;
      x = interpolate(p, [0, 1], [-14, 14]);
      break;
  }

  return (
    <AbsoluteFill style={{ backgroundColor: "#000" }}>
      <AbsoluteFill style={{ overflow: "hidden" }}>
        <Img
          src={staticFile(shot.imageSrc as string)}
          style={{
            width: "100%",
            height: "100%",
            objectFit: "cover",
            transform: `scale(${scale.toFixed(4)}) translate(${x.toFixed(1)}px, ${y.toFixed(1)}px)`,
            // Contrast and desaturation only. Brightness stays at 1.0 — the
            // generated frames already come back dark, and every layer that
            // darkens again is a layer of picture the viewer never sees.
            filter: `contrast(1.14) saturate(${(0.78 - intensity * 0.16).toFixed(2)})`,
          }}
        />
      </AbsoluteFill>

      {/* Cold shadow / warm rot split-tone. Kept light: it is a grade, not a lid. */}
      <AbsoluteFill
        style={{
          background:
            "linear-gradient(180deg, rgba(120,150,185,0.22) 0%, rgba(255,255,255,0) 48%, rgba(190,90,80,0.20) 100%)",
          mixBlendMode: "multiply",
        }}
      />
    </AbsoluteFill>
  );
};
