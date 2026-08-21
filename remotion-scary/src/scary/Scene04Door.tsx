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

export const Scene04Door: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  return (
    <AbsoluteFill name="Scene 4 — The door">
      <Atmosphere />
      {/* Widening strip of light from the opening door */}
      <AbsoluteFill
        style={{
          justifyContent: "center",
          alignItems: "center",
        }}
      >
        <Interactive.Div
          name="Door gap"
          style={{
            width: 26,
            height: 1500,
            background:
              "linear-gradient(180deg, rgba(255,240,210,0) 0%, rgba(255,240,210,0.55) 45%, rgba(255,240,210,0) 100%)",
            filter: "blur(14px)",
            scale: interpolate(frame, [0.5 * fps, 3.2 * fps], [0.2, 4.5], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
              easing: Easing.bezier(0.33, 0, 0.67, 1),
              output: "perceptual-scale",
            }),
            opacity: interpolate(
              frame,
              [0.5 * fps, 1.5 * fps, 3.5 * fps],
              [0, 0.9, 0.25],
              {
                extrapolateLeft: "clamp",
                extrapolateRight: "clamp",
                easing: Easing.linear,
              },
            ),
          }}
        />
      </AbsoluteFill>
      <AbsoluteFill
        style={{
          justifyContent: "flex-end",
          alignItems: "center",
          padding: 120,
          textAlign: "center",
        }}
      >
        <Interactive.Div
          name="Line"
          style={{
            fontFamily: bodyFont,
            fontSize: 96,
            color: "#e9e2d4",
            lineHeight: 1.3,
            textShadow: "0 0 40px rgba(0,0,0,0.95)",
            opacity: interpolate(frame, [0.4 * fps, 1.2 * fps], [0, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
              easing: Easing.bezier(0.16, 1, 0.3, 1),
            }),
            translate: interpolate(
              frame,
              [0.4 * fps, 1.2 * fps],
              ["0px 40px", "0px 0px"],
              {
                extrapolateLeft: "clamp",
                extrapolateRight: "clamp",
                easing: Easing.bezier(0.16, 1, 0.3, 1),
              },
            ),
          }}
        >
          Last night, I opened the door.
        </Interactive.Div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
