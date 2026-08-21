import { AbsoluteFill, random, useCurrentFrame } from "remotion";
import { COLORS } from "./timing";

/**
 * The pieces that do the actual scaring. Kept separate from the templates so
 * each one can be reasoned about (and turned down) on its own.
 */

/**
 * RGB split. Real horror grading uses chromatic aberration to signal that the
 * image itself is under stress — it reads as "something is wrong with the
 * recording", which is far more unsettling than another shadow.
 *
 * Renders the same text three times in red/cyan/white, offset by `amount` px.
 */
export const ChromaText: React.FC<{
  children: React.ReactNode;
  style?: React.CSSProperties;
  amount?: number;
}> = ({ children, style, amount = 0 }) => {
  if (amount <= 0.2) {
    return <div style={style}>{children}</div>;
  }

  const ghost: React.CSSProperties = {
    ...style,
    position: "absolute",
    left: 0,
    right: 0,
    textShadow: "none",
  };

  return (
    <div style={{ position: "relative", ...style }}>
      <div
        aria-hidden
        style={{
          ...ghost,
          color: "#ff2d2d",
          mixBlendMode: "screen",
          transform: `translate(${-amount}px, ${amount * 0.35}px)`,
        }}
      >
        {children}
      </div>
      <div
        aria-hidden
        style={{
          ...ghost,
          color: "#22e0ff",
          mixBlendMode: "screen",
          transform: `translate(${amount}px, ${-amount * 0.35}px)`,
        }}
      >
        {children}
      </div>
      {/* The style is reapplied here, not only on the wrapper: the wrapper is
          also the positioning context for the ghosts, and leaving the readable
          layer to inherit meant a single missed property (the fill colour) made
          the word render in whatever the ghosts happened to blend to. */}
      <div style={{ ...style, position: "relative" }}>{children}</div>
    </div>
  );
};

/**
 * Where a big accent sits in the frame.
 *
 * Upper third, deliberately. The captions were moved to 62% of the height to
 * clear the platform UI, and accents used to be centred — so a scene with an
 * accent drew huge type straight through the caption band and neither could be
 * read. One component rather than five copies of the same flexbox, so the two
 * layers cannot drift back into each other.
 */
export const AccentStage: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <AbsoluteFill
    style={{
      justifyContent: "flex-start",
      alignItems: "center",
      paddingTop: "24%",
      paddingLeft: 70,
      paddingRight: 70,
    }}
  >
    {children}
  </AbsoluteFill>
);

/**
 * Sizing and legibility for the big on-screen accents.
 *
 * Two failures this fixes, both visible in the first cut of the renderer:
 *
 *  1. The size was hardcoded per template (156px, 200px). A four-word accent at
 *     200px runs off both edges of a 1080-wide frame.
 *  2. The accent was drawn in bone-white with a soft glow and nothing else, so
 *     over a mid-grey frame it rendered grey-on-grey and could not be read at
 *     all. A hard stroke fixes that on any backdrop, which is the whole point
 *     of putting one word on screen.
 *
 * Sizing is by character count rather than by measuring the laid-out text: the
 * accent is capped at 24 characters upstream, the face is fixed, and a
 * measure-and-reflow pass would need a layout dependency for a problem this
 * shape does not have.
 */
const ACCENT_COMFORTABLE_CHARS = 11;

/** Usable width inside AccentStage's padding, on the 1080-wide composition. */
const ACCENT_MAX_WIDTH_PX = 940;

/** Rough advance width of the display faces, as a fraction of font size. */
const ACCENT_CHAR_RATIO = 0.52;

/** How far past the template's base size a very short accent may grow. */
const ACCENT_MAX_GROWTH = 2.2;

