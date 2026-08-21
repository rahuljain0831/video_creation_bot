import {
  AbsoluteFill,
  interpolate,
  random,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

/**
 * Shared horror backdrop.
 *
 * `intensity` (0..1) is the dread ramp — the composition raises it scene by
 * scene, so the room visibly closes in as the story goes on: the vignette
 * tightens, grain coarsens, the light flickers harder and the red bleeds in.
 * A horror short that looks the same at 0:05 and 0:60 has no build.
 *
 * random() and the SVG turbulence seed are Remotion's deterministic RNG, not
 * Math.random — that is what lets multiple render workers agree on each frame.
 * Do not "modernise" them.
 */
export const Atmosphere: React.FC<{
  seed?: number;
  intensity?: number;
  /**
   * True when a generated image sits underneath. The opaque ground and the
   * heavy fog blooms are dropped so the picture reads through — otherwise the
   * atmosphere pass simply erases the image it is supposed to be grading.
   */
  transparent?: boolean;
}> = ({ seed = 0, intensity = 0, transparent = false }) => {
  const frame = useCurrentFrame();
  const { durationInFrames, fps } = useVideoConfig();
  const t = frame / fps;

  // Breathing vignette — a slow pulse, quickening as dread rises.
  // Vignette stays generous on purpose. Phone screens in daylight eat shadow
  // detail, and a horror short nobody can see is not scary, it is broken —
  // so the frame closes in, but never to the point of pure black.
  const breathRate = 0.55 + intensity * 0.85;
  const breath = Math.sin(t * breathRate * Math.PI) * 0.5 + 0.5;
  const vignetteInner = 34 - intensity * 8 - breath * 3;
  const vignetteOuter = 88 - intensity * 10;

  // Flicker: mostly steady, with occasional hard dropouts that get more
  // frequent as intensity climbs. The dropouts are what make it feel wrong.
  const flickerBase = random(`f${seed}-${frame}`);
  const dropout = random(`d${seed}-${Math.floor(frame / 3)}`) < 0.04 + intensity * 0.09;
  const lightLevel = dropout ? 0.22 : 0.6 + flickerBase * 0.4;

  const grainOpacity = 0.14 + intensity * 0.16;

  // Over a picture this pass is a grade, not a scene. The fog blooms and the
  // red bleed exist to build something out of an empty black frame; laid over a
  // photograph they simply cover it, which is how a generated backdrop ended up
  // looking like a grey wash. Grain and a soft vignette survive — those read as
  // film stock rather than as paint.
  const redBleed = transparent ? 0 : 0.18 + intensity * 0.35;
  const fogAlpha = transparent ? 0 : 0.55;

  return (
    <AbsoluteFill
      style={transparent ? undefined : { backgroundColor: "#030304" }}
    >
      {/* Cold drifting fog */}
      <AbsoluteFill
        style={{
          background: `radial-gradient(65% 45% at 30% 25%, rgba(96,110,130,${fogAlpha}), transparent 72%)`,
          filter: "blur(60px)",
          translate: interpolate(
            frame,
            [0, durationInFrames],
            ["-60px 0px", "80px -40px"],
            { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
          ),
        }}
      />
      {/* Warm rot creeping up from below */}
      <AbsoluteFill
        style={{
          background: `radial-gradient(50% 35% at 70% 80%, rgba(120,20,25,${redBleed.toFixed(
            3,
          )}), transparent 70%)`,
          filter: "blur(80px)",
          translate: interpolate(
            frame,
            [0, durationInFrames],
            ["40px 30px", "-50px -20px"],
            { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
          ),
        }}
      />
      {/* Failing overhead light. Over a picture it only modulates the existing
          exposure, so it is kept faint — the flicker should read on the image,
          not replace it. */}
      <AbsoluteFill
        style={{
          background:
            "radial-gradient(50% 34% at 50% 15%, rgba(255,240,200,0.28), transparent 72%)",
          opacity: lightLevel * (transparent ? 0.3 : 1),
        }}
      />
      {/* Film grain — coarsens with dread */}
      <AbsoluteFill style={{ opacity: grainOpacity, mixBlendMode: "overlay" }}>
        <svg width="100%" height="100%">
          <filter id="scary-grain">
            <feTurbulence
              type="fractalNoise"
              baseFrequency={0.85 - intensity * 0.35}
              numOctaves={3}
              seed={(seed + frame) % 12}
            />
          </filter>
          <rect width="100%" height="100%" filter="url(#scary-grain)" />
        </svg>
      </AbsoluteFill>
      {/* Breathing vignette */}
      <AbsoluteFill
        style={{
          background: `radial-gradient(circle at 50% 45%, transparent ${vignetteInner.toFixed(
            1,
          )}%, rgba(0,0,0,${transparent ? 0.42 : 0.88}) ${vignetteOuter.toFixed(1)}%)`,
        }}
      />
    </AbsoluteFill>
  );
};
