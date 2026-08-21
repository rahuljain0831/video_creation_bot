"""
Phase 3 — TTS synthesis.
Provider priority: edge_tts (always available) → piper → kokoro
Returns path to .mp3/.wav file and duration in seconds.
Edge TTS also saves word_timings.json alongside audio for karaoke captions.
"""
import asyncio
import json
import logging
import os
import random
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

log = logging.getLogger(__name__)

# Word timings are stored in 100-nanosecond ticks throughout the pipeline —
# the unit Edge TTS reports — and every consumer of word_timings.json
# assumes it.
_TICKS_PER_SECOND = 10_000_000


# ── edge_tts ──────────────────────────────────────────────────────────────────

async def _edge_tts_async(
    text: str,
    voice: str,
    out_path: str,
    rate: str = "+0%",
    volume: str = "+0%",
    pitch: str = "+0Hz",
) -> list[dict]:
    """
    Stream Edge TTS output, capturing audio bytes and word boundary events.
    Returns list of word timing dicts: {text, offset, duration} in 100-ns units.
    """
    import edge_tts
    communicate = edge_tts.Communicate(
        text, voice,
        rate=rate, volume=volume, pitch=pitch,
        boundary="WordBoundary",
    )

    word_boundaries: list[dict] = []
    audio_chunks: list[bytes] = []

    async for event in communicate.stream():
        if event["type"] == "audio":
            audio_chunks.append(event["data"])
        elif event["type"] == "WordBoundary":
            word_boundaries.append({
                "text": event["text"],
                "offset": event["offset"],    # 100-nanosecond units
                "duration": event["duration"],
            })

    with open(out_path, "wb") as f:
        for chunk in audio_chunks:
            f.write(chunk)

    return word_boundaries


def _synthesize_edge(
    text: str,
    voice: str,
    out_path: str,
    rate: str = "+0%",
    volume: str = "+0%",
    pitch: str = "+0Hz",
) -> tuple[str, list[dict]]:
    word_boundaries = asyncio.run(_edge_tts_async(text, voice, out_path, rate=rate, volume=volume, pitch=pitch))
    return out_path, word_boundaries


# ── elevenlabs ────────────────────────────────────────────────────────────────
#
# Dormant unless ELEVENLABS_API_KEY is set, which it is not by default: the free
# 10k characters are web-UI only and API access needs a paid plan. The code is
# here because the endpoint shape is known and the integration is otherwise a
# config change — if a plan is ever taken, set the key and put "elevenlabs" at
# the front of provider_priority.

_ELEVEN_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps"


def _synthesize_elevenlabs(
    text: str,
    voice_id: str,
    out_path: str,
    model_id: str = "eleven_multilingual_v2",
    stability: float = 0.4,
    style: float = 0.6,
) -> tuple[str, list[dict]]:
    """
    ElevenLabs TTS, returning the same (path, word_boundaries) contract as Edge.

    The API reports *character* alignment, not word boundaries, so the
    characters are grouped on whitespace here: a word starts at its first
    character's start time and ends at its last character's end time. Everything
    downstream — captions, shot cuts, the riser — reads word_timings.json in
    100-nanosecond ticks, so that shape is what comes back.
    """
    import base64

    import requests

    api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")

    resp = requests.post(
        _ELEVEN_URL.format(voice_id=voice_id),
        headers={"xi-api-key": api_key, "Content-Type": "application/json"},
        json={
            "text": text,
            "model_id": model_id,
            "voice_settings": {"stability": stability, "style": style,
                               "similarity_boost": 0.75},
        },
        params={"output_format": "mp3_44100_128"},
        timeout=180,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"elevenlabs HTTP {resp.status_code}: {resp.text[:300]}")

    payload = resp.json()
    Path(out_path).write_bytes(base64.b64decode(payload["audio_base64"]))

    alignment = payload.get("alignment") or {}
    chars = alignment.get("characters") or []
    starts = alignment.get("character_start_times_seconds") or []
    ends = alignment.get("character_end_times_seconds") or []

    boundaries: list[dict] = []
    word, start, prev_end = "", None, 0.0
    for ch, cs, ce in zip(chars, starts, ends):
        if ch.isspace():
            if word:
                boundaries.append({
                    "text": word,
                    "offset": int(start * _TICKS_PER_SECOND),
                    "duration": int((prev_end - start) * _TICKS_PER_SECOND),
                })
                word, start = "", None
            continue
        if not word:
            start = cs
        word += ch
        prev_end = ce
    if word and start is not None:
        boundaries.append({
            "text": word,
            "offset": int(start * _TICKS_PER_SECOND),
            "duration": int((prev_end - start) * _TICKS_PER_SECOND),
        })

    log.info("elevenlabs: %d characters, %d words", len(text), len(boundaries))
    return out_path, boundaries


