"""
Derive real per-scene durations from Edge TTS's word_timings.json,
instead of splitting total audio duration equally across scenes.

Falls back to equal split if word_timings.json is missing or the word
count doesn't line up with the scenes (e.g. Piper/Kokoro ran instead
of Edge TTS, which don't produce word-level timing).
"""

import json
import logging
import re

log = logging.getLogger(__name__)


def _equal_split(scenes: list[dict], total_audio_dur: float, min_scene_dur: float = 3.0) -> list[float]:
    n = len(scenes)
    return [max(total_audio_dur / n, min_scene_dur)] * n


def _norm(text: str) -> str:
    """Letters and digits only, lowercased — punctuation and spacing removed."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _align_words_to_scenes(scenes: list[dict], word_data: list[dict]) -> list[list[dict]] | None:
    """
    Assign timed words to scenes by matching text, not by counting tokens.

    Counting was fragile in exactly one way, and it fired often: Edge TTS emits
    "3:14 AM" as a single word boundary while `narration.split()` sees two
    tokens. One such phrase anywhere in the script made the counts disagree and
    threw away the timings for the *whole* video, falling back to an equal split
    where every scene gets an identical length regardless of what was said.

    Matching on normalised characters instead absorbs merges and splits alike:
    a scene consumes timed words until their combined text covers its narration.

    Returns one list of words per scene, or None if the streams diverge badly
    enough that the caller should fall back.
    """
    grouped: list[list[dict]] = []
    idx = 0

    for scene in scenes:
        target = _norm(scene["narration"])
        acc = ""
        words: list[dict] = []

        while idx < len(word_data) and len(acc) < len(target):
            acc += _norm(str(word_data[idx].get("text", "")))
            words.append(word_data[idx])
            idx += 1

        if not words:
            log.warning("scene_timing: ran out of timed words mid-script — equal split")
            return None

        # Overshoot of a character or two is normal (a merged token can carry
        # the start of the next scene). A large divergence means the streams are
        # not the same text at all, and the offsets would be meaningless.
        if len(acc) < len(target) * 0.6:
            log.warning(
                "scene_timing: timed text diverged from the script (%d vs %d chars) — equal split",
                len(acc), len(target),
            )
            return None

        grouped.append(words)

    return grouped


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

    grouped = _align_words_to_scenes(scenes, word_data)
    if grouped is None:
        return _equal_split(scenes, total_audio_dur, min_scene_dur)

    durations = []
    for words in grouped:
        start = words[0]["offset"] / 10_000_000
        end = (words[-1]["offset"] + words[-1]["duration"]) / 10_000_000
        durations.append(max(end - start, min_scene_dur))

    # Normalise so total matches actual audio length exactly (covers trailing silence)
    total = sum(durations)
    if total > 0:
        scale = total_audio_dur / total
        durations = [d * scale for d in durations]

    return durations
