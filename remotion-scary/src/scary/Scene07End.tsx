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

export const Scene07End: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  return (
    <AbsoluteFill name="Scene 7 — End card">
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
          name="End title"
          style={{
            fontFamily: titleFont,
            fontSize: 168,
            color: "#c8242a",
            letterSpacing: 4,
            textShadow: "0 0 70px rgba(200,30,30,0.5)",
            opacity: interpolate(frame, [0, 1 * fps], [0, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
              easing: Easing.bezier(0.16, 1, 0.3, 1),
            }),
          }}
        >
          DON&apos;T ANSWER
        </Interactive.Div>
        <Interactive.Div
          name="End sub"
          style={{
            fontFamily: bodyFont,
            fontSize: 50,
            color: "#8b8377",
            marginTop: 56,
            letterSpacing: 6,
            opacity: interpolate(
              frame,
              [1.2 * fps, 2 * fps, 2.6 * fps, 3 * fps],
              [0, 1, 1, 0],
              {
                extrapolateLeft: "clamp",
                extrapolateRight: "clamp",
                easing: Easing.linear,
              },
            ),
          }}
        >
          more stories every night at 3:07
        </Interactive.Div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