# ── piper ─────────────────────────────────────────────────────────────────────

def _synthesize_piper(text: str, voice_model: str, out_path: str) -> str:
    """
    Calls piper CLI: echo text | piper --model <voice> --output_file <path>
    Requires piper binary on PATH and model file downloaded.
    """
    result = subprocess.run(
        ["piper", "--model", voice_model, "--output_file", out_path],
        input=text.encode(),
        capture_output=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"piper failed: {result.stderr.decode()[:200]}")
    return out_path


# ── kokoro ────────────────────────────────────────────────────────────────────

def _synthesize_kokoro(text: str, out_path: str) -> str:
    from kokoro_onnx import Kokoro
    import soundfile as sf
    import numpy as np

    kokoro = Kokoro("kokoro-v0_19.onnx", "voices.json")
    samples, sample_rate = kokoro.create(text, voice="af", speed=1.0, lang="en-us")
    sf.write(out_path, samples, sample_rate)
    return out_path


# ── duration helper ───────────────────────────────────────────────────────────

def _get_duration(path: str) -> float:
    """Get audio duration using ffprobe, fallback to wave module."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True, text=True, timeout=15,
        )
        return float(result.stdout.strip())
    except Exception:
        pass

    # wave fallback (wav only)
    if path.endswith(".wav"):
        try:
            with wave.open(path) as wf:
                return wf.getnframes() / wf.getframerate()
        except Exception:
            pass

    return 0.0


# ── public interface ──────────────────────────────────────────────────────────

def _resolve_tts_config(cfg, niche: dict | None) -> dict:
    """
    Merge global TTS config with niche-level overrides.
    Priority (highest first): niche["tts"] > global cfg.tts > hardcoded defaults.
    """
    defaults = {
        # elevenlabs is first but skips itself without ELEVENLABS_API_KEY, so
        # the effective default is unchanged: edge_tts.
        "provider_priority": ["elevenlabs", "edge_tts", "piper", "kokoro"],
        "voice_edge": "en-US-GuyNeural",
        "voice_piper": "en_US-lessac-medium",
        # "George" — the stock ElevenLabs narration voice. Only used if a key
        # is present.
        "voice_elevenlabs": "JBFqnCBsd6RMkjVDRZzb",
        "elevenlabs_model": "eleven_multilingual_v2",
        "rate": "+0%",
        "volume": "+0%",
        "pitch": "+0Hz",
        # Horror voice treatment. Only meaningful on the per-scene path, since
        # the profile is chosen from the beat's template.
        "voice_fx": False,
    }
    global_tts = (cfg.tts if cfg is not None else {}) or {}
    niche_tts = (niche.get("tts") if niche else None) or {}
    return {**defaults, **global_tts, **niche_tts}


def synthesize(
    text: str,
    output_dir: str,
    video_id: int,
    cfg=None,
    niche: dict | None = None,
) -> tuple[str, float]:
    """
    Synthesize speech for `text`. Tries providers in priority order.
    Returns (file_path, duration_seconds).

    niche: optional niche dict — if it has a "tts" key those values override
           global cfg.tts settings (voice, rate, pitch, volume).

    When edge_tts is used, also writes word_timings.json to output_dir
    containing per-word offset/duration data (100-ns units) for karaoke captions.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    tts_cfg = _resolve_tts_config(cfg, niche)
    providers = tts_cfg["provider_priority"]
    # Pick random voice from pool if available, else use single voice
    voice_pool = tts_cfg.get("voice_pool_edge")
    if voice_pool:
        voice_edge = random.choice(voice_pool)
    else:
        voice_edge = tts_cfg["voice_edge"]
    voice_piper = tts_cfg["voice_piper"]
    rate = tts_cfg["rate"]
    volume = tts_cfg["volume"]
    pitch = tts_cfg["pitch"]

    log.info(
        "TTS config: niche=%s voice=%s rate=%s volume=%s pitch=%s",
        niche.get("id", "?") if niche else "none", voice_edge, rate, volume, pitch,
    )

    last_error = None
    for provider in providers:
        word_boundaries: list[dict] = []
        try:
            if provider == "elevenlabs":
                if not os.getenv("ELEVENLABS_API_KEY", "").strip():
                    continue          # dormant without a key; not a failure
                out_path = str(out_dir / "voice.mp3")
                log.info("TTS provider=elevenlabs video_id=%d", video_id)
                _, word_boundaries = _synthesize_elevenlabs(
                    text, tts_cfg["voice_elevenlabs"], out_path,
                    model_id=tts_cfg["elevenlabs_model"],
                )

            elif provider == "edge_tts":
                out_path = str(out_dir / "voice.mp3")
                log.info("TTS provider=edge_tts video_id=%d", video_id)
                _, word_boundaries = _synthesize_edge(
                    text, voice_edge, out_path,
                    rate=rate, volume=volume, pitch=pitch,
                )

            elif provider == "piper":
                out_path = str(out_dir / "voice.wav")
                log.info("TTS provider=piper video_id=%d", video_id)
                _synthesize_piper(text, voice_piper, out_path)

            elif provider == "kokoro":
                out_path = str(out_dir / "voice.wav")
                log.info("TTS provider=kokoro video_id=%d", video_id)
                _synthesize_kokoro(text, out_path)

            else:
                log.warning("Unknown TTS provider: %s", provider)
                continue

            # Karaoke captions need these; only the providers that report
            # boundaries produce them, and a run without them still renders
            # (uncaptioned) rather than failing.
            if word_boundaries:
                timings_path = str(out_dir / "word_timings.json")
                with open(timings_path, "w", encoding="utf-8") as f:
                    json.dump(word_boundaries, f)
                log.info("Word timings saved: %s (%d words)",
                         timings_path, len(word_boundaries))

            duration = _get_duration(out_path)
            log.info("TTS done: %s (%.2fs)", out_path, duration)
            return out_path, duration

        except Exception as e:
            log.warning("TTS provider %s failed: %s — trying next", provider, e)
            last_error = e

    raise RuntimeError(f"All TTS providers failed. Last error: {last_error}")


