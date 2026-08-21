"""
Script-level rules for the cinematic_scary schema.

Both of these exist because the prompt asked for the behaviour and the model
ignored it. Asking is not enforcement; these are.
"""

import pytest

from pipeline.script_gen import _accent_is_spoken, _break_line_runs


# ── the accent must not repeat its own line ───────────────────────────────────

@pytest.mark.parametrize("accent,narration", [
    # The exact regression: the cold-open rule pushes a clock time into the
    # narration and the model reuses it as the accent, so the hook draws
    # "3:14 AM" over a caption reading "At 3:14 AM the".
    ("3:14 AM", "At 3:14 AM, the smart lock on the front door clicked open."),
    # Same duplication, separated by an article — a substring test misses this.
    ("FROM INSIDE", "But the notification log showed the door was unlocked from the inside."),
    ("KNOCK", "Three knocks. Always three."),
    ("don't look back", "She told me: do not look back."),
])
def test_accent_spoken_is_detected(accent, narration):
    assert _accent_is_spoken(accent, narration)


@pytest.mark.parametrize("accent,narration", [
    ("IT OPENED", "At 3:14 AM, the smart lock clicked."),
    ("CONNECTED", "The blue ring spun, announcing a new user had joined."),
    ("RUN", "And the lock is clicking once more."),
    ("4.2 MILES IN", "We had been walking since dawn."),
    ("", "Anything at all."),
])
def test_distinct_accent_is_allowed(accent, narration):
    assert not _accent_is_spoken(accent, narration)


def test_partial_overlap_is_not_duplication():
    """Sharing one word is not repeating the line."""
    assert not _accent_is_spoken("THE DOOR MOVED", "The door was shut when I left.")


# ── beat rhythm ───────────────────────────────────────────────────────────────

def _beats(spec):
    """'h L l I' -> scenes; upper case means the beat carries an accent."""
    kinds = {"h": "hook", "l": "line", "i": "impact", "s": "scare", "e": "end"}
    return [
        {"visual": kinds[c.lower()], "accent": "X" if c.isupper() else ""}
        for c in spec.split()
    ]


def test_long_line_run_is_broken():
    scenes = _beats("h l L l l i l l s e")
    _break_line_runs(scenes)
    assert scenes[2]["visual"] == "impact"


def test_short_runs_are_left_alone():
    scenes = _beats("h l L l i s e")
    before = [s["visual"] for s in scenes]
    _break_line_runs(scenes)
    assert [s["visual"] for s in scenes] == before


def test_run_without_any_accent_is_left_alone():
    """
    Promoting an accent-less beat would give impact a flash and a shake with no
    word to punch onto the screen — and would undo the duplication guard.
    """
    scenes = _beats("h l l l l l s e")
    _break_line_runs(scenes)
    assert all(s["visual"] == "line" for s in scenes[1:6])


def test_promotion_prefers_the_middle_of_the_run():
    scenes = _beats("h L L L L L s e")
    _break_line_runs(scenes)
    promoted = [i for i, s in enumerate(scenes) if s["visual"] == "impact"]
    assert promoted == [3]


def test_every_run_is_considered():
    scenes = _beats("h l l L l i l l L l s e")
    _break_line_runs(scenes)
    impacts = [i for i, s in enumerate(scenes) if s["visual"] == "impact"]
    # One promotion in each of the two four-long runs, plus the original impact.
    assert len(impacts) == 3
