"""Tests for skillstotraineyes/difficulty.py"""
import random

import pytest

from skillstotraineyes.difficulty import (
    KNOBS, LEVEL_LABEL, LEVEL_SCORE, LEVELS, fits, pick_level, roll, score,
)

BALL_KNOBS = ["n", "ball_r", "speed", "decoy", "move_s"]


def test_levels_and_labels_are_exact():
    assert LEVELS == ("easy", "medium", "hard", "expert", "god")
    assert LEVEL_LABEL["easy"] == "EASY · 100% can pass this"
    assert LEVEL_LABEL["medium"] == "MEDIUM · 60% can pass this"
    assert LEVEL_LABEL["hard"] == "HARD · 25% can pass this"
    assert LEVEL_LABEL["expert"] == "EXPERT · 10% can pass this"
    assert LEVEL_LABEL["god"] == "GOD MODE · 1% can pass this"


def test_level_ranges_are_contiguous_and_ascending():
    bounds = [LEVEL_SCORE[lv] for lv in LEVELS]
    for lo, hi in bounds:
        assert lo < hi
    for (_, hi), (lo, _) in zip(bounds, bounds[1:]):
        assert hi == lo, "level score ranges must tile with no gap or overlap"


@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize("seed", range(20))
def test_roll_lands_in_the_level_band(level, seed):
    values = roll(BALL_KNOBS, level, random.Random(seed))
    assert set(values) == set(BALL_KNOBS)
    lo, hi = LEVEL_SCORE[level]
    assert lo <= score(BALL_KNOBS, values) < hi


def test_roll_is_deterministic():
    a = roll(BALL_KNOBS, "hard", random.Random(7))
    b = roll(BALL_KNOBS, "hard", random.Random(7))
    assert a == b


def test_roll_varies_within_a_level():
    mixes = {tuple(sorted(roll(BALL_KNOBS, "hard", random.Random(s)).items()))
             for s in range(30)}
    assert len(mixes) > 5, "a level must not collapse to one knob mix"


def test_big_and_small_balls_both_score_hard():
    """Difficulty is not one-dimensional: huge balls crowd, tiny balls hide."""
    sizes = dict(KNOBS["ball_r"])
    hard = [r for r, s in sizes.items() if s >= 3]
    assert min(hard) < 30 and max(hard) > 60


def test_harder_levels_really_are_harder():
    def mean(level):
        return sum(score(BALL_KNOBS, roll(BALL_KNOBS, level, random.Random(s)))
                   for s in range(30)) / 30
    means = [mean(lv) for lv in LEVELS]
    assert all(means[i] < means[i + 1] for i in range(len(means) - 1))


def test_fits_rejects_an_impossible_pack():
    assert fits(3, 440, 40)
    assert not fits(10, 440, 74), "10 huge balls cannot be placed in a 440px arena"
    # n out of range
    assert not fits(1, 440, 40), "n < MIN_BALLS"
    assert not fits(11, 440, 40), "n > MAX_BALLS"
    # ball_r * 4 > arena_r
    assert not fits(3, 100, 30), "ball_r * 4 > arena_r"


def test_ball_knobs_max_score_is_reachable():
    """god level [16, 21) must be reachable: max total score >= 20."""
    max_scores = {name: max(score_val for _, score_val in KNOBS[name])
                  for name in BALL_KNOBS}
    total_max = sum(max_scores.values())
    assert total_max >= 20, f"max reachable score {total_max} < 20"


def test_all_god_rolls_land_in_band():
    """300 god-level rolls must all land in [16, 21) with no off-band fallback."""
    for seed in range(300):
        values = roll(BALL_KNOBS, "god", random.Random(seed))
        total = score(BALL_KNOBS, values)
        assert 16 <= total < 21, f"seed {seed} produced {total}, outside [16, 21)"


def test_pick_level_avoids_the_previous_level():
    assert all(pick_level(s, ["god"]) != "god" for s in range(50))


def test_pick_level_is_weighted_to_the_middle():
    picks = [pick_level(s, []) for s in range(400)]
    assert picks.count("medium") > picks.count("easy")
    assert picks.count("hard") > picks.count("god")
