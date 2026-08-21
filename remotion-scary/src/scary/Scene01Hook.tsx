import {
  AbsoluteFill,
  Easing,
  Interactive,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { Atmosphere } from "./Atmosphere";
import { bodyFont, titleFont } from "./fonts";

export const Scene01Hook: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  return (
    <AbsoluteFill name="Scene 1 — Hook">
      <Atmosphere />
      <AbsoluteFill
        style={{
          justifyContent: "center",
          alignItems: "center",
          padding: 100,
          textAlign: "center",
        }}
      >
        <Interactive.Div
          name="Clock time"
          style={{
            fontFamily: titleFont,
            fontSize: 220,
            color: "#e8e2d6",
            letterSpacing: 6,
            textShadow: "0 0 40px rgba(200,30,30,0.55)",
            opacity: interpolate(frame, [0, 0.8 * fps], [0, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
              easing: Easing.bezier(0.16, 1, 0.3, 1),
            }),
            scale: interpolate(frame, [0, 2 * fps], [1.25, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
              easing: Easing.bezier(0.16, 1, 0.3, 1),
              output: "perceptual-scale",
            }),
          }}
        >
          3:07 AM
        </Interactive.Div>
        <Interactive.Div
          name="Hook line"
          style={{
            fontFamily: bodyFont,
            fontSize: 68,
            color: "#b9b2a6",
            marginTop: 40,
            lineHeight: 1.35,
            opacity: interpolate(frame, [1.4 * fps, 2.2 * fps], [0, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
              easing: Easing.bezier(0.16, 1, 0.3, 1),
            }),
            translate: interpolate(
              frame,
              [1.4 * fps, 2.2 * fps],
              ["0px 30px", "0px 0px"],
              {
                extrapolateLeft: "clamp",
                extrapolateRight: "clamp",
                easing: Easing.bezier(0.16, 1, 0.3, 1),
              },
            ),
          }}
        >
          Every night, someone knocks.
        </Interactive.Div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
