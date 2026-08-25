import {
  AbsoluteFill,
  Easing,
  Interactive,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { Atmosphere } from "./Atmosphere";
import { bodyFont } from "./fonts";

export const Scene02Alone: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  return (
    <AbsoluteFill name="Scene 2 — I live alone">
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
          name="Line"
          style={{
            fontFamily: bodyFont,
            fontSize: 110,
            color: "#ded7c9",
            lineHeight: 1.3,
            textShadow: "0 0 30px rgba(0,0,0,0.9)",
            opacity: interpolate(frame, [0, 0.7 * fps], [0, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
              easing: Easing.bezier(0.16, 1, 0.3, 1),
            }),
            scale: interpolate(frame, [0, 3.5 * fps], [1, 1.08], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
              easing: Easing.linear,
              output: "perceptual-scale",
            }),
          }}
        >
          I live alone.
        </Interactive.Div>
        <Interactive.Div
          name="Sub line"
          style={{
            fontFamily: bodyFont,
            fontSize: 56,
            color: "#8f877b",
            marginTop: 48,
            opacity: interpolate(frame, [1.5 * fps, 2.3 * fps], [0, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
              easing: Easing.bezier(0.16, 1, 0.3, 1),
            }),
          }}
        >
          Fourth floor. No neighbours.
        </Interactive.Div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
