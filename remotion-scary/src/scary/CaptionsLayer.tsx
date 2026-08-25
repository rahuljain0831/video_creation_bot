import React from "react";
import { AbsoluteFill, Easing, interpolate, Sequence, useCurrentFrame } from "remotion";
import { bodyFont } from "./fonts";
import { EASE_OUT } from "./timing";
import type { CaptionChunk, CaptionStyle } from "./schema";

/**
 * Word-synced caption band.
 *
 * Chunks (~3 words) arrive already framed by Python, which reuses the same
 * grouping the ffmpeg ASS karaoke builder uses, so both renderers caption
 * identically. One Sequence per chunk rather than per word keeps this at ~60
 * sequences for a 70s video instead of ~250.
 *
 * Size and position are both set from settings.json, the size against a 480p
 * reference. Two constraints pull against each other here:
 *
 *   - Too small and it is unreadable on a phone. 56px on a 1920-tall frame is
 *     3% of the height, which is where this started and why it moved up to 96.
 *     It now sits near 72px — subtitle-sized rather than caption-sized.
 *   - Too low and the platform draws its own UI over it. The bottom ~15% of a
 *     vertical video (below ~1630px of 1920) belongs to the feed, not to us.
 *
 * `anchorYPercent` is the *top* of the caption block, so the real limit is that
 * anchor plus the rendered line height. At 78% a single line of 72px type ends
 * near 1585px, which clears the band; a chunk that wraps to two lines does not,
 * which is why `chunk_size` stays at 3 and the side padding stays wide.
 *
 * Accents live in the upper third (see AccentStage in effects.tsx), so there is
 * no collision to worry about in the other direction.
 *
 * The typewriter face stays — it is the one thing that stopped these looking
 * like every other auto-captioned short.
 *
 * Sits outside the TransitionSeries so cross-fades never dim the text.
 */

const POP_FRAMES = 3;

const Chunk: React.FC<{ chunk: CaptionChunk; style: CaptionStyle }> = ({ chunk, style }) => {
  const frame = useCurrentFrame();
  const absolute = chunk.fromFrame + frame;

  return (
    <AbsoluteFill>
      <div
        style={{
          // `top` as a percentage resolves against the containing block's
          // HEIGHT. `padding-top` as a percentage resolves against its WIDTH —
          // which is what this used to use, so "anchor at 62%" silently meant
          // 62% of 1080 = 670px, or 35% of the frame. The captions were sitting
          // near the middle of the picture the whole time.
          position: "absolute",
          top: `${style.anchorYPercent}%`,
          left: 80,
          right: 80,
          fontFamily: bodyFont,
          fontSize: style.fontSizePx,
          fontWeight: 700,
          lineHeight: 1.22,
          textAlign: "center",
          whiteSpace: "pre-wrap",
          // The stroke is what carries legibility over a photograph. A shadow
          // alone disappears the moment a caption lands on a bright patch.
          WebkitTextStroke: `${Math.round(style.fontSizePx * 0.07)}px #000`,
          paintOrder: "stroke fill",
          textShadow: "0 0 30px rgba(0,0,0,0.95), 0 3px 8px rgba(0,0,0,0.9)",
        }}
      >
        {chunk.words.map((word, i) => {
          const active = word.fromFrame <= absolute && absolute < word.toFrame;
          // A short scale pop on the word being spoken. Small on purpose:
          // enough to draw the eye, not enough to make the line jump.
          const pop = interpolate(
            absolute,
            [word.fromFrame, word.fromFrame + POP_FRAMES],
            [1.12, 1],
            {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
              easing: Easing.bezier(...EASE_OUT),
            },
          );
          // The separator is a text node, not part of the span: a leading space
          // inside an inline-block collapses, which would run the words together.
          return (
            <React.Fragment key={i}>
              {i === 0 ? null : " "}
              <span
                style={{
                  display: "inline-block",
                  // The pop scales the whole box about its centre, which grows
                  // the glyphs over the word separators on either side and runs
                  // the active word into its neighbour. The padding is inside
                  // the box, so it scales too and the gap survives.
                  padding: "0 0.06em",
                  color: active ? style.activeColor : style.idleColor,
                  scale: active ? String(pop) : "1",
                }}
              >
                {word.text}
              </span>
            </React.Fragment>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};

export const CaptionsLayer: React.FC<{
  captions: CaptionChunk[];
  style: CaptionStyle;
}> = ({ captions, style }) => {
  return (
    <AbsoluteFill>
      {captions.map((chunk, i) => {
        const duration = chunk.toFrame - chunk.fromFrame;
        if (duration <= 0) {
          return null;
        }
        return (
          <Sequence key={i} from={chunk.fromFrame} durationInFrames={duration}>
            <Chunk chunk={chunk} style={style} />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