export const accentFontSize = (
  text: string,
  base: number,
  maxWidthPx: number = ACCENT_MAX_WIDTH_PX,
): number => {
  const longestWord = text
    .split(/\s+/)
    .reduce((acc, w) => Math.max(acc, w.length), 0);
  if (!longestWord) {
    return base;
  }

  // Whichever is tighter: the whole line fitting, or the longest single word
  // fitting. A word never wraps mid-way, so it sets its own floor.
  const byLine = ACCENT_COMFORTABLE_CHARS / Math.max(text.length, 1);
  const byWord = (ACCENT_COMFORTABLE_CHARS * 0.85) / longestWord;

  // Growth, not just shrinkage. The old version clamped this ratio at 1, so a
  // three-letter word got exactly the same size as an eleven-letter one and
  // sat marooned in the middle of an empty frame. "RUN" should own the screen.
  const scale = Math.min(ACCENT_MAX_GROWTH, Math.max(byLine, byWord * 0.55, 0.42));

  // Hard ceiling so the longest word can never run past the stage padding,
  // whatever the ratio above wanted.
  const ceiling = maxWidthPx / (ACCENT_CHAR_RATIO * longestWord);

  return Math.round(Math.min(base * scale, ceiling));
};

/**
 * The shared accent style. `base` is the size the template wants at a
 * comfortable length; the real size is derived from the text.
 */
export const accentStyle = (
  text: string,
  base: number,
  overrides: React.CSSProperties = {},
): React.CSSProperties => {
  const fontSize = accentFontSize(text, base);
  return {
    fontSize,
    lineHeight: 1.1,
    textAlign: "center",
    color: COLORS.bone,
    // Stroke first, fill on top: legible over a bright window or a black
    // corridor without changing the type itself.
    WebkitTextStroke: `${Math.max(3, Math.round(fontSize * 0.035))}px #08070a`,
    paintOrder: "stroke fill",
    ...overrides,
  };
};

/**
 * Horizontal tear bands, like broken VHS tracking. Fires in short bursts
 * rather than continuously — constant glitch stops registering within seconds.
 */
export const TrackingTear: React.FC<{ seed: number; active: number }> = ({
  seed,
  active,
}) => {
  const frame = useCurrentFrame();
  if (active <= 0.01) {
    return null;
  }

  const bands = Math.round(2 + active * 5);
  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      {Array.from({ length: bands }, (_, i) => {
        const r = random(`tear${seed}-${Math.floor(frame / 2)}-${i}`);
        const r2 = random(`tearx${seed}-${Math.floor(frame / 2)}-${i}`);
        return (
          <div
            key={i}
            style={{
              position: "absolute",
              left: 0,
              right: 0,
              top: `${r * 100}%`,
              height: 2 + r2 * 22 * active,
              background: `rgba(255,255,255,${(0.04 + r2 * 0.12 * active).toFixed(3)})`,
              transform: `translateX(${(r2 - 0.5) * 90 * active}px)`,
              mixBlendMode: "overlay",
            }}
          />
        );
      })}
    </AbsoluteFill>
  );
};

/** Scanlines. Cheap, but it sells "found footage" instantly. */
export const Scanlines: React.FC<{ opacity?: number }> = ({ opacity = 0.16 }) => (
  <AbsoluteFill
    style={{
      backgroundImage:
        "repeating-linear-gradient(0deg, rgba(0,0,0,0.55) 0px, rgba(0,0,0,0.55) 1px, transparent 1px, transparent 4px)",
      opacity,
      pointerEvents: "none",
    }}
  />
);

/**
 * Full-frame flash. One or two frames of near-white or blood red.
 *
 * This is the single most effective jump-scare tool available and the easiest
 * to overuse — `Flash` is intentionally hard-limited to a couple of frames.
 */
export const Flash: React.FC<{ strength: number; color?: string }> = ({
  strength,
  color = "#ffffff",
}) => {
  if (strength <= 0.01) {
    return null;
  }
  return (
    <AbsoluteFill
      style={{
        backgroundColor: color,
        opacity: Math.min(strength, 0.92),
        mixBlendMode: "screen",
      }}
    />
  );
};

