"""
Difficulty as a score, not a speed dial.

Every knob carries its own difficulty points, and a level is a range of total
points. The seed picks whichever mix of knobs lands in that range, so two videos
at the same level look nothing alike: one GOD MODE drill is ten tiny fast balls,
the next is four huge ones behind an occluder.

Ball size is deliberately non-monotonic. Tiny balls are hard to see and huge
ones crowd the arena and collide constantly, so both ends score high and the
comfortable middle scores zero. Encoding options as explicit (value, score)
pairs rather than an ordered scale is what allows that.

The badge percentages are labels, not measurements.
"""

import random

from skillstotraineyes.sim import MAX_BALLS, MIN_BALLS

LEVELS = ("easy", "medium", "hard", "expert", "god")

LEVEL_LABEL = {
    "easy":   "EASY · 100% can pass this",
    "medium": "MEDIUM · 60% can pass this",
    "hard":   "HARD · 25% can pass this",
    "expert": "EXPERT · 10% can pass this",
    "god":    "GOD MODE · 1% can pass this",
}

# Half-open [lo, hi). They tile with no gap, so every reachable score has a level.
LEVEL_SCORE = {
    "easy":   (0, 4),
    "medium": (4, 8),
    "hard":   (8, 12),
    "expert": (12, 16),
    "god":    (16, 21),
}

# Weighted toward the middle: Easy and God Mode stay rare, so the badge means
# something when it does show up.
LEVEL_WEIGHT = {"easy": 1, "medium": 3, "hard": 3, "expert": 2, "god": 1}

# (value, score) per option. Scores are starting estimates, tuned by watching
# renders — see the spec's open items.
KNOBS: dict[str, tuple[tuple[object, int], ...]] = {
    # Ball drills
    "n":       ((3, 0), (4, 1), (5, 2), (6, 3), (8, 4), (10, 6)),
    "ball_r":  ((50, 0), (44, 0), (38, 1), (32, 2), (26, 3), (20, 4),
                (62, 2), (72, 3)),
    "speed":   ((240, 0), (320, 1), (420, 2), (520, 3), (640, 5)),
    "decoy":   (("distinct", 0), ("similar", 2), ("identical", 3)),
    "move_s":  ((8, 0), (11, 1), (14, 2), (17, 3)),
    # Path-pursuit drills
    "guide":   (("visible", 0), ("faint", 2), ("hidden", 3)),
    "tempo":   ((0.6, 0), (0.9, 1), (1.3, 2), (1.8, 3), (2.4, 4)),
    "occlude": ((0, 0), (1, 2), (2, 3)),
    "ghosts":  ((0, 0), (4, 2), (8, 3)),
    "shift":   ((0.0, 0), (0.25, 2), (0.5, 3)),
    # Grid / flash drills
    "cells":   (((2, 4), 0), ((3, 4), 1), ((3, 5), 2), ((4, 5), 3), ((4, 6), 4)),
    "step":    ((1.0, 0), (0.85, 1), (0.7, 2), (0.55, 3), (0.42, 4)),
    "flash":   ((0.45, 0), (0.35, 1), (0.25, 2), (0.18, 3), (0.12, 4)),
    "gap":     ((1.8, 0), (1.5, 1), (1.2, 2), (0.95, 3)),
    "radius":  ((320, 0), (380, 1), (420, 2), (460, 3)),
    "look_s":  ((12, 0), (10, 1), (8, 2), (6, 3)),
    "density": (((5, 7), 0), ((6, 8), 1), ((7, 9), 2), ((8, 10), 3)),
}

_MAX_TRIES = 200


def score(knob_names: list[str], values: dict) -> int:
    """Total difficulty points for one knob mix."""
    total = 0
    for name in knob_names:
        options = dict(KNOBS[name])
        total += options[values[name]]
    return total


def roll(knob_names: list[str], level: str, rng: random.Random) -> dict:
    """
    One value per knob, chosen so the total lands in `level`'s score range.

    Rejection sampling: the knob sets are tiny (3-6 knobs, 3-8 options each), so
    a mix in range turns up within a handful of draws. On the rare miss it
    returns the closest mix rather than raising — a slightly off-band drill is
    always better than no video.

    ponytail: rejection sampling, O(tries * knobs). Swap for a bounded-knapsack
    walk if a family ever carries enough knobs to make misses common.
    """
    if level not in LEVEL_SCORE:
        raise ValueError(f"unknown level {level!r}; expected one of {LEVELS}")
    lo, hi = LEVEL_SCORE[level]
    best, best_miss = None, None
    for _ in range(_MAX_TRIES):
        values = {name: rng.choice(KNOBS[name])[0] for name in knob_names}
        total = score(knob_names, values)
        if lo <= total < hi:
            return values
        miss = lo - total if total < lo else total - (hi - 1)
        if best_miss is None or miss < best_miss:
            best, best_miss = values, miss
    return best


def fits(n: int, arena_r: float, ball_r: float) -> bool:
    """
    True when `n` balls of `ball_r` can plausibly be placed in the arena.

    `sim.make_sim()` rejects a start where any two centres are closer than
    2.2*ball_r, by rejection sampling up to 1000 times per ball, and raises when
    it cannot. Random sequential packing jams near a 0.35 area fraction, so
    screening on that here turns a crash into a re-roll.
    """
    if not MIN_BALLS <= n <= MAX_BALLS:
        return False
    if ball_r * 4 > arena_r:            # make_sim's own guard
        return False
    free = (arena_r - ball_r) ** 2
    return n * (1.1 * ball_r) ** 2 <= 0.35 * free


def pick_level(seed: int, recent: list[str]) -> str:
    """Weighted level choice that never repeats the previous video's level."""
    last = recent[0] if recent else None
    pool = [lv for lv in LEVELS if lv != last]
    weights = [LEVEL_WEIGHT[lv] for lv in pool]
    return random.Random(seed).choices(pool, weights=weights, k=1)[0]
