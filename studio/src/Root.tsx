import { Composition } from "remotion";
import { Reel, ReelProps } from "./Reel";

const FPS = 30;

const demo: ReelProps = {
  total: 6,
  audio: "",
  scenes: [
    { text: "Thirty reels and you watched none of them.", screen: "30 reels, zero watching", emphasis: ["none"], chips: ["/human", "SKILL.md"], start: 0.4, end: 3.2,
      words: [["Thirty", 0.4, 0.8], ["reels", 0.8, 1.2], ["and", 1.2, 1.4], ["you", 1.4, 1.7], ["watched", 1.7, 2.2], ["none", 2.2, 2.6], ["of", 2.6, 2.8], ["them.", 2.8, 3.2]] },
  ],
};

export const Root = () => (
  <Composition
    id="Reel"
    component={Reel}
    width={1080}
    height={1920}
    fps={FPS}
    durationInFrames={Math.ceil(demo.total * FPS)}
    defaultProps={demo}
    calculateMetadata={({ props }) => ({ durationInFrames: Math.max(30, Math.ceil(props.total * FPS)) })}
  />
);
