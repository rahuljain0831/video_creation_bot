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
import subprocess
import tempfile
import wave
from pathlib import Path

log = logging.getLogger(__name__)


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
        "provider_priority": ["edge_tts", "piper", "kokoro"],
        "voice_edge": "en-US-GuyNeural",
        "voice_piper": "en_US-lessac-medium",
        "rate": "+0%",
        "volume": "+0%",
        "pitch": "+0Hz",
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
        try:
            if provider == "edge_tts":
                out_path = str(out_dir / "voice.mp3")
                log.info("TTS provider=edge_tts video_id=%d", video_id)
                _, word_boundaries = _synthesize_edge(
                    text, voice_edge, out_path,
                    rate=rate, volume=volume, pitch=pitch,
                )
                # Save word timings for karaoke caption rendering
                if word_boundaries:
                    timings_path = str(out_dir / "word_timings.json")
                    with open(timings_path, "w", encoding="utf-8") as f:
                        json.dump(word_boundaries, f)
                    log.info("Word timings saved: %s (%d words)", timings_path, len(word_boundaries))

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

            duration = _get_duration(out_path)
            log.info("TTS done: %s (%.2fs)", out_path, duration)
            return out_path, duration

        except Exception as e:
            log.warning("TTS provider %s failed: %s — trying next", provider, e)
            last_error = e

    raise RuntimeError(f"All TTS providers failed. Last error: {last_error}")


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    text = sys.argv[1] if len(sys.argv) > 1 else "The strongest steel is forged in the hottest fire."
    out_dir = sys.argv[2] if len(sys.argv) > 2 else "data/audio"
    path, dur = synthesize(text, out_dir, video_id=0)
    print(f"Output: {path}  Duration: {dur:.2f}s")
