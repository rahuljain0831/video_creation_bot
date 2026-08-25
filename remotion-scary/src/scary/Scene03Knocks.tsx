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

export const Scene03Knocks: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  return (
    <AbsoluteFill name="Scene 3 — Three knocks">
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
          name="Knock 1"
          style={{
            fontFamily: bodyFont,
            fontSize: 130,
            color: "#e6ded0",
            opacity: interpolate(
              frame,
              [0.2 * fps, 0.4 * fps, 3 * fps],
              [0, 1, 1],
              {
                extrapolateLeft: "clamp",
                extrapolateRight: "clamp",
                easing: Easing.linear,
              },
            ),
            scale: interpolate(frame, [0.2 * fps, 0.6 * fps], [1.6, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
              easing: Easing.bezier(0.16, 1, 0.3, 1),
              output: "perceptual-scale",
            }),
          }}
        >
          KNOCK.
        </Interactive.Div>
        <Interactive.Div
          name="Knock 2"
          style={{
            fontFamily: bodyFont,
            fontSize: 130,
            color: "#e6ded0",
            marginTop: 20,
            opacity: interpolate(
              frame,
              [1 * fps, 1.2 * fps, 3 * fps],
              [0, 1, 1],
              {
                extrapolateLeft: "clamp",
                extrapolateRight: "clamp",
                easing: Easing.linear,
              },
            ),
            scale: interpolate(frame, [1 * fps, 1.4 * fps], [1.6, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
              easing: Easing.bezier(0.16, 1, 0.3, 1),
              output: "perceptual-scale",
            }),
          }}
        >
          KNOCK.
        </Interactive.Div>
        <Interactive.Div
          name="Knock 3"
          style={{
            fontFamily: bodyFont,
            fontSize: 130,
            color: "#c8242a",
            marginTop: 20,
            textShadow: "0 0 50px rgba(200,30,30,0.6)",
            opacity: interpolate(
              frame,
              [1.8 * fps, 2 * fps, 3 * fps],
              [0, 1, 1],
              {
                extrapolateLeft: "clamp",
                extrapolateRight: "clamp",
                easing: Easing.linear,
              },
            ),
            scale: interpolate(frame, [1.8 * fps, 2.2 * fps], [1.6, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
              easing: Easing.bezier(0.16, 1, 0.3, 1),
              output: "perceptual-scale",
            }),
          }}
        >
          KNOCK.
        </Interactive.Div>
        <Interactive.Div
          name="Caption"
          style={{
            fontFamily: bodyFont,
            fontSize: 58,
            color: "#948c80",
            marginTop: 60,
            opacity: interpolate(frame, [2.6 * fps, 3.2 * fps], [0, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
              easing: Easing.bezier(0.16, 1, 0.3, 1),
            }),
          }}
        >
          Always three. Never more.
        </Interactive.Div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