# ── per-scene synthesis ───────────────────────────────────────────────────────
#
# Synthesizing the whole video as one string gives every beat the same delivery,
# which is why the narration sounded flat no matter which voice was picked. One
# call per beat lets the scare be slower and lower than the setup, and buys two
# things for free:
#
#   * a real silence before the scare, so the riser drops into quiet rather than
#     into the next sentence;
#   * exact scene boundaries. scene_timing no longer has to infer where one
#     scene ends by matching words against the script.


# Delivery per template, applied on top of the niche's own rate/pitch.
# Values are deltas in the units Edge TTS takes.
#
# Kept modest because they compound: scary_stories already narrates at -15%, so
# a -18 delta here lands at -33% and the line drags rather than builds. These
# are the amounts that read as a change of delivery without sounding slowed
# down.
#
# What makes a read sound like *storytelling* is contrast, not slowness. `line`
# is therefore nudged slightly faster than the niche baseline so the beats that
# slow down have something to be slower than; a script where every beat sits at
# the same -15% reads as flat no matter how low the pitch goes.
_PROSODY = {
    "hook":   {"rate": -4,  "pitch": -2},
    "line":   {"rate": +2,  "pitch": 0},
    "impact": {"rate": -8,  "pitch": -4},
    "reveal": {"rate": -6,  "pitch": -4},
    "scare":  {"rate": -12, "pitch": -7},
    "end":    {"rate": -7,  "pitch": -5},
}

# Silence inserted *before* a beat, in seconds. The pause ahead of the scare is
# the setup for the riser; the one before the closing card lets the last line
# land on its own.
#
# Every template gets one. A storyteller stops between sentences; TTS does not,
# and beats concatenated with no gap are the other half of why the read sounded
# like a person reciting rather than telling. Across ~11 beats this adds about
# three seconds, which the niche's 60-75s window absorbs.
_LEAD_SILENCE = {
    "hook":   0.0,
    "line":   0.22,
    "impact": 0.50,
    "reveal": 0.45,
    "scare":  0.55,
    "end":    0.40,
}


def _shift(base: str, delta: int, unit: str) -> str:
    """Apply a signed delta to an Edge TTS '+N%' / '-NHz' style value."""
    try:
        current = int(str(base).replace(unit, "").replace("+", "") or 0)
    except ValueError:
        current = 0
    return f"{current + delta:+d}{unit}"


def _silence_wav(path: Path, seconds: float) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"anullsrc=r=48000:cl=mono", "-t", f"{seconds:.3f}",
         "-c:a", "pcm_s16le", str(path)],
        capture_output=True, check=True,
    )


def _to_wav(src: Path, dest: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(src),
         "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(dest)],
        capture_output=True, check=True,
    )