/**
 * Two points of reflected light at face height, resolving out of the dark.
 *
 * Deliberately abstract — no figure is ever drawn. The viewer supplies the
 * rest, which is both scarier and sidesteps the mangled-anatomy problem that
 * made generated imagery unusable for this niche in the first place.
 */
export const EyesInTheDark: React.FC<{
  progress: number;
  seed: number;
  y?: string;
}> = ({ progress, seed, y = "38%" }) => {
  const frame = useCurrentFrame();
  if (progress <= 0.01) {
    return null;
  }

  // Blink: rare, brief, and never on a predictable beat.
  const blink = random(`blink${seed}-${Math.floor(frame / 5)}`) < 0.06 ? 0.12 : 1;
  const drift = Math.sin(frame / 26) * 7;
  const opacity = Math.min(progress, 1) * blink;

  // Sized for a 1080-wide frame viewed on a phone: small enough to stay
  // ambiguous, large enough that the viewer definitely registers them.
  const eye: React.CSSProperties = {
    position: "absolute",
    top: y,
    width: 92,
    height: 38,
    borderRadius: "50%",
    background:
      "radial-gradient(circle, rgba(255,244,232,0.98) 0%, rgba(235,80,50,0.7) 50%, transparent 78%)",
    filter: "blur(5px)",
    boxShadow: "0 0 120px rgba(235,70,45,0.85), 0 0 240px rgba(180,20,20,0.5)",
    opacity,
  };

  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      <div style={{ ...eye, left: `calc(40% + ${drift}px)` }} />
      <div style={{ ...eye, left: `calc(52% + ${drift}px)` }} />
    </AbsoluteFill>
  );
};

/**
 * A darker mass rising from the bottom of the frame — something approaching,
 * shape unresolved. Pure gradient, so it never renders a broken silhouette.
 */
export const ApproachingMass: React.FC<{ progress: number }> = ({ progress }) => {
  if (progress <= 0.01) {
    return null;
  }
  const height = 8 + progress * 62;
  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      <div
        style={{
          position: "absolute",
          left: "-10%",
          right: "-10%",
          bottom: 0,
          height: `${height}%`,
          background:
            "radial-gradient(ellipse 60% 100% at 50% 100%, rgba(0,0,0,0.97) 40%, rgba(0,0,0,0.6) 70%, transparent 100%)",
          filter: "blur(26px)",
        }}
      />
    </AbsoluteFill>
  );
};

/** Blood-dark wash used at the turn. Separate from Flash so it can linger. */
export const BloodWash: React.FC<{ strength: number }> = ({ strength }) => {
  if (strength <= 0.01) {
    return null;
  }
  return (
    <AbsoluteFill
      style={{
        background: `radial-gradient(circle at 50% 45%, rgba(190,20,24,${(
          strength * 0.85
        ).toFixed(3)}) 0%, rgba(70,0,4,${(strength * 0.95).toFixed(3)}) 70%)`,
        mixBlendMode: "multiply",
      }}
    />
  );
};

/** Jittering duplicate of a word, one frame behind. Used on the scare hit. */
export const GlitchShadow: React.FC<{
  children: React.ReactNode;
  style?: React.CSSProperties;
  amount: number;
  seed: number;
}> = ({ children, style, amount, seed }) => {
  const frame = useCurrentFrame();
  if (amount <= 0.01) {
    return null;
  }
  const dx = (random(`gx${seed}-${frame}`) - 0.5) * 40 * amount;
  const dy = (random(`gy${seed}-${frame}`) - 0.5) * 20 * amount;
  return (
    <div
      aria-hidden
      style={{
        ...style,
        position: "absolute",
        left: 0,
        right: 0,
        color: COLORS.blood,
        opacity: 0.55 * amount,
        transform: `translate(${dx}px, ${dy}px)`,
        mixBlendMode: "screen",
      }}
    >
      {children}
    </div>
  );
};
