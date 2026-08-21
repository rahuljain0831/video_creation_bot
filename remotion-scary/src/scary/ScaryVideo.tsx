import { linearTiming, TransitionSeries } from "@remotion/transitions";
import { fade } from "@remotion/transitions/fade";
import { AbsoluteFill } from "remotion";
import { Scene01Hook } from "./Scene01Hook";
import { Scene02Alone } from "./Scene02Alone";
import { Scene03Knocks } from "./Scene03Knocks";
import { Scene04Door } from "./Scene04Door";
import { Scene05Empty } from "./Scene05Empty";
import { Scene06Twist } from "./Scene06Twist";
import { Scene07End } from "./Scene07End";

// Scene durations sum to 810 frames; 6 fades of 15 frames overlap,
// so the composition is 810 - 90 = 720 frames (24s at 30fps).
export const ScaryVideo: React.FC = () => {
  return (
    <AbsoluteFill style={{ backgroundColor: "#000000" }}>
      <TransitionSeries>
        <TransitionSeries.Sequence durationInFrames={120} name="Hook">
          <Scene01Hook />
        </TransitionSeries.Sequence>
        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: 15 })}
        />
        <TransitionSeries.Sequence durationInFrames={105} name="Alone">
          <Scene02Alone />
        </TransitionSeries.Sequence>
        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: 15 })}
        />
        <TransitionSeries.Sequence durationInFrames={120} name="Knocks">
          <Scene03Knocks />
        </TransitionSeries.Sequence>
        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: 15 })}
        />
        <TransitionSeries.Sequence durationInFrames={120} name="Door">
          <Scene04Door />
        </TransitionSeries.Sequence>
        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: 15 })}
        />
        <TransitionSeries.Sequence durationInFrames={105} name="Empty">
          <Scene05Empty />
        </TransitionSeries.Sequence>
        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: 15 })}
        />
        <TransitionSeries.Sequence durationInFrames={150} name="Twist">
          <Scene06Twist />
        </TransitionSeries.Sequence>
        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({ durationInFrames: 15 })}
        />
        <TransitionSeries.Sequence durationInFrames={90} name="End card">
          <Scene07End />
        </TransitionSeries.Sequence>
      </TransitionSeries>
    </AbsoluteFill>
  );
};
