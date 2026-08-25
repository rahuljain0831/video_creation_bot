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

export const Scene06Twist: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  return (
    <AbsoluteFill name="Scene 6 — The twist">
      <Atmosphere />
      {/* Red jump-scare wash on the reveal */}
      <AbsoluteFill
        style={{
          backgroundColor: "#8e0f14",
          opacity: interpolate(
            frame,
            [2.3 * fps, 2.45 * fps, 2.8 * fps],
            [0, 0.75, 0],
            {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
              easing: Easing.linear,
            },
          ),
        }}
      />
      <AbsoluteFill
        style={{
          justifyContent: "center",
          alignItems: "center",
          padding: 100,
          textAlign: "center",
        }}
      >
        <Interactive.Div
          name="Setup line"
          style={{
            fontFamily: bodyFont,
            fontSize: 76,
            color: "#cdc5b7",
            lineHeight: 1.35,
            opacity: interpolate(
              frame,
              [0.3 * fps, 1 * fps, 2.3 * fps, 2.6 * fps],
              [0, 1, 1, 0.3],
              {
                extrapolateLeft: "clamp",
                extrapolateRight: "clamp",
                easing: Easing.linear,
              },
            ),
          }}
        >
          Then the knocking started again.
        </Interactive.Div>
        <Interactive.Div
          name="Reveal line"
          style={{
            fontFamily: titleFont,
            fontSize: 150,
            color: "#f2e9da",
            marginTop: 70,
            lineHeight: 1.15,
            letterSpacing: 3,
            textShadow: "0 0 60px rgba(220,20,20,0.8)",
            opacity: interpolate(frame, [2.3 * fps, 2.5 * fps], [0, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
              easing: Easing.linear,
            }),
            scale: interpolate(frame, [2.3 * fps, 3.2 * fps], [1.35, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
              easing: Easing.bezier(0.16, 1, 0.3, 1),
              output: "perceptual-scale",
            }),
            translate: interpolate(
              frame,
              [2.3 * fps, 2.4 * fps, 2.5 * fps, 2.6 * fps, 2.8 * fps],
              ["-26px 14px", "22px -18px", "-16px 10px", "10px -6px", "0px 0px"],
              {
                extrapolateLeft: "clamp",
                extrapolateRight: "clamp",
                easing: Easing.linear,
              },
            ),
          }}
        >
          FROM INSIDE.
        </Interactive.Div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
