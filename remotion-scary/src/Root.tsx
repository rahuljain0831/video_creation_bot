import "./index.css";
import { Composition, Folder } from "remotion";
import { ScaryStory, calculateScaryStoryMetadata } from "./scary/ScaryStory";
import { scaryStorySchema } from "./scary/schema";
import sampleProps from "./sample-props.json";
import { ScaryVideo } from "./scary/ScaryVideo";
import { Scene01Hook } from "./scary/Scene01Hook";
import { Scene02Alone } from "./scary/Scene02Alone";
import { Scene03Knocks } from "./scary/Scene03Knocks";
import { Scene04Door } from "./scary/Scene04Door";
import { Scene05Empty } from "./scary/Scene05Empty";
import { Scene06Twist } from "./scary/Scene06Twist";
import { Scene07End } from "./scary/Scene07End";

export const RemotionRoot: React.FC = () => {
  return (
    <>
      {/*
        The composition the Python pipeline renders. Everything about it comes
        from --props; the defaults below are the checked-in sample so the
        Studio and `npm run render:sample` work with no audio file present.
        durationInFrames is a placeholder — calculateMetadata computes the real
        value from the props.
      */}
      <Composition
        id="ScaryStory"
        component={ScaryStory}
        schema={scaryStorySchema}
        defaultProps={scaryStorySchema.parse(sampleProps)}
        calculateMetadata={calculateScaryStoryMetadata}
        durationInFrames={1800}
        fps={30}
        width={1080}
        height={1920}
      />

      {/*
        The original hand-authored short. Kept as the visual reference the
        prop-driven templates were lifted from — handy for eyeballing whether a
        refactor drifted from the look that worked.
      */}
      <Folder name="Reference">
        <Composition
          id="ScaryVideo"
          component={ScaryVideo}
          durationInFrames={720}
          fps={30}
          width={1080}
          height={1920}
        />
        <Composition
          id="Scene01Hook"
          component={Scene01Hook}
          durationInFrames={120}
          fps={30}
          width={1080}
          height={1920}
        />
        <Composition
          id="Scene02Alone"
          component={Scene02Alone}
          durationInFrames={105}
          fps={30}
          width={1080}
          height={1920}
        />
        <Composition
          id="Scene03Knocks"
          component={Scene03Knocks}
          durationInFrames={120}
          fps={30}
          width={1080}
          height={1920}
        />
        <Composition
          id="Scene04Door"
          component={Scene04Door}
          durationInFrames={120}
          fps={30}
          width={1080}
          height={1920}
        />
        <Composition
          id="Scene05Empty"
          component={Scene05Empty}
          durationInFrames={105}
          fps={30}
          width={1080}
          height={1920}
        />
        <Composition
          id="Scene06Twist"
          component={Scene06Twist}
          durationInFrames={150}
          fps={30}
          width={1080}
          height={1920}
        />
        <Composition
          id="Scene07End"
          component={Scene07End}
          durationInFrames={90}
          fps={30}
          width={1080}
          height={1920}
        />
      </Folder>
    </>
  );
};
