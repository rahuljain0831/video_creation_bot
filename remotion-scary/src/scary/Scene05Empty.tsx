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

export const Scene05Empty: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  return (
    <AbsoluteFill name="Scene 5 — Nobody there">
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
            fontSize: 118,
            color: "#d9d1c2",
            letterSpacing: 2,
            opacity: interpolate(
              frame,
              [0.3 * fps, 1 * fps, 3 * fps, 3.5 * fps],
              [0, 1, 1, 0.55],
              {
                extrapolateLeft: "clamp",
                extrapolateRight: "clamp",
                easing: Easing.linear,
              },
            ),
            scale: interpolate(frame, [0, 3.5 * fps], [1.12, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
              easing: Easing.linear,
              output: "perceptual-scale",
            }),
          }}
        >
          Nobody was there.
        </Interactive.Div>
        <Interactive.Div
          name="Sub line"
          style={{
            fontFamily: bodyFont,
            fontSize: 54,
            color: "#8b8377",
            marginTop: 52,
            opacity: interpolate(frame, [1.8 * fps, 2.6 * fps], [0, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
              easing: Easing.bezier(0.16, 1, 0.3, 1),
            }),
          }}
        >
          Just the empty corridor. And the cold.
        </Interactive.Div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
