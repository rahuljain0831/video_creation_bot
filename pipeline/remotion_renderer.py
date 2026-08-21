"""
Remotion renderer — drop-in replacement for `ffmpeg_assembler.assemble_from_images`
for niches whose visuals are drawn procedurally rather than assembled from images.

Division of labour with the TypeScript side: **Python owns every number.**
Tick-to-frame conversion, scene boundaries, transition padding and caption
chunking all happen here, and the composition receives frames it can render
without arithmetic. Two reasons:

  * `word_timings.json` is in 100-nanosecond ticks and `scene_timing.py`
    normalises scene durations so they sum to the measured audio length. TS
    re-deriving boundaries from raw ticks would skip that normalisation and
    drift against the narration by the trailing-silence amount.
  * A bad number raises a normal traceback here instead of a React error buried
    in headless-Chrome stdout.

Caption chunking mirrors `ffmpeg_assembler._build_ass_karaoke` (same chunk size,
same "extend each word to the next word's start" rule) so the two renderers
caption identically.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path



log = logging.getLogger(__name__)

_TICKS_PER_SECOND = 10_000_000

_DEFAULT_FPS = 30
_DEFAULT_RES = (1080, 1920)
_DEFAULT_PROJECT_DIR = "remotion-scary"
_DEFAULT_ENTRY = "src/index.ts"
_DEFAULT_COMPOSITION = "ScaryStory"
_DEFAULT_TRANSITION_FRAMES = 12
_DEFAULT_TIMEOUT_SEC = 1800
_DEFAULT_CRF = 20

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Preflight stats the filesystem; a retry loop shouldn't repeat it.
_PREFLIGHT_CACHE: dict[str, tuple[Path, Path, list[str]]] = {}


class RemotionRenderError(RuntimeError):
    """Raised when the Remotion render cannot be started, or produced bad output."""


# ── Frame planning ────────────────────────────────────────────────────────────

def plan_frames(
    scene_durations: list[float],
    fps: int,
    transition_frames: int,
    target_total_frames: int | None = None,
) -> tuple[list[int], list[int], list[int], int, int]:
    """
    Turn per-scene seconds into TransitionSeries frame counts.

    `TransitionSeries` overlaps adjacent sequences, consuming the transition
    length from the total: `total = Σ seq - (n-1) * T`. Left alone that would
    shorten the video by (n-1)*T frames and truncate the narration, so every
    sequence after the first is padded by T. The padding cancels exactly:

        Σ padded - (n-1)*T == Σ narrative

    The visible effect is that scene i fades in starting T frames before its
    narration boundary and is fully opaque exactly on it — which is what you
    want from a cross-fade. `lead_in[i]` tells the template how far to shift its
    own animation beats so they stay locked to the voice.

    Returns (padded, narrative, lead_in, total_frames, effective_transition).
    """
    if not scene_durations:
        raise RemotionRenderError("plan_frames: no scene durations")

    narrative = [max(1, round(d * fps)) for d in scene_durations]

    if target_total_frames is not None:
        # Absorb rounding drift in the last scene so the video lands exactly on
        # the measured audio length.
        narrative[-1] = max(1, narrative[-1] + target_total_frames - sum(narrative))

    n = len(narrative)

    # Remotion throws if a transition is >= either adjacent sequence. Clamp to
    # a third of the shortest scene; one terse narration line must not kill the
    # whole render.
    effective_t = max(0, min(transition_frames, min(narrative) // 3))
    if effective_t != transition_frames:
        log.info(
            "Remotion: transition clamped %d → %d frames (shortest scene %d)",
            transition_frames, effective_t, min(narrative),
        )

    padded = [f + (effective_t if i else 0) for i, f in enumerate(narrative)]
    lead_in = [effective_t if i else 0 for i in range(n)]

    total = sum(padded) - (n - 1) * effective_t
    assert total == sum(narrative), "frame padding invariant broken"

    return padded, narrative, lead_in, total, effective_t


# ── Shot planning ─────────────────────────────────────────────────────────────

# A shot shorter than this reads as a glitch rather than a cut.
_MIN_SHOT_SEC = 0.9
# Below this a beat is too short to hold two shots at all.
_TWO_SHOT_SEC = 2.4
# Above this a beat can hold three, provided it has a second image to cut to.
_THREE_SHOT_SEC = 5.5

# First-shot camera move per beat, rotated so consecutive beats don't repeat.
_OPENING_MOVES = ("zoomIn", "panLeft", "zoomOut", "panRight")


def plan_shots(
    narrative_frames: int,
    beat_start: int,
    fps: int,
    word_starts: list[int],
    image_a: str | None,
    image_b: str | None,
    index: int,
) -> list[dict]:
    """
    Split one narration beat into 1-3 shots.

    The old renderer gave each beat a single image with one slow Ken Burns move,
    which put a visual change on screen every 5.3s. Short-form retention wants
    one every 2-3s, and the narration is already the wrong unit for that — a
    sentence takes as long as it takes.

    So the beat stays the unit of *meaning* and the shot becomes the unit of
    *picture*. A beat with a second image cuts to it; a beat without one cuts to
    a tighter framing of the same image, which still reads as a cut because the
    scale changes on a single frame.

    Cuts land on the nearest word start, never mid-word. A cut inside a word is
    the one thing viewers consciously notice.

    `word_starts` are absolute frames; `beat_start` locates this beat within
    them. Returns shots whose durations sum to exactly `narrative_frames`.
    """
    min_shot = max(2, round(_MIN_SHOT_SEC * fps))
    duration_sec = narrative_frames / fps

    def snap(target: int, low: int, high: int) -> int:
        """Nearest word start to `target`, clamped into [low, high]."""
        if low > high:
            return max(low, min(target, high))
        candidates = [
            w - beat_start for w in word_starts
            if low <= w - beat_start <= high
        ]
        best = min(candidates, key=lambda c: abs(c - target)) if candidates else target
        return max(low, min(best, high))

    # How many shots this beat can carry.
    if duration_sec < _TWO_SHOT_SEC or narrative_frames < min_shot * 2:
        count = 1
    elif duration_sec >= _THREE_SHOT_SEC and narrative_frames >= min_shot * 3:
        count = 3
    else:
        count = 2

    opening = _OPENING_MOVES[index % len(_OPENING_MOVES)]

    if count == 1:
        return [{"imageSrc": image_a, "durationInFrames": narrative_frames, "move": opening}]

    if count == 2:
        cut = snap(narrative_frames // 2, min_shot, narrative_frames - min_shot)
        second = image_b or image_a
        # Same picture means the cut has to come from the framing, so punch in.
        # A different picture is already a cut, so let it breathe instead.
        return [
            {"imageSrc": image_a, "durationInFrames": cut, "move": opening},
            {
                "imageSrc": second,
                "durationInFrames": narrative_frames - cut,
                "move": "punch" if second == image_a else "driftClose",
            },
        ]

    # Three shots: establish, cut to the detail, come back tighter.
    #
    # With no second image the middle shot is the same picture at a different
    # scale. That is a weaker cut than a real one, but a long beat held on one
    # slow drift is worse — nothing on screen changes for six seconds.
    first = snap(narrative_frames // 3, min_shot, narrative_frames - min_shot * 2)
    second = snap(first + narrative_frames // 3, first + min_shot, narrative_frames - min_shot)
    return [
        {"imageSrc": image_a, "durationInFrames": first, "move": opening},
        {"imageSrc": image_b or image_a, "durationInFrames": second - first,
         "move": "driftClose"},
        {"imageSrc": image_a, "durationInFrames": narrative_frames - second, "move": "punch"},
    ]


# ── Captions ──────────────────────────────────────────────────────────────────

def build_caption_chunks(
    word_timings_path: str | None,
    fps: int,
    chunk_size: int,
    total_frames: int,
) -> list[dict]:
    """
    word_timings.json (100-ns ticks) → caption chunks in frames.

    Each chunk holds `chunk_size` words shown together; within a chunk every
    word carries its own window so the composition can highlight the one being
    spoken. A word's window is extended to the next word's start so the
    highlight never blinks off between words.

    Returns [] when there are no timings — only edge_tts produces them, and a
    Piper/Kokoro fallback should still render, just without captions.
    """
    if not word_timings_path or not Path(word_timings_path).is_file():
        log.warning("Remotion: no word timings at %s — rendering without captions",
                    word_timings_path)
        return []

    try:
        words = json.loads(Path(word_timings_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("Remotion: unreadable word timings (%s) — rendering without captions", e)
        return []

    if not isinstance(words, list) or not words:
        return []

    def to_frame(ticks: float) -> int:
        return max(0, min(total_frames, round(ticks / _TICKS_PER_SECOND * fps)))

    chunks: list[dict] = []
    for start in range(0, len(words), chunk_size):
        group = words[start:start + chunk_size]
        out_words = []

        for j, w in enumerate(group):
            offset = w.get("offset", 0)
            dur = w.get("duration", 0)
            flat = start + j

            if flat + 1 < len(words):
                end_ticks = max(offset + dur, words[flat + 1].get("offset", offset + dur))
            else:
                # Last word: hold it a beat rather than snapping away.
                end_ticks = offset + max(dur, 0.3 * _TICKS_PER_SECOND)

            out_words.append({
                "text": str(w.get("text", "")),
                "fromFrame": to_frame(offset),
                "toFrame": to_frame(end_ticks),
            })

        if not out_words:
            continue

        chunk_from = out_words[0]["fromFrame"]
        chunk_to = out_words[-1]["toFrame"]
        if chunk_to <= chunk_from:
            continue

        chunks.append({"fromFrame": chunk_from, "toFrame": chunk_to, "words": out_words})

    return chunks


# ── Props ─────────────────────────────────────────────────────────────────────

def build_soundtrack(
    scenes: list[dict],
    narrative_frames: list[int],
    public_dir: Path,
    total_frames: int,
    fps: int,
    seed: int,
    audio_cfg: dict,
) -> tuple[str | None, list[dict], str | None, list[dict]]:
    """
    Synthesize the ambience bed and place stingers on the scary beats.

    Everything is generated into `public_dir` (the run's audio directory) so
    Remotion can reach it through --public-dir alongside the narration.

    Returns (ambience_filename, stings, bed_name, gaps). `gaps` are frame ranges
    where the ambience must go silent — see the riser below. On any ffmpeg
    failure this degrades to a narration-only render rather than losing the
    whole video: a silent-bed video is still publishable, a failed render is not.
    """
    from pipeline.horror_audio import (
        HorrorAudioError, build_ambience, build_sting, sting_duration,
    )

    if not audio_cfg.get("enabled", True):
        return None, [], None, []

    duration = total_frames / fps

    try:
        _path, bed_name = build_ambience(
            duration, public_dir / "ambience.wav", seed=seed,
            bed=audio_cfg.get("bed"),
            bed_pool=audio_cfg.get("bed_pool"),
        )
    except HorrorAudioError as e:
        log.warning("Horror ambience failed (%s) — rendering without a bed", e)
        return None, [], None, []

    # Where each scene's narration actually starts, in absolute frames.
    starts: list[int] = []
    running = 0
    for f in narrative_frames:
        starts.append(running)
        running += f

    # One-shot hits on the punched-accent / reveal / scare / end beats.
    #
    # Off for scary_stories. These are synthesized sweeps and noise bursts, and
    # against a photographic backdrop and a treated narration they read as a
    # cartoon sound effect rather than as a scare — the video plays as comedy
    # the moment one lands. The bed and the voice treatment carry the dread
    # instead. The recipes stay in horror_audio.py; flip `stings` back on in the
    # niche's `horror_audio` block to get them back.
    #
    # The riser goes with them: it exists only to set up the screech hit, and a
    # three-second rise that resolves into nothing is worse than no rise at all.
    if not audio_cfg.get("stings", True):
        return "ambience.wav", [], bed_name, []

    # Which template gets which hit, and how far into the scene it lands.
    # The offsets mirror the templates' own beats so sound and picture agree:
    # Impact.tsx puts its first punch at beat(0.1), Reveal opens at beat(0.12),
    # Scare hits at beat(0.45), End lands on its first frame.
    sting_plan = {
        "impact": ("impact", 0.1),
        "scare":  ("screech", 0.45),
        "reveal": ("impact", 0.12),
        "end":    ("boom", 0.0),
    }

    stings: list[dict] = []
    gaps: list[dict] = []
    built: set[str] = set()
    for i, scene in enumerate(scenes):
        plan = sting_plan.get(scene.get("visual", "line"))
        if not plan:
            continue
        kind, offset = plan
        filename = f"sting_{kind}.wav"

        if kind not in built:
            try:
                build_sting(public_dir / filename, seed=seed + i, kind=kind)
                built.add(kind)
            except HorrorAudioError as e:
                log.warning("Sting %s failed (%s) — skipping", kind, e)
                continue

        at = starts[i] + round(offset * narrative_frames[i])
        at = max(0, min(at, total_frames - 1))
        stings.append({
            "src": filename,
            "atFrame": at,
            "volume": float(audio_cfg.get("sting_volume", 0.85)),
        })

        # The scare gets a riser that ends just before the hit, and a few frames
        # of total silence in between. A jump-scare in a video whose bed never
        # stops is just a louder moment; the drop out of the rise is what the
        # ear actually reacts to.
        if kind != "screech" or not audio_cfg.get("riser", True):
            continue

        gap_frames = int(audio_cfg.get("riser_gap_frames", 5))
        riser_frames = round(sting_duration("riser") * fps)
        riser_at = at - gap_frames - riser_frames
        if riser_at < starts[i] - riser_frames // 2 or riser_at < 0:
            log.info("Soundtrack: no room for a riser before the scare — skipped")
            continue

        try:
            build_sting(public_dir / "sting_riser.wav", seed=seed + 77, kind="riser")
        except HorrorAudioError as e:
            log.warning("Riser failed (%s) — the scare lands without one", e)
            continue

        stings.append({
            "src": "sting_riser.wav",
            "atFrame": riser_at,
            "volume": float(audio_cfg.get("riser_volume", 0.9)),
        })
        # Duck the bed through the rise, then kill it dead in the gap.
        gaps.append({
            "fromFrame": riser_at,
            "toFrame": at,
            "silentFromFrame": at - gap_frames,
        })

    return "ambience.wav", stings, bed_name, gaps


def _normalized_narration(audio_path: Path, target_db: float = -1.5) -> str:
    """
    Write a peak-normalized copy of the narration next to the original.

    Edge TTS output can sit around -20 dBFS peak, which is far too quiet once
    an ambience bed is underneath it. A flat gain (not loudnorm) keeps the file
    sample-aligned with word_timings.json, so captions stay in sync.

    Written as WAV, deliberately. Re-encoding to MP3 prepends ~46ms of encoder
    delay (visible as a non-zero start_time), which drags the narration late
    against the captions and the stings. WAV has no such padding.

    Returns the filename to reference; falls back to the original on failure.
    """
    from pipeline.horror_audio import HorrorAudioError, normalize_peak

    out = audio_path.with_name("voice_mix.wav")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(audio_path),
             "-ac", "2", "-ar", "48000", str(out)],
            capture_output=True, text=True, check=True,
        )
        normalize_peak(out, target_db)
        return out.name
    except (OSError, subprocess.CalledProcessError, HorrorAudioError) as e:
        log.warning("Narration normalize failed (%s) — using the raw voice track", e)
        return audio_path.name


def build_props(
    scenes: list[dict],
    audio_src: str | None,
    word_timings_path: str | None,
    scene_durations: list[float],
    cfg,
    niche: dict,
    title: str = "",
    seed: int = 0,
    public_dir: Path | None = None,
    image_names: list[str] | None = None,
    image_names_b: list[str | None] | None = None,
) -> dict:
    """Build the props payload the ScaryStory composition validates with zod."""
    video_cfg = getattr(cfg, "video", {}) if cfg else {}
    fps = int(video_cfg.get("fps", _DEFAULT_FPS))
    height = int((video_cfg.get("resolution") or _DEFAULT_RES)[1])
    # A niche may override caption styling without disturbing the shared
    # defaults — the same numbers feed ffmpeg_assembler's ASS builder for every
    # other niche, and a vertical horror short wants much larger type than a
    # mythology video does.
    caption_cfg = {
        **(video_cfg.get("caption_style", {}) or {}),
        **((niche or {}).get("caption_style", {}) or {}),
    }
    remotion_cfg = (niche or {}).get("remotion", {}) or {}

    if len(scene_durations) != len(scenes):
        raise RemotionRenderError(
            f"scene/duration mismatch: {len(scenes)} scenes, "
            f"{len(scene_durations)} durations"
        )

    target = round(sum(scene_durations) * fps)
    padded, narrative, lead_in, total, transition = plan_frames(
        scene_durations,
        fps,
        int(remotion_cfg.get("transition_frames", _DEFAULT_TRANSITION_FRAMES)),
        target_total_frames=target,
    )

    # settings.json states caption fontsize against a 480p reference, same as
    # the ASS builder does, so both renderers read one number.
    font_px = max(int(caption_cfg.get("fontsize", 14) * height / 480), 12)
    captions = build_caption_chunks(
        word_timings_path, fps, int(caption_cfg.get("chunk_size", 3)), total
    )
    # Flat list of word start frames, used to keep every cut off a word.
    word_starts = [w["fromFrame"] for chunk in captions for w in chunk["words"]]

    out_scenes = []
    beat_start = 0
    for i, scene in enumerate(scenes):
        image_a = image_names[i] if image_names and i < len(image_names) else None
        image_b = image_names_b[i] if image_names_b and i < len(image_names_b) else None

        shots = plan_shots(
            narrative_frames=narrative[i],
            beat_start=beat_start,
            fps=fps,
            word_starts=word_starts,
            image_a=image_a,
            image_b=image_b,
            index=i,
        )
        assert sum(s["durationInFrames"] for s in shots) == narrative[i], \
            f"shot planning broke the beat length invariant on scene {i}"

        # The lead-in frames are the cross-fade into this beat. The backdrop has
        # to cover them, so they go onto the first shot — the picture is already
        # on screen while it fades up, which is the point of a cross-fade.
        shots[0]["durationInFrames"] += lead_in[i]

        out_scenes.append({
            "template": scene.get("visual", "line"),
            "durationInFrames": padded[i],
            "narrativeDurationInFrames": narrative[i],
            "leadInFrames": lead_in[i],
            "imageSrc": image_a,
            "shots": shots,
            "accent": scene.get("accent", "") or "",
            "repeat": int(scene.get("repeat", 1) or 1),
        })
        beat_start += narrative[i]

    audio_cfg = (niche or {}).get("horror_audio", {}) or {}
    ambience_src: str | None = None
    stings: list[dict] = []
    ambience_gaps: list[dict] = []
    if public_dir is not None:
        ambience_src, stings, bed, ambience_gaps = build_soundtrack(
            scenes, narrative, public_dir, total, fps, seed, audio_cfg,
        )
        if bed:
            log.info("Soundtrack: bed=%s stings=%d gaps=%d",
                     bed, len(stings), len(ambience_gaps))

    return {
        "schemaVersion": 2,
        "title": title,
        "audioSrc": audio_src,
        "narrationVolume": float(audio_cfg.get("narration_volume", 1.0)),
        "ambienceSrc": ambience_src,
        "ambienceVolume": float(audio_cfg.get("ambience_volume", 0.22)),
        "ambienceGaps": ambience_gaps,
        "stings": stings,
        "audioDurationInFrames": total,
        "transitionFrames": transition,
        "seed": seed,
        "endSubline": remotion_cfg.get("end_subline", "a new story every night"),
        "scenes": out_scenes,
        "captions": captions,
        "captionStyle": {
            "fontSizePx": font_px,
            # Captions are anchored from the top rather than the bottom: the
            # bottom ~15% of a vertical video is covered by the platform's own
            # UI (caption text, handle, action rail), and that is exactly where
            # the old 5% bottom margin put them.
            "anchorYPercent": float(caption_cfg.get("anchor_y_percent", 62)),
            "activeColor": "#f4ece0",
            "idleColor": "#9c948a",
        },
    }


# ── Preflight ─────────────────────────────────────────────────────────────────

def preflight(niche: dict) -> tuple[Path, Path, list[str]]:
    """
    Verify the Remotion project can actually run.

    Returns (project_dir, props_dir_hint, launcher_argv). Every failure message
    names the command that fixes it — this runs on someone else's machine after
    a fresh clone more often than it runs on yours.
    """
    remotion_cfg = (niche or {}).get("remotion", {}) or {}
    project_dir = (_REPO_ROOT / remotion_cfg.get("project_dir", _DEFAULT_PROJECT_DIR)).resolve()

    cache_key = str(project_dir)
    if cache_key in _PREFLIGHT_CACHE:
        return _PREFLIGHT_CACHE[cache_key]

    if not project_dir.is_dir():
        raise RemotionRenderError(
            f"Remotion project not found at {project_dir}.\n"
            f"Expected the '{remotion_cfg.get('project_dir', _DEFAULT_PROJECT_DIR)}' "
            f"folder at the repo root."
        )

    entry = project_dir / remotion_cfg.get("entry_point", _DEFAULT_ENTRY)
    if not entry.is_file():
        raise RemotionRenderError(f"Remotion entry point not found: {entry}")

    if not (project_dir / "node_modules" / "remotion").is_dir():
        raise RemotionRenderError(
            "Remotion dependencies are not installed. Run:\n"
            f"    cd {project_dir}\n"
            "    npm ci"
        )

    # Prefer the project's own bin shim: it cannot resolve to a different
    # Remotion version and skips npx's resolution overhead.
    bin_dir = project_dir / "node_modules" / ".bin"
    candidates = (
        [bin_dir / "remotion.cmd", bin_dir / "remotion.exe"]
        if os.name == "nt" else [bin_dir / "remotion"]
    )
    launcher: list[str] | None = next(
        ([str(c)] for c in candidates if c.is_file()), None
    )

    if launcher is None:
        npx = shutil.which("npx.cmd" if os.name == "nt" else "npx")
        if not npx:
            raise RemotionRenderError(
                "Neither the local remotion binary nor npx was found.\n"
                "Install Node.js 20+ from https://nodejs.org, reopen the shell, then:\n"
                f"    cd {project_dir}\n"
                "    npm ci"
            )
        launcher = [npx, "remotion"]

    result = (project_dir, entry, launcher)
    _PREFLIGHT_CACHE[cache_key] = result
    return result


# ── Output verification ───────────────────────────────────────────────────────

def limit_audio(video_path: str, ceiling_db: float = -2.0) -> None:
    """
    Re-mux with a true-peak limiter on the audio, in place.

    Three layers land on the same timeline — narration, ambience bed and
    stingers — and when a sting fires under a loud word the sum runs past
    0 dBFS and clips. Rather than dropping every layer's gain (which would make
    the whole video quiet to protect against a rare collision), catch the peaks
    with a limiter and keep the loudness.

    Video is stream-copied, so this costs seconds, not another render.

    The ceiling is set below the target because the AAC encode that follows the
    limiter overshoots it — limiting to -1.0 measured back at 0.0 dBFS. The
    result is re-measured and a warning is logged if it still lands hot.
    """
    src = Path(video_path)
    tmp = src.with_name(src.stem + ".limited.mp4")
    limit = 10 ** (ceiling_db / 20)

    try:
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(src),
             "-c:v", "copy",
             "-af", f"alimiter=limit={limit:.4f}:attack=5:release=60:level=false",
             "-c:a", "aac", "-b:a", "192k",
             # Drop container tags. Remotion stamps a "Made with Remotion"
             # comment and ffmpeg adds an encoder string; neither belongs on a
             # published video.
             "-map_metadata", "-1",
             "-movflags", "+faststart", str(tmp)],
            capture_output=True, text=True, check=True,
        )
        tmp.replace(src)
    except (subprocess.CalledProcessError, OSError) as e:
        log.warning("Audio limiter failed (%s) — keeping the unlimited mix", e)
        tmp.unlink(missing_ok=True)
        return

    from pipeline.horror_audio import _peak_db
    peak = _peak_db(src)
    if peak is not None and peak > -0.5:
        log.warning("Audio still peaks at %.1f dBFS after limiting — check the mix", peak)


def _probe(path: str) -> dict:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
        capture_output=True, text=True, check=True,
    )
    return json.loads(proc.stdout)


def _verify_output(output_path: str, expected_dur: float | None) -> None:
    out = Path(output_path)
    if not out.is_file():
        raise RemotionRenderError(f"Remotion reported success but {out} does not exist")
    if out.stat().st_size < 100_000:
        raise RemotionRenderError(f"Remotion output is suspiciously small: {out.stat().st_size} bytes")

    try:
        info = _probe(output_path)
    except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError) as e:
        log.warning("Remotion: could not ffprobe the output (%s) — skipping verification", e)
        return

    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if video is None:
        raise RemotionRenderError("Remotion output has no video stream")
    if video.get("codec_name") != "h264":
        raise RemotionRenderError(f"Expected h264, got {video.get('codec_name')}")
    if (video.get("width"), video.get("height")) != _DEFAULT_RES:
        raise RemotionRenderError(
            f"Expected {_DEFAULT_RES[0]}x{_DEFAULT_RES[1]}, "
            f"got {video.get('width')}x{video.get('height')}"
        )
    if audio is None:
        log.warning("Remotion output has no audio stream — narration missing?")
    elif audio.get("codec_name") != "aac":
        log.warning("Remotion audio codec is %s, expected aac", audio.get("codec_name"))

    if expected_dur:
        try:
            actual = float(info["format"]["duration"])
            if abs(actual - expected_dur) > 0.5:
                log.warning(
                    "Remotion output is %.2fs but the narration is %.2fs (drift %.2fs)",
                    actual, expected_dur, actual - expected_dur,
                )
        except (KeyError, ValueError):
            pass


# ── Entry point ───────────────────────────────────────────────────────────────

def render_with_remotion(
    scene_images: list[str] | None,
    audio_path: str,
    output_path: str,
    scenes: list[dict] | None = None,
    cfg=None,
    scene_duration: float | None = None,
    word_timings_path: str | None = None,
    scene_durations: list[float] | None = None,
    *,
    niche: dict | None = None,
    title: str = "",
    seed: int = 0,
) -> str:
    """
    Render a procedural video with Remotion.

    Positional signature matches `ffmpeg_assembler.assemble_from_images` exactly
    so the two are interchangeable behind `renderer_dispatch.get_assembler`.
    `scene_images` is accepted and ignored — procedural niches have none.
    """
    if not scenes:
        raise RemotionRenderError("render_with_remotion: no scenes")
    if not scene_durations:
        raise RemotionRenderError("render_with_remotion: no scene durations")
    if scene_images:
        log.info("Remotion: %d scene images will be used as backdrops", len(scene_images))

    niche = niche or {}
    remotion_cfg = niche.get("remotion", {}) or {}
    project_dir, entry, launcher = preflight(niche)

    audio = Path(audio_path).resolve()
    if not audio.is_file():
        raise RemotionRenderError(f"Narration audio not found: {audio}")

    # The audio directory doubles as the Remotion public dir. It already holds
    # exactly voice.mp3 + word_timings.json, is per-run isolated by slug, and is
    # already gitignored — so nothing is copied and nothing needs cleaning up.
    public_dir = audio.parent

    # Remotion can only reach files under --public-dir, and that is the audio
    # directory. Copy the scene images in beside the narration rather than
    # widening the served directory to the whole output tree.
    def _publish(src: str | None, name: str) -> str | None:
        """Copy one image into the public dir, returning the name to reference."""
        if not src:
            return None
        src_img = Path(src)
        if not src_img.is_file():
            log.warning("Scene image missing, rendering that shot bare: %s", src_img)
            return None
        dest = public_dir / f"{name}{src_img.suffix.lower()}"
        if src_img.resolve() != dest.resolve():
            shutil.copyfile(src_img, dest)
        return dest.name

    image_names: list[str | None] = [
        _publish(img, f"scene_{i:02d}") for i, img in enumerate(scene_images or [])
    ]
    # The second image of a beat, set by the image stage when the script asked
    # for one. Absent for most beats, and absent entirely for niches that don't
    # generate images.
    image_names_b: list[str | None] = [
        _publish(scene.get("_image_b"), f"scene_{i:02d}_b")
        for i, scene in enumerate(scenes)
    ]

    props = build_props(
        scenes=scenes,
        audio_src=_normalized_narration(audio),
        word_timings_path=word_timings_path,
        scene_durations=scene_durations,
        cfg=cfg,
        niche=niche,
        title=title,
        seed=seed,
        public_dir=public_dir,
        image_names=image_names,
        image_names_b=image_names_b,
    )

    # Props go next to the script manifest, NOT in the public dir — anything in
    # there gets bundled and served.
    out = Path(output_path).resolve()
    props_path = out.parent / f"{out.stem}.props.json"
    props_path.parent.mkdir(parents=True, exist_ok=True)
    props_path.write_text(json.dumps(props, indent=2, ensure_ascii=False), encoding="utf-8")

    cmd = [
        *launcher,
        "render",
        str(entry.relative_to(project_dir)).replace("\\", "/"),
        remotion_cfg.get("composition_id", _DEFAULT_COMPOSITION),
        str(out),
        f"--props={props_path}",
        f"--public-dir={public_dir}",
        "--codec=h264",
        f"--crf={remotion_cfg.get('crf', _DEFAULT_CRF)}",
        "--log=info",
    ]
    if remotion_cfg.get("concurrency"):
        cmd.append(f"--concurrency={remotion_cfg['concurrency']}")

    timeout = int(remotion_cfg.get("timeout_sec", _DEFAULT_TIMEOUT_SEC))
    log.info(
        "Remotion render: %d scenes, %d frames, comp=%s",
        len(scenes), props["audioDurationInFrames"],
        remotion_cfg.get("composition_id", _DEFAULT_COMPOSITION),
    )
    log.debug("Remotion cmd: %s", " ".join(cmd))

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(project_dir),           # --props resolves against cwd
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env={**os.environ, "FORCE_COLOR": "0", "CI": "1"},
        )
    except subprocess.TimeoutExpired as e:
        raise RemotionRenderError(
            f"Remotion render timed out after {timeout}s.\n"
            "A cold bundle cache plus the first Chrome Headless Shell download can "
            "take several minutes — raise niche.remotion.timeout_sec, or warm it "
            f"with:  cd {project_dir} && npx remotion browser ensure\n"
            f"Last output:\n{(e.stdout or '')[-2000:]}\n{(e.stderr or '')[-2000:]}"
        ) from e

    if proc.returncode != 0:
        raise RemotionRenderError(
            f"Remotion render failed (exit {proc.returncode}).\n"
            f"Command: {' '.join(cmd)}\n"
            f"cwd: {project_dir}\n"
            f"--- stderr ---\n{(proc.stderr or '')[-4000:]}\n"
            f"--- stdout ---\n{(proc.stdout or '')[-4000:]}"
        )

    limit_audio(str(out))

    fps = int((getattr(cfg, "video", {}) or {}).get("fps", _DEFAULT_FPS))
    _verify_output(str(out), props["audioDurationInFrames"] / fps)

    if not remotion_cfg.get("keep_artifacts", False):
        props_path.unlink(missing_ok=True)

    log.info("Remotion render complete: %s", out)
    return str(out)
