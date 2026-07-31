"""
Derive real per-scene durations from Edge TTS's word_timings.json,
instead of splitting total audio duration equally across scenes.

Falls back to equal split if word_timings.json is missing or the word
count doesn't line up with the scenes (e.g. Piper/Kokoro ran instead
of Edge TTS, which don't produce word-level timing).
"""

import json
import logging

log = logging.getLogger(__name__)


def _equal_split(scenes: list[dict], total_audio_dur: float, min_scene_dur: float = 3.0) -> list[float]:
    n = len(scenes)
    return [max(total_audio_dur / n, min_scene_dur)] * n


def compute_scene_durations(
    scenes: list[dict],
    word_timings_path: str | None,
    total_audio_dur: float,
    min_scene_dur: float = 2.0,
) -> list[float]:
    """
    Returns one duration (seconds) per scene, summing to ~total_audio_dur.
    Uses actual word-level timestamps when available; equal-split otherwise.
    """
    if not word_timings_path:
        return _equal_split(scenes, total_audio_dur, min_scene_dur)

    try:
        with open(word_timings_path) as f:
            word_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return _equal_split(scenes, total_audio_dur, min_scene_dur)

    if not word_data:
        return _equal_split(scenes, total_audio_dur, min_scene_dur)

    scene_word_counts = [len(s["narration"].split()) for s in scenes]
    if len(word_data) < sum(scene_word_counts):
        log.warning(
            "word_timings word count mismatch (%d timed vs %d expected) — equal split",
            len(word_data), sum(scene_word_counts),
        )
        return _equal_split(scenes, total_audio_dur, min_scene_dur)

    durations = []
    idx = 0
    for count in scene_word_counts:
        words = word_data[idx: idx + count]
        idx += count
        start = words[0]["offset"] / 10_000_000
        end = (words[-1]["offset"] + words[-1]["duration"]) / 10_000_000
        durations.append(max(end - start, min_scene_dur))

    # Normalise so total matches actual audio length exactly (covers trailing silence)
    total = sum(durations)
    if total > 0:
        scale = total_audio_dur / total
        durations = [d * scale for d in durations]

    return durations