def synthesize_scenes(
    scenes: list[dict],
    output_dir: str,
    video_id: int,
    cfg=None,
    niche: dict | None = None,
) -> tuple[str, float, list[float]]:
    """
    Synthesize one narration track, one beat at a time.

    Returns (audio_path, total_duration, per_scene_durations). The per-scene
    durations are measured, not estimated, and include each beat's lead silence.

    Output is a WAV rather than an MP3 on purpose: the concatenation happens in
    the sample domain, and re-encoding to MP3 would prepend ~46ms of encoder
    delay that drags the narration late against captions and stings.

    Falls back to whole-text `synthesize()` if anything here fails — a flat
    delivery is much better than no video.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    tts_cfg = _resolve_tts_config(cfg, niche)
    voice_pool = tts_cfg.get("voice_pool_edge")
    voice = random.choice(voice_pool) if voice_pool else tts_cfg["voice_edge"]
    prosody_cfg = {**_PROSODY, **(tts_cfg.get("prosody") or {})}

    # Off by default so every other niche is untouched; scary_stories turns it
    # on. Applied per beat rather than to the finished track because the scare
    # wants a heavier treatment than the lines leading up to it, and that
    # distinction is gone once the beats are concatenated.
    voice_fx = bool(tts_cfg.get("voice_fx", False))

    use_eleven = (
        "elevenlabs" in tts_cfg["provider_priority"]
        and bool(os.getenv("ELEVENLABS_API_KEY", "").strip())
    )

    log.info("TTS per-scene: engine=%s voice=%s scenes=%d",
             "elevenlabs" if use_eleven else "edge_tts",
             tts_cfg["voice_elevenlabs"] if use_eleven else voice, len(scenes))

    parts: list[Path] = []
    timings: list[dict] = []
    durations: list[float] = []
    clock_ticks = 0

    work = out_dir / "_scenes"
    work.mkdir(exist_ok=True)

    try:
        for i, scene in enumerate(scenes):
            template = scene.get("visual", "line")
            shift = prosody_cfg.get(template, {})
            rate = _shift(tts_cfg["rate"], int(shift.get("rate", 0)), "%")
            pitch = _shift(tts_cfg["pitch"], int(shift.get("pitch", 0)), "Hz")

            lead = float(_LEAD_SILENCE.get(template, 0.0))
            if lead:
                pad = work / f"pad_{i:02d}.wav"
                _silence_wav(pad, lead)
                parts.append(pad)
                clock_ticks += int(lead * _TICKS_PER_SECOND)

            mp3 = work / f"scene_{i:02d}.mp3"
            if use_eleven:
                # Prosody comes from the model's own delivery here, not from
                # rate/pitch knobs: stability drops on the scare so the read
                # varies more, and rises on plain lines so they stay level.
                _, boundaries = _synthesize_elevenlabs(
                    scene["narration"], tts_cfg["voice_elevenlabs"], str(mp3),
                    model_id=tts_cfg["elevenlabs_model"],
                    stability=0.25 if template == "scare" else 0.45,
                )
            else:
                _, boundaries = _synthesize_edge(
                    scene["narration"], voice, str(mp3),
                    rate=rate, volume=tts_cfg["volume"], pitch=pitch,
                )

            wav = work / f"scene_{i:02d}.wav"
            _to_wav(mp3, wav)

            # Before the duration is measured, but after the boundaries were
            # captured — the treatment is duration-locked, so both stay valid.
            if voice_fx:
                from pipeline.horror_audio import apply_voice_fx
                apply_voice_fx(wav, profile=template)

            spoken = _get_duration(str(wav))
            if spoken <= 0:
                raise RuntimeError(f"scene {i} produced no audio")

            for b in boundaries:
                timings.append({
                    "text": b["text"],
                    "offset": b["offset"] + clock_ticks,
                    "duration": b["duration"],
                })

            parts.append(wav)
            clock_ticks += int(spoken * _TICKS_PER_SECOND)
            durations.append(lead + spoken)

        # Concat in the sample domain. The demuxer needs a list file, and every
        # part is already the same format, so no re-encode happens at the seams.
        listing = work / "parts.txt"
        listing.write_text(
            "".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8",
        )
        out_path = out_dir / "voice.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
             "-i", "parts.txt", "-c", "copy", str(out_path.resolve())],
            cwd=str(work), capture_output=True, check=True,
        )

        (out_dir / "word_timings.json").write_text(
            json.dumps(timings), encoding="utf-8",
        )

        total = _get_duration(str(out_path))
        drift = abs(total - sum(durations))
        if drift > 0.15:
            log.warning("TTS per-scene: concat is %.2fs but parts sum to %.2fs",
                        total, sum(durations))

        log.info("TTS per-scene done: %s (%.2fs, %d words, %d beats)",
                 out_path, total, len(timings), len(durations))
        return str(out_path), total, durations

    except Exception as e:
        log.warning("Per-scene TTS failed (%s) — falling back to one-shot synthesis", e)
        text = "  ".join(s["narration"] for s in scenes)
        path, total = synthesize(text, output_dir, video_id, cfg=cfg, niche=niche)
        return path, total, []

    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    text = sys.argv[1] if len(sys.argv) > 1 else "The strongest steel is forged in the hottest fire."
    out_dir = sys.argv[2] if len(sys.argv) > 2 else "data/audio"
    path, dur = synthesize(text, out_dir, video_id=0)
    print(f"Output: {path}  Duration: {dur:.2f}s")
