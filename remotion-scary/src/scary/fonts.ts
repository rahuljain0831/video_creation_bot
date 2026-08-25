import { loadFont as loadCreepster } from "@remotion/google-fonts/Creepster";
import { loadFont as loadSpecialElite } from "@remotion/google-fonts/SpecialElite";

export const { fontFamily: titleFont } = loadCreepster("normal", {
  weights: ["400"],
  subsets: ["latin"],
});

export const { fontFamily: bodyFont } = loadSpecialElite("normal", {
  weights: ["400"],
  subsets: ["latin"],
});
