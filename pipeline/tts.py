"""
Phase 3 — TTS synthesis.
Provider priority: edge_tts (always available) → piper → kokoro
Returns path to .mp3/.wav file and duration in seconds.
"""
import asyncio
import logging
import os
import subprocess
import tempfile
import wave
from pathlib import Path

log = logging.getLogger(__name__)


# ── edge_tts ──────────────────────────────────────────────────────────────────

async def _edge_tts_async(text: str, voice: str, out_path: str) -> None:
    import edge_tts
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(out_path)


def _synthesize_edge(text: str, voice: str, out_path: str) -> str:
    asyncio.run(_edge_tts_async(text, voice, out_path))
    return out_path


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

def synthesize(
    text: str,
    output_dir: str,
    video_id: int,
    cfg=None,
) -> tuple[str, float]:
    """
    Synthesize speech for `text`. Tries providers in priority order.
    Returns (file_path, duration_seconds).
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    providers = ["edge_tts", "piper", "kokoro"]
    voice_edge = "en-US-GuyNeural"
    voice_piper = "en_US-lessac-medium"

    if cfg is not None:
        providers = cfg.tts.get("provider_priority", providers)
        voice_edge = cfg.tts.get("voice_edge", voice_edge)
        voice_piper = cfg.tts.get("voice_piper", voice_piper)

    last_error = None
    for provider in providers:
        try:
            if provider == "edge_tts":
                out_path = str(out_dir / f"voice_{video_id}.mp3")
                synthesize_fn = lambda: _synthesize_edge(text, voice_edge, out_path)
            elif provider == "piper":
                out_path = str(out_dir / f"voice_{video_id}.wav")
                synthesize_fn = lambda: _synthesize_piper(text, voice_piper, out_path)
            elif provider == "kokoro":
                out_path = str(out_dir / f"voice_{video_id}.wav")
                synthesize_fn = lambda: _synthesize_kokoro(text, out_path)
            else:
                log.warning("Unknown TTS provider: %s", provider)
                continue

            log.info("TTS provider=%s video_id=%d", provider, video_id)
            synthesize_fn()
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
