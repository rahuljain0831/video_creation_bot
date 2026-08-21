"""
Scene boundaries derived from Edge TTS word timings.

The regression these guard against is quiet and expensive: a single token-count
disagreement used to discard the timings for the whole video and fall back to an
equal split, so every scene got an identical length and the picture stopped
changing when the sentence did. Nothing failed, the video just went flat.
"""

import json

import pytest

from pipeline.scene_timing import compute_scene_durations

TICKS = 10_000_000


def _timings(pairs):
    """[(text, start_sec, dur_sec)] → word_timings.json shape."""
    return [
        {"text": t, "offset": int(s * TICKS), "duration": int(d * TICKS)}
        for t, s, d in pairs
    ]


def _write(tmp_path, pairs):
    p = tmp_path / "word_timings.json"
    p.write_text(json.dumps(_timings(pairs)), encoding="utf-8")
    return str(p)


def test_uses_real_boundaries(tmp_path):
    scenes = [{"narration": "one two three."}, {"narration": "four five."}]
    path = _write(tmp_path, [
        ("one", 0.0, 0.4), ("two", 0.5, 0.4), ("three", 1.0, 0.5),
        ("four", 4.0, 0.4), ("five", 4.5, 0.5),
    ])
    durations = compute_scene_durations(scenes, path, 5.0, min_scene_dur=0.1)

    assert len(durations) == 2
    assert durations[0] > durations[1]
    assert sum(durations) == pytest.approx(5.0)


def test_merged_token_does_not_discard_the_timings(tmp_path):
    """
    Edge TTS emits "3:14 AM" as one word boundary; str.split() sees two tokens.

    The old count-based mapping treated that one-token drift as a mismatch and
    fell back to an equal split for the entire video.
    """
    scenes = [
        {"narration": "At 3:14 AM, the lock opened."},
        {"narration": "Nobody was there."},
    ]
    path = _write(tmp_path, [
        ("At", 0.0, 0.2), ("3:14 AM", 0.3, 0.8), ("the", 1.2, 0.2),
        ("lock", 1.5, 0.3), ("opened", 1.9, 0.5),
        ("Nobody", 5.0, 0.4), ("was", 5.5, 0.2), ("there", 5.8, 0.4),
    ])
    durations = compute_scene_durations(scenes, path, 7.0, min_scene_dur=0.1)

    assert sum(durations) == pytest.approx(7.0)
    # The tell of the old bug: every scene exactly the same length.
    assert durations[0] != pytest.approx(durations[1])


def test_falls_back_when_the_text_is_unrelated(tmp_path):
    """Timings for different audio must not be trusted."""
    scenes = [{"narration": "a completely different sentence here now"}]
    path = _write(tmp_path, [("zzz", 0.0, 0.2)])
    durations = compute_scene_durations(scenes, path, 6.0, min_scene_dur=0.1)

    assert durations == [6.0]


def test_falls_back_when_words_run_out(tmp_path):
    scenes = [{"narration": "one two three"}, {"narration": "four five six"}]
    path = _write(tmp_path, [("one", 0.0, 0.2), ("two", 0.3, 0.2), ("three", 0.6, 0.2)])
    durations = compute_scene_durations(scenes, path, 6.0, min_scene_dur=0.1)

    assert durations == [3.0, 3.0]


def test_missing_file_equal_splits(tmp_path):
    scenes = [{"narration": "a"}, {"narration": "b"}]
    durations = compute_scene_durations(scenes, str(tmp_path / "nope.json"), 8.0, min_scene_dur=0.1)

    assert durations == [4.0, 4.0]


def test_durations_always_sum_to_the_audio(tmp_path):
    """The renderer's frame plan is built on this."""
    scenes = [{"narration": f"line number {i} of the story"} for i in range(6)]
    pairs = []
    clock = 0.0
    for i in range(6):
        for tok in f"line number {i} of the story".split():
            pairs.append((tok, clock, 0.25))
            clock += 0.3
        clock += 0.6
    path = _write(tmp_path, pairs)

    durations = compute_scene_durations(scenes, path, 25.0, min_scene_dur=0.1)
    assert sum(durations) == pytest.approx(25.0)
