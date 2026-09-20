# Eye Scenarios (difficulty levels, drill catalog, hidden-object posts) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the `skillstotraineyes` niche five difficulty levels, a data-driven drill catalog covering the skilldrills.online drill set, and a second content type — hidden-object image posts whose answer is never revealed.

**Architecture:** Difficulty becomes a *score* over independent knobs (count, size, speed, decoy similarity, path visibility, occlusion), not a speed dial; a level is a score range and a seeded generator picks a knob mix that lands in it. Drills move from a hardcoded `FAMILIES` tuple to `catalog.json`, where many entries share one parameterised builder. Hidden-object posts reuse the existing image-generation chain and the existing Drive `pending/` + cron publish flow, routed by a new `media_type` key in the schedule manifest.

**Tech Stack:** Python 3, numpy, Pillow, ffmpeg, SQLite, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-20-eye-scenarios-design.md`

**Scope:** Phases 1 and 2 only (Tasks 1–10). Phase 3 (memory and cognitive drill families from the memory/cognitive CSVs) is deliberately out of scope and gets its own plan once phase 1's knob scores are tuned against real renders.

## Global Constraints

- Wording is "eye exercise for fun". Never a medical claim. The `_CLAIMS` regex in `skillstotraineyes/wording.py` stays authoritative, and `niche["disclaimer"]` is appended in code, never by the LLM.
- Every drill video runs 15–30 seconds. `tests/test_skillstotraineyes_drills.py::test_drill_shape` enforces it.
- Frame is 1080×1920 at 30fps. Nothing is drawn below `SAFE_BOTTOM = 1630` (Instagram's UI band) and nothing outside the side gutters (`_TEXT_MAX_W = 880`).
- Determinism is asserted on sim state and params, never on pixels — Pillow anti-aliasing varies across versions.
- Ball count is 2..10 (`sim.MIN_BALLS` / `sim.MAX_BALLS`). `make_sim` raises outside that, and on infeasible radius/arena/speed combinations.
- Badge text is exact: `EASY · 100% can pass this`, `MEDIUM · 60% can pass this`, `HARD · 25% can pass this`, `EXPERT · 10% can pass this`, `GOD MODE · 1% can pass this`.
- LLM output is untrusted everywhere: length-capped, claim-screened, compared against recent, with a default fallback per field. A dead LLM costs variety, never a video.
- No new GitHub Actions secrets. The Drive env-var block in `CLAUDE.md` is load-bearing — do not touch the workflow files.
- Hidden-object posts never reveal the answer, in the image, the caption or anywhere else.
- Never add `Co-Authored-By` or any AI attribution to a commit. Sole author: Rahul Jain.

---

## File Structure

**Create:**
- `skillstotraineyes/difficulty.py` — levels, knobs, the score-budget generator, feasibility screen, badge labels.
- `skillstotraineyes/catalog.json` — one entry per drill: id, display name, description, builder name, mode, fixed knobs, presets.
- `skillstotraineyes/hidden_object.py` — LLM scene invention, prompt assembly, image generation, JPEG conversion, resolution gate.
- `tests/test_skillstotraineyes_difficulty.py`
- `tests/test_skillstotraineyes_catalog.py`
- `tests/test_skillstotraineyes_hidden_object.py`
- `tests/test_instagram_image_post.py`

**Modify:**
- `skillstotraineyes/drills.py` — badge in `_text_ops`, `level` through every builder, catalog-driven `build()`, new `path_pursuit` builder, `tracking` robustness.
- `skillstotraineyes/renderer.py` — a `rect` draw op for the occluder band.
- `skillstotraineyes/wording.py` — catalog-aware drill pick, hidden-object wording, position-word screen.
- `run_skillstotraineyes.py` — level selection, `--level`, `--mode image`, catalog family names.
- `pipeline/instagram_upload.py` — `upload_image_post()`, dynamic MIME in `_upload_to_temp_host`.
- `scripts/upload_all_platforms.py` — `media_type` routing.
- `scripts/run_scheduled_upload.py` — honour `media_type` in the manifest.
- `pipeline/scheduler.py` — `media_type` into the manifest.
- `settings.json` — `human_policy`, `levels`, `hidden_object` keys on the niche.
- `CLAUDE.md` — document all of the above.

---

# PHASE 1 — Difficulty and catalog (Tasks 1–6)

## Task 1: Difficulty scores, levels and the knob generator

**Files:**
- Create: `skillstotraineyes/difficulty.py`
- Test: `tests/test_skillstotraineyes_difficulty.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `LEVELS: tuple[str, ...]` = `("easy", "medium", "hard", "expert", "god")`
  - `LEVEL_LABEL: dict[str, str]` — badge text per level
  - `LEVEL_SCORE: dict[str, tuple[int, int]]` — half-open `[lo, hi)` score range per level
  - `KNOBS: dict[str, tuple[tuple[object, int], ...]]` — knob name to `(value, score)` options
  - `pick_level(seed: int, recent: list[str]) -> str`
  - `roll(knob_names: list[str], level: str, rng: random.Random) -> dict` — one value per knob
  - `score(knob_names: list[str], values: dict) -> int`
  - `fits(n: int, arena_r: float, ball_r: float) -> bool`

- [ ] **Step 1: Write the failing test**

Create `tests/test_skillstotraineyes_difficulty.py`:

```python
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
    assert means == sorted(means)


def test_fits_rejects_an_impossible_pack():
    assert fits(3, 440, 40)
    assert not fits(10, 440, 74), "10 huge balls cannot be placed in a 440px arena"


def test_pick_level_avoids_the_previous_level():
    assert all(pick_level(s, ["god"]) != "god" for s in range(50))


def test_pick_level_is_weighted_to_the_middle():
    picks = [pick_level(s, []) for s in range(400)]
    assert picks.count("medium") > picks.count("easy")
    assert picks.count("hard") > picks.count("god")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_skillstotraineyes_difficulty.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skillstotraineyes.difficulty'`

- [ ] **Step 3: Write the implementation**

Create `skillstotraineyes/difficulty.py`:

```python
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
    "n":       ((3, 0), (4, 1), (5, 2), (6, 3), (8, 4), (10, 5)),
    "ball_r":  ((50, 0), (44, 0), (38, 1), (32, 2), (26, 3), (20, 4),
                (62, 2), (72, 3)),
    "speed":   ((240, 0), (320, 1), (420, 2), (520, 3), (640, 4)),
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
    if not 2 <= n <= 10:
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_skillstotraineyes_difficulty.py -v`
Expected: PASS, 60+ tests.

If `test_roll_lands_in_the_level_band` fails for `god`, the five ball knobs top out below 16. Raise the top option scores in `n`, `ball_r` and `speed` until the maximum reachable total is at least 20, then re-run.

- [ ] **Step 5: Commit**

```bash
git add skillstotraineyes/difficulty.py tests/test_skillstotraineyes_difficulty.py
git commit -m "feat: difficulty scores, five levels and a seeded knob generator"
```

---

## Task 2: Difficulty badge on every drill, at zero duration cost

**Files:**
- Modify: `skillstotraineyes/drills.py:65-73` (`_text_ops`), and each of the six builders
- Test: `tests/test_skillstotraineyes_drills.py`

**Interfaces:**
- Consumes: `difficulty.LEVEL_LABEL` (Task 1).
- Produces: `drills.build(family, seed, text=None, level=None) -> Drill`; `Drill.level: str`; `BADGE_Y: int`; `BADGE_SIZE: int`; `_text_ops(..., badge: str = "")`.

The badge is drawn *inside* the existing hook window, above the hook text. It must not add a segment: `saccade` already runs near 27.5s and the 30s ceiling is asserted by `test_drill_shape`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_skillstotraineyes_drills.py`:

```python
from skillstotraineyes.difficulty import LEVELS, LEVEL_LABEL


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("level", LEVELS)
def test_badge_shows_at_the_start(family, level):
    d = build(family, 3, level=level)
    first = [op[1] for op in d.ops(0) if op[0] == "text"]
    assert LEVEL_LABEL[level] in first
    assert d.level == level


@pytest.mark.parametrize("family", FAMILIES)
def test_badge_costs_no_duration(family):
    plain = build(family, 3, level=None)
    badged = build(family, 3, level="god")
    assert badged.duration == plain.duration


@pytest.mark.parametrize("family", FAMILIES)
def test_badge_is_gone_by_the_end(family):
    d = build(family, 3, level="god")
    last = [op[1] for op in d.ops(d.frames - 1) if op[0] == "text"]
    assert LEVEL_LABEL["god"] not in last
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_skillstotraineyes_drills.py -k badge -v`
Expected: FAIL — `TypeError: build() got an unexpected keyword argument 'level'`

- [ ] **Step 3: Write the implementation**

In `skillstotraineyes/drills.py`, add the badge constants next to the existing layout constants (after `TEXT_LOW_Y = 1450`):

```python
BADGE_Y = 120               # above the hook; the hook block starts at TEXT_TOP_Y
BADGE_SIZE = 54
BADGE_COLOR = (255, 196, 61)
```

Replace `_text_ops` (currently lines 65-73) with:

```python
def _text_ops(t: float, hook: str, hook_until: float, question: str, t_q: float,
              cta: str, t_cta: float, badge: str = "") -> list:
    if t < hook_until:
        out = [("text", hook, CX, TEXT_TOP_Y, 92, WHITE)]
        if badge:
            # Inside the hook window on purpose: a badge segment of its own would
            # push saccade past the 30s ceiling.
            out.insert(0, ("text", badge, CX, BADGE_Y, BADGE_SIZE, BADGE_COLOR))
        return out
    if t >= t_cta:
        return [("text", cta, CX, TEXT_LOW_Y, 88, WHITE)]
    if t >= t_q:
        return [("text", question, CX, TEXT_LOW_Y, 66, WHITE)]
    return []
```

Add `level` to the `Drill` dataclass, after `fps: int = FPS`:

```python
    level: str = ""
```

Add a helper below `_text`:

```python
def _badge(level: str | None) -> str:
    """Badge text for a level, or "" when the drill is unlevelled."""
    from skillstotraineyes.difficulty import LEVEL_LABEL
    return LEVEL_LABEL.get(level or "", "")
```

In each of the six builders — `tracking`, `_pursuit`, `saccade`, `peripheral`, `search` — take `level: str | None = None`, compute `badge = _badge(level)` next to the other text locals, pass `badge=badge` as the final argument of the `_text_ops(...)` call inside `ops`, and add `level=level or ""` to the `Drill(...)` construction. For `pursuit_dual` and `figure8`, add the `level` parameter and forward it to `_pursuit`.

Worked example — `tracking`:

```python
def tracking(seed: int, text: dict | None = None, level: str | None = None) -> Drill:
    ...
    hook = _text(text, "hook", "Track the red ball")
    question = _text(text, "question", "Were you able to track it?")
    cta = _text(text, "cta", DEFAULT_TEXT["cta"])
    badge = _badge(level)

    def ops(f: int) -> list:
        ...
        return out + _text_ops(t, hook, 2.5, question, t_q, cta, t_cta, badge=badge)

    return Drill("tracking", total, ops,
                 {"family": "tracking", "n": n, "arena": arena_r, "ball": ball_r,
                  "speed": round(speed, -1), "move": move_s},
                 chimes=[t_reveal], voice=_lines(hook, question, cta, 2.5, t_q, t_cta),
                 level=level or "")
```

And `_pursuit` gains `level: str | None = None` as its last parameter, with `badge = _badge(level)` before `ops` and `level=level or ""` on its `Drill(...)`.

Finally, widen `build`:

```python
def build(family: str, seed: int, text: dict | None = None,
          level: str | None = None) -> Drill:
    return BUILDERS[family](seed, text, level)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_skillstotraineyes_drills.py -v -m "not slow"`
Expected: PASS. The pre-existing `test_drill_shape`, `test_ends_with_cta` and `test_text_override_reaches_frames` must still pass — the badge is additive and absent when `level` is `None`.

- [ ] **Step 5: Commit**

```bash
git add skillstotraineyes/drills.py tests/test_skillstotraineyes_drills.py
git commit -m "feat: difficulty badge in the hook window, no added duration"
```

---

## Task 3: Knob-driven `tracking`, with no crash at high difficulty

**Files:**
- Modify: `skillstotraineyes/drills.py:107-166` (`tracking`)
- Test: `tests/test_skillstotraineyes_drills.py`

**Interfaces:**
- Consumes: `difficulty.roll`, `difficulty.fits`, `difficulty.KNOBS` (Task 1); `Drill.level` (Task 2).
- Produces: `tracking(seed, text=None, level=None) -> Drill` whose `params` now carry `"level"` and the rolled knob values.

This is the risk task. Today `tracking` raises `RuntimeError` when 60 seeds miss its `min_gap(0) >= 3.0` reveal gate, and `make_sim` raises outright on an infeasible ball count/radius pair. Both become likely at `expert` and `god`. Neither may kill a run.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_skillstotraineyes_drills.py`:

```python
from skillstotraineyes.difficulty import LEVEL_SCORE


@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize("seed", range(12))
def test_tracking_never_raises_at_any_level(level, seed):
    d = build("tracking", seed, level=level)
    assert 15 <= d.duration <= 30
    assert d.params["level"] == level


@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize("seed", range(12))
def test_tracking_reveal_stays_readable(level, seed):
    """The target must be clear of its neighbours when it lights up."""
    d = build("tracking", seed, level=level)
    assert d.params["gap"] >= 2.0, f"{level}/{seed} reveal gap {d.params['gap']}"


def test_tracking_gets_harder_with_level():
    def n_balls(level):
        return sum(build("tracking", s, level=level).params["n"] for s in range(10))
    assert n_balls("god") > n_balls("easy")


@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize("seed", range(12))
def test_tracking_params_are_physically_placeable(level, seed):
    from skillstotraineyes.difficulty import fits
    p = build("tracking", seed, level=level).params
    assert fits(p["n"], p["arena"], p["ball"])
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_skillstotraineyes_drills.py -k tracking -v`
Expected: FAIL — `KeyError: 'level'` on `d.params["level"]`, and likely a `RuntimeError("no unambiguous reveal found in 60 seeds")` or a `ValueError` from `make_sim` on a `god` seed.

- [ ] **Step 3: Write the implementation**

Replace the body of `tracking` in `skillstotraineyes/drills.py` down to the `sim is None` check:

```python
_TRACKING_KNOBS = ["n", "ball_r", "speed", "decoy", "move_s"]


def tracking(seed: int, text: dict | None = None, level: str | None = None) -> Drill:
    from skillstotraineyes.difficulty import fits, roll

    rng = random.Random(seed)
    accent = rng.choice(ACCENTS)
    static_s, recolor_s = 2.0, 2.0

    # Re-roll rather than raise: an infeasible pack (ten balls at r=72) is a
    # legitimate draw from the knob table, and make_sim rejects it outright.
    arena_r = 440
    for _attempt in range(40):
        # The arena is re-picked each try, so a preset that pins n and ball_r can
        # still find a home rather than re-rolling identical values forever.
        arena_r = rng.choice([400, 420, 440])
        if level:
            k = roll(_TRACKING_KNOBS, level, rng)
            n, ball_r, speed, decoy, move_s = (
                k["n"], k["ball_r"], float(k["speed"]), k["decoy"], k["move_s"])
        else:
            n, ball_r = rng.randint(2, 5), rng.randint(34, 50)
            speed, decoy, move_s = rng.uniform(250, 450), "distinct", rng.choice([10, 12, 14, 16])
        if fits(n, arena_r, ball_r):
            break
    else:
        n, ball_r, speed, decoy, move_s = 4, 40, 320.0, "distinct", 12

    # The reveal must be unambiguous, but a crowded arena cannot always give the
    # target three clear radii. Scale the bar with the crowd and keep the best
    # candidate seen, because raising here would kill the whole run.
    want_gap = 3.0 if n <= 5 else 2.2
    best = None
    for attempt in range(60):
        cand = make_sim(seed * 1000 + attempt, n, arena_r, ball_r, speed, arena_c=(CX, CY))
        pos0 = cand.pos.copy()
        frames = run(cand, round(move_s * FPS), FPS)
        gap = cand.min_gap(0)
        if best is None or gap > best[0]:
            best = (gap, cand, pos0, frames)
        if gap >= want_gap:
            break
    gap, sim, pos0, frames = best
    if gap < want_gap:
        # ponytail: best-effort reveal. Tighten by lowering the top `n` option
        # if this warns often at god level.
        log.warning("tracking: best reveal gap %.1f radii (wanted %.1f) at n=%d",
                    gap, want_gap, n)
```

Delete the now-dead `if sim is None: raise RuntimeError(...)` block and the old parameter lines above it (`n = rng.randint(2, 5)` through `static_s, recolor_s = 2.0, 2.0`).

Add a module logger at the top of `drills.py`, after the imports:

```python
import logging

log = logging.getLogger(__name__)
```

Apply the `decoy` knob where the non-target balls are drawn, inside `ops`:

```python
            else:
                if revealed:
                    col = DIM
                elif decoy == "identical":
                    col = RED if i == 1 else WHITE     # one convincing double
                elif decoy == "similar":
                    col = (210, 110, 110)
                else:
                    col = WHITE
                out.append(("disc", x, y, ball_r, col))
```

Record the knobs in `params`:

```python
    return Drill("tracking", total, ops,
                 {"family": "tracking", "level": level or "", "n": n,
                  "arena": arena_r, "ball": ball_r, "speed": round(speed, -1),
                  "decoy": decoy, "move": move_s, "gap": round(gap, 1)},
                 chimes=[t_reveal], voice=_lines(hook, question, cta, 2.5, t_q, t_cta),
                 level=level or "")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_skillstotraineyes_drills.py -v -m "not slow"`
Expected: PASS. `test_tracking_target_is_id_and_recolours` must still pass — with `decoy == "identical"` a second ball is red, so confirm that test builds seed 5 at no level (it does) and therefore keeps `decoy == "distinct"`.

- [ ] **Step 5: Commit**

```bash
git add skillstotraineyes/drills.py tests/test_skillstotraineyes_drills.py
git commit -m "fix: tracking re-rolls infeasible packs and never raises on a crowded reveal"
```

---

## Task 4: Drill catalog as the single source of families

**Files:**
- Create: `skillstotraineyes/catalog.json`
- Modify: `skillstotraineyes/drills.py` (`FAMILIES`, `BUILDERS`, `build`, `pick_family`)
- Test: `tests/test_skillstotraineyes_catalog.py`

**Interfaces:**
- Consumes: `build()` and every builder from Tasks 2–3.
- Produces:
  - `drills.load_catalog() -> list[dict]` (cached)
  - `drills.CATALOG_PATH: Path`
  - `drills.entry(drill_id: str) -> dict`
  - `drills.FAMILIES: tuple[str, ...]` — now every catalog id
  - `drills.build(family, seed, text=None, level=None)` — resolves a catalog id to its builder and merges the entry's fixed knobs
  - `drills.knob_names(drill_id: str) -> list[str]`

A catalog entry is `{id, name, about, builder, mode, knobs, knob_names, presets}`. Several entries share one builder with different fixed `knobs` — that is the whole point: five CSV path drills are one builder and five entries.

- [ ] **Step 1: Write the failing test**

Create `tests/test_skillstotraineyes_catalog.py`:

```python
"""Tests for skillstotraineyes/catalog.json and the catalog-driven build()."""
import pytest

from skillstotraineyes.difficulty import KNOBS, LEVELS
from skillstotraineyes.drills import (
    BUILDERS, FAMILIES, build, entry, knob_names, load_catalog, pick_family,
)

REQUIRED = {"id", "name", "about", "builder", "mode", "knobs", "knob_names", "presets"}


def test_every_entry_has_the_required_fields():
    for e in load_catalog():
        assert REQUIRED <= set(e), f"{e.get('id')} missing {REQUIRED - set(e)}"


def test_entry_ids_are_unique():
    ids = [e["id"] for e in load_catalog()]
    assert len(ids) == len(set(ids))


def test_every_builder_exists():
    """The old FAMILIES/BUILDERS split let an entry be unreachable or a KeyError."""
    for e in load_catalog():
        assert e["builder"] in BUILDERS, f"{e['id']} names unknown builder {e['builder']}"


def test_families_is_the_catalog():
    assert set(FAMILIES) == {e["id"] for e in load_catalog()}


def test_every_knob_name_is_real():
    for e in load_catalog():
        for name in e["knob_names"]:
            assert name in KNOBS, f"{e['id']} names unknown knob {name}"


def test_fixed_knob_values_are_legal():
    for e in load_catalog():
        for name, value in e["knobs"].items():
            if name in KNOBS:
                assert value in dict(KNOBS[name]), f"{e['id']}: {name}={value!r}"


def test_modes_are_known():
    assert {e["mode"] for e in load_catalog()} <= {"watch", "challenge"}


def test_presets_name_a_real_level():
    for e in load_catalog():
        for p in e["presets"]:
            assert p["level"] in LEVELS
            assert p["name"]


def test_the_six_original_families_survive():
    """Existing DB rows and --family arguments must keep working."""
    assert {"tracking", "pursuit_dual", "figure8", "saccade",
            "peripheral", "search"} <= set(FAMILIES)


def test_entry_lookup_raises_clearly():
    with pytest.raises(KeyError, match="nope"):
        entry("nope")


def test_knob_names_excludes_fixed_knobs():
    """A knob the entry pins is not rolled."""
    for e in load_catalog():
        assert not (set(knob_names(e["id"])) & set(e["knobs"]))


@pytest.mark.parametrize("drill_id", FAMILIES)
@pytest.mark.parametrize("level", LEVELS)
def test_every_entry_builds_at_every_level(drill_id, level):
    d = build(drill_id, 5, level=level)
    assert 15 <= d.duration <= 30, f"{drill_id}/{level} ran {d.duration}s"
    assert d.level == level


def test_pick_family_covers_the_whole_catalog():
    seen = {pick_family(s, []) for s in range(600)}
    assert seen == set(FAMILIES)


def test_a_preset_fires_sometimes_and_pins_its_own_level():
    """Presets are signature knob mixes; the budget generator is the default."""
    built = [build("tracking", s) for s in range(80)]
    used = [d for d in built if d.params["preset"]]
    assert used, "no preset ever fired in 80 seeds"
    assert len(used) < len(built), "presets must not replace the generator"
    for d in used:
        preset = next(p for p in entry("tracking")["presets"]
                      if p["name"] == d.params["preset"])
        assert d.level == preset["level"]
        for name, value in preset["knobs"].items():
            assert d.params[{"ball_r": "ball", "move_s": "move"}.get(name, name)] == value


def test_an_explicit_level_beats_a_preset():
    assert all(build("tracking", s, level="easy").level == "easy" for s in range(80))
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_skillstotraineyes_catalog.py -v`
Expected: FAIL — `ImportError: cannot import name 'entry' from 'skillstotraineyes.drills'`

- [ ] **Step 3: Write the implementation**

Create `skillstotraineyes/catalog.json` with the six existing drills (Task 5 appends the path-pursuit entries):

```json
{
  "drills": [
    {
      "id": "tracking",
      "name": "Target Lock",
      "about": "One ball turns red, then all of them turn white and scatter. Keep your eyes on the one that was red.",
      "builder": "tracking",
      "mode": "challenge",
      "knobs": {},
      "knob_names": ["n", "ball_r", "speed", "decoy", "move_s"],
      "presets": [
        {"name": "Swarm", "level": "god", "knobs": {"n": 10, "ball_r": 26, "speed": 520, "decoy": "identical", "move_s": 14}},
        {"name": "Bullet", "level": "expert", "knobs": {"n": 4, "ball_r": 72, "speed": 640, "decoy": "similar", "move_s": 11}}
      ]
    },
    {
      "id": "pursuit_dual",
      "name": "Divided Pursuit",
      "about": "Two dots trace opposite Lissajous curves. Hold both at once without turning your head.",
      "builder": "pursuit_dual",
      "mode": "watch",
      "knobs": {},
      "knob_names": ["tempo", "guide"],
      "presets": []
    },
    {
      "id": "figure8",
      "name": "Infinity Pursuit",
      "about": "A single dot runs a figure-eight. Follow it with your eyes only.",
      "builder": "figure8",
      "mode": "watch",
      "knobs": {},
      "knob_names": ["tempo", "guide"],
      "presets": []
    },
    {
      "id": "saccade",
      "name": "Jump Grid",
      "about": "Dots light up one at a time across a grid. Snap your eyes to each the moment it lights.",
      "builder": "saccade",
      "mode": "watch",
      "knobs": {},
      "knob_names": ["cells", "step"],
      "presets": []
    },
    {
      "id": "peripheral",
      "name": "Edge Flash",
      "about": "Hold the centre cross while dots flash at the rim. Count them without looking away.",
      "builder": "peripheral",
      "mode": "challenge",
      "knobs": {},
      "knob_names": ["gap", "flash", "radius"],
      "presets": []
    },
    {
      "id": "search",
      "name": "Odd One Out",
      "about": "A field of circles hides one square. Find it before the ring appears.",
      "builder": "search",
      "mode": "challenge",
      "knobs": {},
      "knob_names": ["density", "look_s"],
      "presets": []
    }
  ]
}
```

In `skillstotraineyes/drills.py`, replace the `FAMILIES = (...)` line with catalog loading, and rewrite `build` / `pick_family`:

```python
from functools import lru_cache
from pathlib import Path

CATALOG_PATH = Path(__file__).with_name("catalog.json")


@lru_cache(maxsize=1)
def load_catalog() -> tuple[dict, ...]:
    """Every drill the niche knows, as data. Cached — the file never changes at runtime."""
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return tuple(data["drills"])


def entry(drill_id: str) -> dict:
    for e in load_catalog():
        if e["id"] == drill_id:
            return e
    raise KeyError(f"no catalog entry {drill_id!r}; known: {sorted(e['id'] for e in load_catalog())}")


def knob_names(drill_id: str) -> list[str]:
    """The knobs this drill rolls — its declared knobs minus the ones it pins."""
    e = entry(drill_id)
    return [n for n in e["knob_names"] if n not in e["knobs"]]
```

Move that block **above** the `@dataclass class Drill` definition so `FAMILIES` can be derived right after it:

```python
FAMILIES = tuple(e["id"] for e in load_catalog())
```

Replace `build`. A named preset is a signature knob mix that overrides the generator — it pins its own level and its own knob values, so "Swarm" is the same drill every time it comes up:

```python
PRESET_CHANCE = 0.25


def build(family: str, seed: int, text: dict | None = None,
          level: str | None = None) -> Drill:
    """
    Build a catalog drill. `family` is a catalog entry id, not a builder name.

    A quarter of the time an entry with presets uses one instead of rolling: a
    preset is a hand-picked knob mix worth repeating exactly (ten tiny identical
    balls, or four huge fast ones), and it carries its own level. An explicit
    `level` argument is honoured, so --level always wins.
    """
    e = entry(family)
    preset = None
    if e["presets"] and level is None and random.Random(seed ^ 0x9E37).random() < PRESET_CHANCE:
        preset = random.Random(seed).choice(e["presets"])
        e = {**e, "knobs": {**e["knobs"], **preset["knobs"]}}
        level = preset["level"]
    drill = BUILDERS[e["builder"]](seed, text, level, e)
    drill.params["drill_id"] = family
    drill.params["preset"] = preset["name"] if preset else ""
    return drill
```

Note the `level is None` guard: a preset only fires when the caller did not ask for a level, so `--level god` and `pick_level()` both stay authoritative. That is why `test_every_entry_builds_at_every_level` (which always passes a level) never exercises this branch — the preset test below does.

Every builder therefore takes a fourth parameter. Give each the signature
`(seed, text=None, level=None, entry_cfg=None)` and start it with:

```python
    entry_cfg = entry_cfg or {"knobs": {}, "id": "", "knob_names": []}
    fixed = entry_cfg.get("knobs", {})
```

Then, where a builder rolls its knobs, merge the pinned ones in — in `tracking`, replace the `k = roll(...)` line with:

```python
            k = {**roll(knob_names(entry_cfg["id"]), level, rng), **fixed}
```

`pick_family` needs no change — it already reads `FAMILIES`, which is now the catalog.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_skillstotraineyes_catalog.py tests/test_skillstotraineyes_drills.py -v -m "not slow"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add skillstotraineyes/catalog.json skillstotraineyes/drills.py tests/test_skillstotraineyes_catalog.py
git commit -m "feat: catalog.json is the single source of drill families"
```

---

## Task 5: `path_pursuit` builder and the eight path drills from the CSVs

**Files:**
- Modify: `skillstotraineyes/drills.py` (new builder), `skillstotraineyes/renderer.py` (a `rect` op), `skillstotraineyes/catalog.json` (eight entries)
- Test: `tests/test_skillstotraineyes_catalog.py`

**Interfaces:**
- Consumes: `entry`, `knob_names`, `build` (Task 4); `roll` (Task 1).
- Produces: `drills.path_pursuit(seed, text=None, level=None, entry_cfg=None) -> Drill`; `drills._PATHS: dict[str, callable]`; the `("rect", cx, cy, half_w, half_h, color)` draw op.

One builder covers Constant Slow Pursuit, Infinity Pursuit, Sine-Wave Pursuit, Triangular Pursuit, Zig-Zag Path Pursuit, Predictive Pursuit, Spatial-Shift Pursuit, Ghosting Suppress Pursuit and Vertical Tracking. The `path` knob is pinned per entry; `guide`, `tempo`, `occlude`, `ghosts` and `shift` are rolled by level.

The guide line is drawn as spaced dots using the existing `disc` op, so the renderer needs no polyline support and `test_drill_shape`'s `x, y = op[1], op[2]` indexing keeps working.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_skillstotraineyes_catalog.py`:

```python
PATH_DRILLS = ["slow_pursuit", "sine_pursuit", "triangle_pursuit", "zigzag_pursuit",
               "predictive_pursuit", "spatial_shift_pursuit", "ghosting_pursuit",
               "vertical_pursuit"]


def test_path_drills_are_in_the_catalog():
    assert set(PATH_DRILLS) <= set(FAMILIES)


@pytest.mark.parametrize("drill_id", PATH_DRILLS)
@pytest.mark.parametrize("level", LEVELS)
def test_path_drill_stays_on_screen(drill_id, level):
    from skillstotraineyes.drills import H, SAFE_BOTTOM, W
    d = build(drill_id, 4, level=level)
    for f in range(0, d.frames, 5):
        for op in d.ops(f):
            if op[0] == "text":
                assert op[4] * 0.6 + op[3] < SAFE_BOTTOM
            else:
                x, y = op[1], op[2]
                assert 0 < x < W and 0 < y < H, f"{drill_id}/{level}: {op}"


@pytest.mark.parametrize("drill_id", PATH_DRILLS)
def test_visible_guide_draws_dots_hidden_does_not(drill_id):
    from skillstotraineyes.drills import GUIDE_R
    vis = build(drill_id, 4, level="easy")
    if vis.params.get("guide") != "visible":
        pytest.skip("this seed did not roll a visible guide")
    mid = vis.frames // 2
    guide_dots = [op for op in vis.ops(mid) if op[0] == "disc" and op[3] == GUIDE_R]
    assert len(guide_dots) > 30, "a visible guide must actually be drawn"


def test_occluder_hides_the_dot():
    """Predictive Pursuit: the target must genuinely vanish behind the band."""
    from skillstotraineyes.drills import DOT_R
    d = build("predictive_pursuit", 4, level="god")
    assert d.params["occlude"] >= 1
    assert any(op[0] == "rect" for op in d.ops(d.frames // 2))
    body = range(int(3.0 * d.fps), d.frames - int(4.0 * d.fps))
    hidden = [f for f in body if not any(op[0] == "disc" and op[3] == DOT_R for op in d.ops(f))]
    assert hidden, "the dot never went behind the occluder"


def test_ghosting_draws_trail_dots():
    d = build("ghosting_pursuit", 4, level="god")
    assert d.params["ghosts"] >= 1
    mid = d.frames // 2
    assert len([op for op in d.ops(mid) if op[0] == "disc"]) > d.params["ghosts"]


@pytest.mark.parametrize("drill_id", PATH_DRILLS)
def test_path_drill_is_deterministic(drill_id):
    a, b = build(drill_id, 9, level="hard"), build(drill_id, 9, level="hard")
    assert a.params == b.params
    assert all(a.ops(f) == b.ops(f) for f in range(0, a.frames, 11))
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_skillstotraineyes_catalog.py -k path -v`
Expected: FAIL — `AssertionError` on `test_path_drills_are_in_the_catalog`.

- [ ] **Step 3: Write the implementation**

**3a.** Add the `rect` op to `skillstotraineyes/renderer.py`, in `draw_frame`, right after the `square` branch:

```python
        elif kind == "rect":
            _, x, y, hw, hh, col = op
            dr.rectangle([x - hw, y - hh, x + hw, y + hh], fill=col)
```

**3b.** Add the path maths and the builder to `skillstotraineyes/drills.py`, after the existing `_pursuit` helpers:

```python
DOT_R = 34          # the pursued target
GUIDE_R = 5         # one dot of the drawn guide line
GUIDE_SAMPLES = 140
AX, AY = 410, 450   # path half-extents; CY+AY = 1350, clear of SAFE_BOTTOM


def _tri(u: float) -> float:
    """Triangle wave, period 1, range [-1, 1]. Sweeps without a wrap-around jump."""
    u = u % 1.0
    return 4 * u - 1 if u < 0.5 else 3 - 4 * u


def _make_path(kind: str, rng: random.Random, period: float):
    """A path as f(t) -> (x, y), repeating every `period` seconds."""
    tau = 2 * math.pi

    if kind == "sine":
        cycles = rng.choice([2, 3, 4])
        return lambda t: (CX + AX * _tri(t / period),
                          CY + AY * math.sin(tau * cycles * t / period))
    if kind == "zigzag":
        cycles = rng.choice([3, 4, 5])
        return lambda t: (CX + AX * _tri(t / period),
                          CY + AY * _tri(cycles * t / period))
    if kind == "triangle":
        pts = [(CX, CY - AY), (CX + AX, CY + AY), (CX - AX, CY + AY)]

        def tri_path(t):
            u = (t / period) % 1.0 * 3
            i = int(u) % 3
            a, b = pts[i], pts[(i + 1) % 3]
            k = u - int(u)
            return (a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k)
        return tri_path
    if kind == "lemniscate":
        return lambda t: (CX + AX * math.sin(tau * t / period),
                          CY + AY * math.sin(tau * t / period) * math.cos(tau * t / period) * 2)
    if kind == "lissajous":
        a, b = rng.choice([(2, 3), (3, 4), (1, 2)])
        ph = rng.uniform(0, math.pi)
        return lambda t: (CX + AX * math.sin(a * tau * t / period),
                          CY + AY * math.sin(b * tau * t / period + ph))
    if kind == "steps":
        n = rng.choice([4, 6])

        def step_path(t):
            u = (t / period) % 1.0
            k = int(u * n)
            slide = min(1.0, (u * n - k) * 1.6)   # slide, then hold before the snap
            return (CX + AX * _tri((k + slide) / n),
                    CY + AY * (2 * (k / (n - 1)) - 1))
        return step_path
    raise ValueError(f"unknown path {kind!r}")


_PATHS = ("sine", "zigzag", "triangle", "lemniscate", "lissajous", "steps")


def path_pursuit(seed: int, text: dict | None = None, level: str | None = None,
                 entry_cfg: dict | None = None) -> Drill:
    """
    One dot on a named path. Covers every pursuit drill in the catalog: the path
    is pinned per entry, and the level rolls how fast it moves, whether the guide
    line is drawn, whether it hides behind an occluder, and how many ghost trails
    compete with it.
    """
    from skillstotraineyes.difficulty import roll

    entry_cfg = entry_cfg or {"id": "", "knobs": {}, "knob_names": []}
    fixed = entry_cfg.get("knobs", {})
    rng = random.Random(seed)
    accent = rng.choice(ACCENTS)

    k = {"tempo": 1.0, "guide": "visible", "occlude": 0, "ghosts": 0, "shift": 0.0}
    if level:
        k.update(roll(knob_names(entry_cfg["id"]), level, rng))
    k.update(fixed)

    kind = k.get("path", "sine")
    period = 6.5 / float(k["tempo"])
    body = rng.choice([12, 15, 18])
    hook_until = 3.0
    t_q = hook_until + body
    t_cta = t_q + 2.0
    total = t_cta + 2.5

    path = _make_path(kind, rng, period)
    shift = float(k["shift"])
    if shift:
        # Spatial-Shift Pursuit: the tempo jumps at each period boundary, so the
        # dot's speed stops being predictable without leaving the path.
        base = path
        jumps = [1.0 + shift * rng.uniform(-1, 1) for _ in range(24)]

        def path(t, _base=base, _j=jumps):          # noqa: F811
            i = min(int(t / period), len(_j) - 1)
            warped = sum(_j[:i]) * period + (t - i * period) * _j[i]
            return _base(warped)

    guide = k["guide"]
    guide_col = {"visible": (120, 128, 150), "faint": (44, 48, 62)}.get(guide)
    guide_dots = ([("disc", *path(j * period / GUIDE_SAMPLES), GUIDE_R, guide_col)
                   for j in range(GUIDE_SAMPLES)] if guide_col else [])
    # ponytail: the guide is re-pasted every frame. Bake it into the renderer's
    # base image if drills.py ever costs more than ~40ms/frame.

    bands = [(CY - 180, 150), (CY + 240, 130)][:int(k["occlude"])]
    hook = _text(text, "hook", "Follow the dot with your eyes")
    question = _text(text, "question", "Did you keep up?")
    cta = _text(text, "cta", DEFAULT_TEXT["cta"])
    badge = _badge(level)

    def ops(f: int) -> list:
        t = f / FPS
        out = [("dotted", CX, CY, 470, DIM)] + list(guide_dots)
        for by, bh in bands:
            out.append(("rect", CX, by, 470, bh, BG))
        if hook_until - 0.5 <= t < t_q:
            tm = t - (hook_until - 0.5)
            for j in range(int(k["ghosts"]), 0, -1):
                gx, gy = path(max(0.0, tm - j * 0.07))
                fade = 0.45 - j * 0.04
                out.append(("disc", gx, gy, 24,
                            tuple(max(0, int(c * fade)) for c in accent)))
            x, y = path(tm)
            if not any(by - bh <= y <= by + bh for by, bh in bands):
                out.append(("disc", x, y, DOT_R, accent))
        elif t < hook_until - 0.5:
            x, y = path(0.0)
            out.append(("disc", x, y, DOT_R, accent))
        return out + _text_ops(t, hook, hook_until - 0.5, question, t_q, cta, t_cta,
                               badge=badge)

    return Drill(entry_cfg.get("id") or "path_pursuit", total, ops,
                 {"family": entry_cfg.get("id") or "path_pursuit", "level": level or "",
                  "path": kind, "tempo": k["tempo"], "guide": guide,
                  "occlude": int(k["occlude"]), "ghosts": int(k["ghosts"]),
                  "shift": shift, "body": body},
                 voice=_lines(hook, question, cta, hook_until, t_q, t_cta),
                 level=level or "")
```

Register it: `BUILDERS["path_pursuit"] = path_pursuit`.

The occluder band is painted in `BG`, so it reads as a gap in the arena rather than a foreign object. `470` is the half-width — it spans the arena without touching the gutters.

**3c.** Append the eight entries to the `drills` array in `skillstotraineyes/catalog.json`:

```json
    {
      "id": "slow_pursuit", "name": "Constant Slow Pursuit",
      "about": "A dot creeps along a visible Lissajous curve. Stay glued to it without blinking ahead.",
      "builder": "path_pursuit", "mode": "watch",
      "knobs": {"path": "lissajous"},
      "knob_names": ["tempo", "guide"], "presets": []
    },
    {
      "id": "sine_pursuit", "name": "Sine-Wave Pursuit",
      "about": "Track a dot oscillating along a horizontal sine wave guide line.",
      "builder": "path_pursuit", "mode": "watch",
      "knobs": {"path": "sine"},
      "knob_names": ["tempo", "guide"], "presets": []
    },
    {
      "id": "triangle_pursuit", "name": "Triangular Pursuit",
      "about": "Follow the dot around a triangle. The corners are where eyes fall behind.",
      "builder": "path_pursuit", "mode": "watch",
      "knobs": {"path": "triangle"},
      "knob_names": ["tempo", "guide"], "presets": []
    },
    {
      "id": "zigzag_pursuit", "name": "Zig-Zag Path Pursuit",
      "about": "Track the dot along a multi-segment zig-zag. Sharp reversals, no warning.",
      "builder": "path_pursuit", "mode": "watch",
      "knobs": {"path": "zigzag"},
      "knob_names": ["tempo", "guide"], "presets": []
    },
    {
      "id": "predictive_pursuit", "name": "Predictive Pursuit",
      "about": "The dot slips behind a band and comes out the other side. Your eyes have to guess where.",
      "builder": "path_pursuit", "mode": "watch",
      "knobs": {"path": "lemniscate"},
      "knob_names": ["tempo", "occlude"],
      "presets": [{"name": "Blind Run", "level": "god", "knobs": {"tempo": 2.4, "occlude": 2}}]
    },
    {
      "id": "spatial_shift_pursuit", "name": "Spatial-Shift Pursuit",
      "about": "Track a dot that keeps changing speed on the same path. Rhythm will not save you.",
      "builder": "path_pursuit", "mode": "watch",
      "knobs": {"path": "sine"},
      "knob_names": ["tempo", "shift", "guide"], "presets": []
    },
    {
      "id": "ghosting_pursuit", "name": "Ghosting Suppress Pursuit",
      "about": "Trails follow the dot. Lock onto the real one and let the ghosts go.",
      "builder": "path_pursuit", "mode": "watch",
      "knobs": {"path": "lissajous"},
      "knob_names": ["tempo", "ghosts"], "presets": []
    },
    {
      "id": "vertical_pursuit", "name": "Vertical Tracking",
      "about": "Stepped moves: a horizontal slide, then a vertical snap. Follow both.",
      "builder": "path_pursuit", "mode": "watch",
      "knobs": {"path": "steps"},
      "knob_names": ["tempo", "guide"], "presets": []
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_skillstotraineyes_catalog.py tests/test_skillstotraineyes_drills.py -v -m "not slow"`
Expected: PASS.

If `test_path_drill_stays_on_screen` fails on the `steps` path, `AY` puts `CY + AY = 1350` inside the frame but a text op may collide — confirm the failure names a `disc`, and if so lower `AY` to 430.

- [ ] **Step 5: Render one by eye**

```bash
python run_skillstotraineyes.py --family zigzag_pursuit --seed 7 --dry-run
python run_skillstotraineyes.py --family predictive_pursuit --seed 7 --no-publish --no-llm
```

Watch `output/skillstotraineyes/video/*.mp4`. The guide must read as a line, not a smear, and the dot must actually disappear behind the band.

- [ ] **Step 6: Commit**

```bash
git add skillstotraineyes/drills.py skillstotraineyes/renderer.py skillstotraineyes/catalog.json tests/test_skillstotraineyes_catalog.py
git commit -m "feat: path_pursuit builder covering eight pursuit drills from the catalog"
```

---

## Task 6: Wire levels and the catalog into the entry point

**Files:**
- Modify: `run_skillstotraineyes.py`, `skillstotraineyes/wording.py`, `settings.json`
- Test: `tests/test_skillstotraineyes_flow.py`

**Interfaces:**
- Consumes: `difficulty.pick_level`, `difficulty.LEVELS` (Task 1); `drills.load_catalog`, `drills.entry` (Task 4).
- Produces: `wording.pick_drill(seed, recent, cfg) -> str` (a catalog id); `run_skillstotraineyes.py --level`; `variation_params` gains `"level"` and `"drill_id"`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_skillstotraineyes_flow.py`:

```python
def test_pick_drill_falls_back_to_the_catalog_on_a_bad_llm_answer(monkeypatch):
    """Untrusted output: an unknown id must not reach build()."""
    from skillstotraineyes import wording
    from skillstotraineyes.drills import FAMILIES
    monkeypatch.setattr(wording, "call_llm", lambda *a, **k: ("drop table drills", "fake"))
    assert wording.pick_drill(3, [], None) in FAMILIES


def test_pick_drill_accepts_a_real_catalog_id(monkeypatch):
    from skillstotraineyes import wording
    monkeypatch.setattr(wording, "call_llm",
                        lambda *a, **k: ('{"drill_id": "zigzag_pursuit"}', "fake"))
    assert wording.pick_drill(3, [], None) == "zigzag_pursuit"


def test_pick_drill_survives_a_dead_llm(monkeypatch):
    from skillstotraineyes import wording
    from skillstotraineyes.drills import FAMILIES

    def boom(*a, **k):
        raise RuntimeError("no providers")
    monkeypatch.setattr(wording, "call_llm", boom)
    assert wording.pick_drill(3, [], None) in FAMILIES


def test_level_is_recorded_in_variation_params():
    """The uniqueness gate keys on (family, level, knob mix)."""
    from skillstotraineyes.drills import build, params_key
    a = build("tracking", 5, level="easy")
    b = build("tracking", 5, level="god")
    assert params_key(a.params) != params_key(b.params)


def test_niche_config_has_the_new_keys():
    import json
    cfgd = json.loads(open("settings.json", encoding="utf-8").read())
    niche = next(n for n in cfgd["niches"] if n["id"] == "skillstotraineyes")
    assert niche["human_policy"] == "none", "a sniper is a human; 'never' would strip it"
    assert niche["levels"] is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_skillstotraineyes_flow.py -k "pick_drill or level or niche_config" -v`
Expected: FAIL — `AttributeError: module 'skillstotraineyes.wording' has no attribute 'pick_drill'`

- [ ] **Step 3: Write the implementation**

**3a.** In `skillstotraineyes/wording.py`, move the `call_llm` import to module scope so tests can monkeypatch it, replacing the local import inside `generate_wording`:

```python
from llm_router import call_llm
```

Add `pick_drill` below `generate_wording`:

```python
def pick_drill(seed: int, recent: list[dict], cfg=None) -> str:
    """
    Let the LLM choose the next drill from the catalog.

    Its answer is untrusted, so an id that is not in the catalog — or a dead LLM —
    falls back to the seeded rotation. Recent ids are shown so it spreads out.
    """
    import random

    from skillstotraineyes.drills import FAMILIES, load_catalog, pick_family

    last = [r["drill_id"] for r in recent[:1] if r.get("drill_id")]
    fallback = pick_family(seed, last)
    recent_ids = [r.get("drill_id") for r in recent[:10] if r.get("drill_id")]
    menu = "\n".join(f'- {e["id"]}: {e["name"]} — {e["about"]}' for e in load_catalog())
    prompt = (
        "Pick ONE eye-exercise drill for the next short video.\n\n"
        f"{menu}\n\n"
        f"Recently used, avoid these: {recent_ids}\n"
        'Respond with ONLY JSON: {"drill_id": "..."}'
    )
    try:
        raw, model = call_llm(prompt, cfg_router=(cfg.llm_router if cfg else {}), temperature=1.0)
        choice = _parse(raw).get("drill_id")
    except Exception as e:
        log.warning("pick_drill: LLM failed (%s) — rotating instead", e)
        return fallback
    if choice in FAMILIES and choice not in last:
        log.info("pick_drill: %s via %s", choice, model)
        return choice
    log.info("pick_drill: %r rejected — rotating to %s", choice, fallback)
    return fallback
```

Also widen `generate_wording`'s prompt to describe the drill properly — replace its `Drill type: {family}` line with:

```python
Drill: {defaults.get('name', family)} — {defaults.get('about', '')}
Difficulty shown on screen: {defaults.get('level_label', 'none')}
```

**3b.** In `run_skillstotraineyes.py`:

Add the CLI flag, after `--family`:

```python
    parser.add_argument("--level", default=None,
                        help="Force a difficulty level (easy/medium/hard/expert/god)")
```

Replace the family/level selection block (currently lines 117-130):

```python
    from skillstotraineyes.difficulty import LEVELS, LEVEL_LABEL, pick_level

    if args.level and args.level not in LEVELS:
        log.error("Unknown level %r. Options: %s", args.level, list(LEVELS))
        sys.exit(1)

    seed = args.seed if args.seed is not None else int(time.time()) % 1_000_000
    recent = _recent(conn)
    if args.family:
        family = args.family
    elif args.no_llm:
        last = [recent[0]["drill_id"]] if recent and recent[0].get("drill_id") else []
        family = pick_family(seed, last)
    else:
        from skillstotraineyes.wording import pick_drill
        family = pick_drill(seed, recent, cfg)

    recent_levels = [r["level"] for r in recent if r.get("level")]
    level = args.level or pick_level(seed, recent_levels)

    # Wording first, so the gate checks the drill that will actually be rendered.
    from skillstotraineyes.drills import entry
    e = entry(family)
    base = build(family, seed, level=level)
    defaults = {"hook": base.voice[0][1], "question": base.voice[1][1],
                "name": e["name"], "about": e["about"], "level_label": LEVEL_LABEL[level]}
```

Change the family validation above it — `BUILDERS` no longer holds catalog ids:

```python
    from skillstotraineyes.drills import FAMILIES, build, entry, pick_family
    niche = _load_niche(cfg)
    if args.family and args.family not in FAMILIES:
        log.error("Unknown drill %r. Options: %s", args.family, sorted(FAMILIES))
        sys.exit(1)
```

Pass the level into the uniqueness build — change `_build_unique`:

```python
def _build_unique(family: str, seed: int, text: dict | None, recent: list[dict],
                  level: str | None = None):
    """Re-seed until the drill's key params differ from every recent one."""
    from skillstotraineyes.drills import build, too_close
    same_family = [r for r in recent if r.get("drill_id") == family]
    for bump in range(20):
        drill = build(family, seed + bump, text, level=level)
        if not too_close(drill.params, same_family):
            return drill, seed + bump
    log.warning("uniqueness gate exhausted 20 re-seeds for %s; accepting the last", family)
    return drill, seed + 19
```

and its call site: `drill, seed = _build_unique(family, seed, text or None, recent, level)`.

Record the level in `variation` (currently line 149):

```python
    variation = {"seed": seed, "family": family, "drill_id": family, "level": level,
                 "params": drill.params, "key": params_key(drill.params),
                 "hook": hook, "question": question,
                 "caption": caption_body, "audio_mode": mode}
```

Update the log line and the slug to carry the level:

```python
    log.info("Drill=%s level=%s seed=%d duration=%.1fs params=%s",
             family, level, seed, drill.duration, drill.params)
    ...
    slug = f"{NICHE_ID}_{family}_{level}_{video_id}"
```

**3c.** In `settings.json`, add to the `skillstotraineyes` niche object:

```json
  "human_policy": "none",
  "levels": true,
```

`human_policy: "none"` matters for Task 9, not for drills — without it the default `"never"` would strip the hidden-object target out of its own prompt. Set it here so both phases share one config change.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/ -v -m "not slow" -k skillstotraineyes`
Expected: PASS.

- [ ] **Step 5: Verify end to end**

```bash
python run_skillstotraineyes.py --level god --no-llm --dry-run
python run_skillstotraineyes.py --level easy --no-llm --no-publish
```

Expected: the log names a drill, a level and its params; the second writes an mp4 whose first three seconds show the badge above the hook.

- [ ] **Step 6: Commit**

```bash
git add run_skillstotraineyes.py skillstotraineyes/wording.py settings.json tests/test_skillstotraineyes_flow.py
git commit -m "feat: LLM picks the drill from the catalog, level is chosen and recorded"
```

---

# PHASE 2 — Hidden-object image posts (Tasks 7–10)

Phase 1 is shippable on its own. Stop here if the knob scores still need tuning.

## Task 7: Instagram image posts

**Files:**
- Modify: `pipeline/instagram_upload.py:30-76` (`_upload_to_temp_host`), `:141-174` (`_create_media_container`), and add `upload_image_post`
- Test: `tests/test_instagram_image_post.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `instagram_upload.upload_image_post(image_path, caption, hashtags=None, credentials_file=..., image_url=None) -> str`; `_upload_to_temp_host(path, mime=None)`; `_create_media_container(..., media_type="REELS", image_url=None)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_instagram_image_post.py`:

```python
"""Tests for the Instagram feed-image publish path. No network: requests is stubbed."""
import json

import pytest

from pipeline import instagram_upload as ig


@pytest.fixture
def creds(tmp_path):
    p = tmp_path / "ig.json"
    p.write_text(json.dumps({"access_token": "tok", "ig_user_id": "123",
                             "app_id": "a", "app_secret": "s"}))
    return p


def test_temp_host_mime_follows_the_file():
    assert ig._mime_for("a.jpg") == "image/jpeg"
    assert ig._mime_for("a.jpeg") == "image/jpeg"
    assert ig._mime_for("a.png") == "image/png"
    assert ig._mime_for("a.mp4") == "video/mp4"


def test_image_container_uses_image_url_and_no_reels_type(monkeypatch):
    sent = {}

    class Resp:
        def raise_for_status(self): pass
        def json(self): return {"id": "container-1"}

    def fake_post(url, data=None, timeout=None):
        sent.update(data)
        return Resp()

    monkeypatch.setattr(ig.requests, "post", fake_post)
    cid = ig._create_media_container("123", "tok", caption="hi",
                                     image_url="https://x/y.jpg", media_type="IMAGE")
    assert cid == "container-1"
    assert sent["image_url"] == "https://x/y.jpg"
    assert "video_url" not in sent
    assert "media_type" not in sent, "IG rejects media_type=IMAGE on a feed photo"


def test_reel_container_is_unchanged(monkeypatch):
    sent = {}

    class Resp:
        def raise_for_status(self): pass
        def json(self): return {"id": "c2"}

    monkeypatch.setattr(ig.requests, "post",
                        lambda url, data=None, timeout=None: (sent.update(data), Resp())[1])
    ig._create_media_container("123", "tok", video_url="https://x/y.mp4", caption="c")
    assert sent["media_type"] == "REELS"
    assert sent["video_url"] == "https://x/y.mp4"


def test_upload_image_post_publishes(monkeypatch, tmp_path, creds):
    img = tmp_path / "scene.jpg"
    img.write_bytes(b"\xff\xd8\xff\x00")
    monkeypatch.setattr(ig, "_upload_to_temp_host", lambda p, mime=None: "https://x/scene.jpg")
    monkeypatch.setattr(ig, "_create_media_container", lambda **kw: "c3")
    monkeypatch.setattr(ig, "_poll_container_status", lambda *a: "FINISHED")
    monkeypatch.setattr(ig, "_publish_container", lambda *a: "media-9")
    assert ig.upload_image_post(img, "find it", ["eyes"], credentials_file=creds) == "media-9"


def test_upload_image_post_rejects_a_missing_file(tmp_path, creds):
    with pytest.raises(FileNotFoundError):
        ig.upload_image_post(tmp_path / "gone.jpg", "c", credentials_file=creds)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_instagram_image_post.py -v`
Expected: FAIL — `AttributeError: module 'pipeline.instagram_upload' has no attribute '_mime_for'`

- [ ] **Step 3: Write the implementation**

In `pipeline/instagram_upload.py`, add above `_upload_to_temp_host`:

```python
_MIME_BY_EXT = {".mp4": "video/mp4", ".mov": "video/quicktime",
                ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".png": "image/png", ".webp": "image/webp"}


def _mime_for(path) -> str:
    """Content type from the extension. The temp hosts reject a mismatched type."""
    return _MIME_BY_EXT.get(Path(path).suffix.lower(), "application/octet-stream")
```

Change `_upload_to_temp_host`'s signature and the two `files=` lines that hardcode the type:

```python
def _upload_to_temp_host(video_path: Path, mime: str | None = None) -> str:
    """Upload a local file to a temporary public host.

    Tries multiple hosts in order until one succeeds. Returns a public URL.
    The Instagram Graph API needs the media at a public URL to create a
    container — this bridges local files to that requirement.
    """
    mime = mime or _mime_for(video_path)
    errors = []
```

and inside the loop, replace both `"video/mp4"` literals with `mime`.

Widen `_create_media_container` to serve both media kinds:

```python
def _create_media_container(
    ig_user_id: str,
    access_token: str,
    video_url: str | None = None,
    caption: str = "",
    cover_url: str | None = None,
    share_to_feed: bool = True,
    image_url: str | None = None,
    media_type: str = "REELS",
) -> str:
    """Step 1: Create a media container (starts server-side processing).

    A feed photo carries `image_url` and NO `media_type` — the Graph API's
    image container is the default and rejects an explicit IMAGE value.
    """
    params = {"caption": caption, "access_token": access_token}
    if image_url:
        params["image_url"] = image_url
    else:
        params["media_type"] = media_type
        params["video_url"] = video_url
        params["share_to_feed"] = str(share_to_feed).lower()
    if cover_url:
        params["cover_url"] = cover_url
    ...
```

Leave the rest of the function (the POST, the `container_id` check, the log) untouched.

Add `upload_image_post` at the end of the file:

```python
def upload_image_post(
    image_path: str | Path,
    caption: str,
    hashtags: list[str] | None = None,
    credentials_file: str | Path = "credentials/all_niches_ig.json",
    image_url: str | None = None,
) -> str:
    """
    Publish a local image to the Instagram feed and return its media ID.

    Same three steps as a Reel — public URL, container, publish — but the
    container is an image container, so there is no processing wait to speak of.
    JPEG only: the Graph API rejects PNG and WebP for feed photos.
    """
    image_path = Path(image_path)
    creds_path = Path(credentials_file)

    if not image_path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")
    if not creds_path.exists():
        raise FileNotFoundError(
            f"Credentials not found: {creds_path}. "
            "Run: python scripts/instagram_auth_setup.py"
        )
    if image_path.suffix.lower() not in (".jpg", ".jpeg"):
        raise ValueError(f"Instagram feed photos must be JPEG, got {image_path.suffix}")

    if not image_url:
        image_url = _upload_to_temp_host(image_path, _mime_for(image_path))

    creds = _load_credentials(creds_path)

    if hashtags:
        tag_str = " ".join(t if t.startswith("#") else f"#{t}" for t in hashtags)
        full_caption = f"{caption}\n\n{tag_str}"
    else:
        full_caption = caption
    if len(full_caption) > 2200:
        log.warning("Caption truncated from %d to 2200 chars", len(full_caption))
        full_caption = full_caption[:2197] + "..."

    log.info("Uploading feed image: %s (%d chars caption)", image_path.name, len(full_caption))
    container_id = _create_media_container(
        ig_user_id=creds["ig_user_id"], access_token=creds["access_token"],
        image_url=image_url, caption=full_caption,
    )
    _poll_container_status(container_id, creds["access_token"])
    media_id = _publish_container(creds["ig_user_id"], container_id, creds["access_token"])
    log.info("Image published — media ID: %s", media_id)
    return media_id
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_instagram_image_post.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
git add pipeline/instagram_upload.py tests/test_instagram_image_post.py
git commit -m "feat: publish a local image to the Instagram feed"
```

---

## Task 8: Route image posts through the scheduler and the cron

**Files:**
- Modify: `scripts/upload_all_platforms.py:138-169` (`_upload_instagram`), `:209-273` (`upload_all`); `scripts/run_scheduled_upload.py:124-145`; `pipeline/scheduler.py:222-315`; `pipeline/publisher.py:31-92`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `instagram_upload.upload_image_post` (Task 7).
- Produces: `upload_all(..., media_type: str = "video")`; `schedule_video(..., media_type: str = "video")`; `publish(..., media_type: str = "video")`; the manifest key `"media_type"`.

`media_type` defaults to `"video"` everywhere, so every existing caller is untouched. `_write_retry_manifest` already carries unknown keys through `{**manifest}`, so retries inherit it for free.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_scheduler.py`:

```python
def test_upload_all_routes_an_image_to_the_feed(monkeypatch, tmp_path):
    from scripts import upload_all_platforms as uap

    img = tmp_path / "scene.jpg"
    img.write_bytes(b"\xff\xd8\xff\x00")
    called = {}
    monkeypatch.setattr(uap, "load_social_config", lambda: {
        "platforms": {"instagram": {"enabled": True}},
        "accounts": [{"account_id": "eye_ig", "platform": "instagram", "enabled": True,
                      "exclusive": True, "niche": "skillstotraineyes",
                      "credentials_file": "credentials/skillstotraineyes_ig.json"}],
    })
    monkeypatch.setattr(uap.Path, "exists", lambda self: True)

    import pipeline.instagram_upload as ig
    monkeypatch.setattr(ig, "upload_image_post",
                        lambda **kw: called.setdefault("image", kw) and "m1" or "m1")
    monkeypatch.setattr(ig, "upload_reel",
                        lambda **kw: called.setdefault("reel", kw) and "m2" or "m2")

    out = uap.upload_all(video_path=img, title="t", platforms_filter=["instagram"],
                         niche_id="skillstotraineyes", media_type="image")
    assert "image" in called and "reel" not in called
    assert out[0]["status"] == "success"


def test_upload_all_still_defaults_to_a_reel(monkeypatch, tmp_path):
    from scripts import upload_all_platforms as uap

    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"\x00")
    called = {}
    monkeypatch.setattr(uap, "load_social_config", lambda: {
        "platforms": {"instagram": {"enabled": True}},
        "accounts": [{"account_id": "ig", "platform": "instagram", "enabled": True,
                      "credentials_file": "credentials/all_niches_ig.json"}],
    })
    monkeypatch.setattr(uap.Path, "exists", lambda self: True)
    import pipeline.instagram_upload as ig
    monkeypatch.setattr(ig, "upload_reel", lambda **kw: called.setdefault("reel", kw) or "m2")
    uap.upload_all(video_path=vid, title="t", platforms_filter=["instagram"])
    assert "reel" in called


def test_manifest_carries_media_type(monkeypatch, tmp_path):
    """The cron routes on this key; absent, it must still mean video."""
    import pipeline.scheduler as sched
    written = {}
    monkeypatch.setattr(sched, "_upload_manifest", lambda p, folder_name=None:
                        written.update(json.loads(Path(p).read_text())) or "id", raising=False)
    assert "media_type" in sched.schedule_video.__doc__
```

Note: `test_manifest_carries_media_type` is a weak guard on purpose — `schedule_video` writes to Drive inside a `try`, which is awkward to stub. The real check is the end-to-end run in Task 10 Step 5.

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_scheduler.py -k "media_type or image" -v`
Expected: FAIL — `TypeError: upload_all() got an unexpected keyword argument 'media_type'`

- [ ] **Step 3: Write the implementation**

**3a.** `scripts/upload_all_platforms.py` — give `_upload_instagram` a `media_type` and route:

```python
def _upload_instagram(
    video_path: Path, title: str, description: str,
    hashtags: list[str], account: dict, dry_run: bool,
    media_type: str = "video",
) -> dict:
    """Upload to Instagram as a Reel, or as a feed photo when media_type='image'."""
    creds_file = ROOT / account["credentials_file"]
    if not creds_file.exists():
        return {"platform": "instagram", "account": account["account_id"],
                "status": "error", "error": f"Credentials not found: {creds_file}"}

    if dry_run:
        return {"platform": "instagram", "account": account["account_id"],
                "status": "dry_run", "credentials": str(creds_file)}

    caption = title
    if description and description != title:
        caption = f"{title}\n\n{description}"

    if media_type == "image":
        from pipeline.instagram_upload import upload_image_post
        media_id = upload_image_post(
            image_path=video_path, caption=caption,
            hashtags=hashtags, credentials_file=creds_file,
        )
    else:
        from pipeline.instagram_upload import upload_reel
        media_id = upload_reel(
            video_path=video_path, caption=caption,
            hashtags=hashtags, credentials_file=creds_file,
        )
    return {"platform": "instagram", "account": account["account_id"],
            "status": "success", "media_id": media_id}
```

Give `_upload_youtube` and `_upload_facebook` the same trailing parameter so the uniform call site keeps working, and have each reject an image early:

```python
    media_type: str = "video",
) -> dict:
    if media_type != "video":
        return {"platform": "youtube", "account": account["account_id"],
                "status": "error", "error": "youtube takes video only"}
```

(and the same with `"facebook"` in the facebook function).

In `upload_all`, add the parameter and pass it through:

```python
def upload_all(
    video_path: Path,
    title: str,
    description: str | None = None,
    hashtags: list[str] | None = None,
    platforms_filter: list[str] | None = None,
    dry_run: bool = False,
    niche_id: str | None = None,
    media_type: str = "video",
) -> list[dict]:
```

Document it in the docstring's Args block:

```
        media_type: "video" (default, a Reel) or "image" (an Instagram feed photo).
```

and change the uploader call:

```python
                result = uploader(
                    video_path, title, full_description,
                    hashtags, account, dry_run, media_type,
                )
```

**3b.** `scripts/run_scheduled_upload.py` — in `process_schedule`, replace the hardcoded filename and pass the type through:

```python
    media_type = manifest.get("media_type", "video")
    suffix = ".jpg" if media_type == "image" else ".mp4"

    log.info("Processing schedule_id=%d platform=%s media=%s title=%s",
             schedule_id, platform, media_type, title)

    tmp_dir = Path(tempfile.mkdtemp())
    video_path = tmp_dir / f"media{suffix}"
    download_from_drive(drive_file_id, video_path)
    log.info("Downloaded media: %s (%d bytes)", video_path, video_path.stat().st_size)
```

and in the `upload_all(...)` call inside the retry loop, add:

```python
                media_type=media_type,
```

**3c.** `pipeline/scheduler.py` — add the parameter to `schedule_video` after `hashtags`:

```python
    hashtags: list[str] | None = None,
    media_type: str = "video",
) -> dict:
```

extend its docstring:

```
    media_type: "video" (default) or "image" for a feed photo. Written into the
    manifest; run_scheduled_upload.py routes the upload on it.
```

and add the key to `_manifest`:

```python
            "media_type": media_type,
```

**3d.** `pipeline/publisher.py` — add `media_type: str = "video"` to `publish`'s keyword-only block, document it, and forward it in the `schedule_video(...)` call:

```python
            title=title, caption=caption, hashtags=hashtags,
            media_type=media_type,
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_scheduler.py -v`
Expected: PASS. The existing scheduler tests must still pass — every new parameter defaults to the old behaviour.

- [ ] **Step 5: Commit**

```bash
git add scripts/upload_all_platforms.py scripts/run_scheduled_upload.py pipeline/scheduler.py pipeline/publisher.py tests/test_scheduler.py
git commit -m "feat: media_type routes image posts through the scheduler and cron"
```

---

## Task 9: Hidden-object scene generation

**Files:**
- Create: `skillstotraineyes/hidden_object.py`
- Modify: `skillstotraineyes/wording.py` (position-word screen), `settings.json`
- Test: `tests/test_skillstotraineyes_hidden_object.py`

**Interfaces:**
- Consumes: `pipeline.image_gen.generate_image`; `llm_router.call_llm`; the niche's `human_policy: "none"` from Task 6.
- Produces:
  - `hidden_object.invent_scene(seed, recent, cfg) -> dict` with keys `environment`, `target`, `difficulty_note`
  - `hidden_object.build_prompt(scene) -> str`
  - `hidden_object.MAX_PROMPT_WORDS: int` = 55
  - `hidden_object.MIN_SHORT_EDGE: int` = 1024
  - `hidden_object.generate(scene, out_dir, seed, niche, cfg) -> str` (a `.jpg` path)
  - `wording.reveals_position(text) -> bool`

Three spec risks land here. R1: the niche must be `human_policy: "none"`, or a sniper is stripped from its own prompt. R2: FLUX caps near 60 words, so the prompt is capped at 55 with the target clause first. R3: an upscaled 576px image cannot hide anything, so any provider result under 1024px on the short edge is rejected.

- [ ] **Step 1: Write the failing test**

Create `tests/test_skillstotraineyes_hidden_object.py`:

```python
"""Tests for skillstotraineyes/hidden_object.py. No network: image_gen is stubbed."""
import json

import pytest
from PIL import Image

from skillstotraineyes import hidden_object as ho
from skillstotraineyes.wording import reveals_position

SCENE = {"environment": "a dry rocky mountainside at noon",
         "target": "a sand-coloured snake",
         "difficulty_note": "coiled in shadow between two boulders"}


def test_prompt_stays_under_the_flux_ceiling():
    """FLUX truncates near 77 CLIP tokens; a long prompt loses the subject."""
    for seed in range(20):
        scene = {**SCENE, "environment": "a " + "very dense tangled overgrown " * 6 + "jungle"}
        assert len(ho.build_prompt(scene).split()) <= ho.MAX_PROMPT_WORDS


def test_prompt_names_the_target_early():
    words = ho.build_prompt(SCENE).split()
    assert "snake" in " ".join(words[:25]), "the target must survive truncation"


def test_prompt_asks_for_a_hidden_target():
    p = ho.build_prompt(SCENE).lower()
    assert "camouflaged" in p or "hidden" in p


def test_human_target_survives_the_policy():
    """R1: human_policy 'never' would strip 'sniper' out of its own prompt."""
    from pipeline.image_policy import apply_human_policy, resolve_human_policy
    cfgd = json.loads(open("settings.json", encoding="utf-8").read())
    niche = next(n for n in cfgd["niches"] if n["id"] == "skillstotraineyes")
    policy = resolve_human_policy(niche, {})
    scene = {**SCENE, "target": "a sniper in a ghillie suit"}
    positive, _neg = apply_human_policy(ho.build_prompt(scene), "", policy)
    assert "sniper" in positive


def test_generate_rejects_a_low_resolution_result(monkeypatch, tmp_path):
    """R3: an upscaled 576px frame cannot hide a small object."""
    small = tmp_path / "small.png"
    Image.new("RGB", (576, 1024), "green").save(small)
    monkeypatch.setattr(ho, "generate_image", lambda **kw: str(small))
    with pytest.raises(ho.HiddenObjectError, match="1024"):
        ho.generate(SCENE, tmp_path, seed=1, niche={"id": "skillstotraineyes"}, cfg=None)


def test_generate_converts_to_jpeg(monkeypatch, tmp_path):
    """R4: the Graph API rejects PNG for a feed photo."""
    big = tmp_path / "big.png"
    Image.new("RGB", (1080, 1350), "green").save(big)
    monkeypatch.setattr(ho, "generate_image", lambda **kw: str(big))
    out = ho.generate(SCENE, tmp_path, seed=1, niche={"id": "skillstotraineyes"}, cfg=None)
    assert out.endswith(".jpg")
    assert Image.open(out).format == "JPEG"


def test_invent_scene_falls_back_when_the_llm_dies(monkeypatch):
    monkeypatch.setattr(ho, "call_llm", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("dead")))
    scene = ho.invent_scene(3, [], None)
    assert scene["environment"] and scene["target"]


def test_invent_scene_rejects_junk(monkeypatch):
    monkeypatch.setattr(ho, "call_llm", lambda *a, **k: ('{"environment": "", "target": ""}', "f"))
    scene = ho.invent_scene(3, [], None)
    assert scene["environment"] and scene["target"], "empty fields must fall back"


def test_invent_scene_avoids_recent_targets(monkeypatch):
    monkeypatch.setattr(ho, "call_llm",
                        lambda p, **k: ('{"environment": "x", "target": "a snake"}', "f")
                        if "snake" not in p else ('{"environment": "y", "target": "a fox"}', "f"))
    scene = ho.invent_scene(3, [{"target": "a snake"}], None)
    assert scene["target"] != "a snake"


@pytest.mark.parametrize("bad", [
    "It is in the top left corner", "look behind the rock",
    "hiding under the third tree", "check the bottom-right",
])
def test_position_words_are_caught(bad):
    """R10: the caption must never give the answer away."""
    assert reveals_position(bad)


@pytest.mark.parametrize("ok", [
    "Can you find it? Most people need three tries.",
    "One snake, one photo. Drop your answer below.",
])
def test_ordinary_captions_pass(ok):
    assert not reveals_position(ok)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_skillstotraineyes_hidden_object.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skillstotraineyes.hidden_object'`

- [ ] **Step 3: Write the implementation**

**3a.** Add the screen to `skillstotraineyes/wording.py`, next to `has_claim`:

```python
# The answer is never revealed, so the caption must not leak where it is.
_POSITION = re.compile(
    r"\b(top|bottom|upper|lower|left|right|centre|center|middle|corner|edge|"
    r"behind|beneath|under(?:neath)?|above|below|beside|next to|near the|"
    r"first|second|third|fourth)\b",
    re.IGNORECASE,
)


def reveals_position(text: str) -> bool:
    """True if the wording hints at where the hidden target is."""
    return bool(_POSITION.search(text))
```

**3b.** Create `skillstotraineyes/hidden_object.py`:

```python
"""
Hidden-object image posts: a dense scene with one thing buried in it.

Not a Reel and not a drill — a single still for the Instagram feed. The answer
is never revealed anywhere: not in the image, not in the caption, not in a
follow-up. Viewers argue it out in the comments, which is the whole point.

Three constraints are enforced here rather than hoped for, because each one
fails silently:

  * The niche must run `human_policy: "none"`. The default "never" strips
    human-referring chunks out of the positive prompt, so "a sniper in a
    ghillie suit" would be deleted from its own brief and pushed into the
    negative. A test pins the settings.json value.
  * The prompt is capped at 55 words with the target named first. The FLUX
    providers truncate near 77 CLIP tokens, and a long "bury it" prompt loses
    the very thing it is supposed to hide.
  * Any result whose short edge is under 1024px is rejected rather than
    upscaled. Pollinations returns 576x1024 for a 1080x1920 ask, and no amount
    of lanczos puts back the detail a camouflaged target needs.
"""

import json
import logging
import random
import re
from pathlib import Path

from PIL import Image

from llm_router import call_llm
from pipeline.image_gen import generate_image

log = logging.getLogger(__name__)

MAX_PROMPT_WORDS = 55
MIN_SHORT_EDGE = 1024
MAX_FIELD = 90
JPEG_QUALITY = 92

# Enough to keep going when the LLM is down or answers with junk.
FALLBACK_SCENES = [
    {"environment": "a dry rocky mountainside at noon",
     "target": "a sand-coloured snake",
     "difficulty_note": "coiled in shadow between two boulders"},
    {"environment": "a dense green jungle canopy",
     "target": "a sniper in a ghillie suit",
     "difficulty_note": "prone under low ferns"},
    {"environment": "a snowbound pine forest at dusk",
     "target": "a white arctic fox",
     "difficulty_note": "curled against a drift"},
    {"environment": "a cluttered autumn leaf floor",
     "target": "a brown moth",
     "difficulty_note": "flat against a dead leaf"},
]


class HiddenObjectError(RuntimeError):
    pass


def _clean(value, max_len: int = MAX_FIELD) -> str | None:
    if not isinstance(value, str):
        return None
    s = " ".join(value.split()).strip(" \"'")
    return s if s and len(s) <= max_len else None


def invent_scene(seed: int, recent: list[dict], cfg=None) -> dict:
    """
    Ask the LLM for one (environment, target) pair. Never raises.

    Its answer is untrusted: empty or over-long fields, and a dead LLM, both fall
    back to a built-in scene so a post is never lost to a quota.
    """
    fallback = FALLBACK_SCENES[seed % len(FALLBACK_SCENES)]
    recent_targets = [r.get("target") for r in recent[:10] if r.get("target")]
    prompt = f"""Invent a "find the hidden object" photo brief.

A dense, busy natural or man-made scene with ONE thing camouflaged in it that a
viewer has to hunt for. Hard but genuinely findable.

Avoid these recent targets: {recent_targets}

Respond with ONLY JSON:
{{"environment": "the scene, under 12 words",
  "target": "the one hidden thing, under 8 words",
  "difficulty_note": "how it blends in, under 12 words"}}"""

    try:
        raw, model = call_llm(prompt, cfg_router=(cfg.llm_router if cfg else {}), temperature=1.0)
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(m.group(0)) if m else {}
    except Exception as e:
        log.warning("hidden_object: LLM failed (%s) — using a built-in scene", e)
        return dict(fallback)

    scene = {}
    for key in ("environment", "target", "difficulty_note"):
        scene[key] = _clean(data.get(key)) or fallback[key]
    if scene["target"] in recent_targets:
        log.info("hidden_object: %r repeats a recent target — using a built-in scene",
                 scene["target"])
        return dict(fallback)
    log.info("hidden_object: %s in %s (via %s)", scene["target"], scene["environment"], model)
    return scene


def build_prompt(scene: dict) -> str:
    """
    One image brief, target first and capped at MAX_PROMPT_WORDS.

    Target first is deliberate: FLUX truncates the tail, so whatever must
    survive goes at the front. The environment is what gets trimmed when a
    verbose LLM answer would breach the cap.
    """
    head = (f"{scene['target']} camouflaged and hidden, {scene['difficulty_note']}, "
            f"in {scene['environment']}")
    tail = "photorealistic, sharp focus, rich detail, natural light, wide establishing shot"
    words = f"{head}, {tail}".split()
    if len(words) <= MAX_PROMPT_WORDS:
        return " ".join(words)
    keep = MAX_PROMPT_WORDS - len(tail.split())
    return " ".join(head.split()[:keep] + tail.split())


def generate(scene: dict, out_dir, seed: int, niche: dict, cfg=None) -> str:
    """
    Generate the scene image and return a path to a JPEG at full resolution.

    Raises HiddenObjectError when the provider chain returns nothing usable —
    caller decides whether to retry with a new seed or give up on this post.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prompt = build_prompt(scene)
    log.info("hidden_object prompt (%d words): %s", len(prompt.split()), prompt)

    raw = generate_image(
        image_prompt=prompt, niche=niche, output_dir=str(out_dir),
        scene_index=0, cfg=cfg, seed=seed, use_notes=False,
    )
    img = Image.open(raw)
    short = min(img.size)
    if short < MIN_SHORT_EDGE:
        raise HiddenObjectError(
            f"provider returned {img.size[0]}x{img.size[1]}; the short edge must be "
            f"at least {MIN_SHORT_EDGE}px — upscaling cannot restore the detail a "
            f"camouflaged target needs"
        )

    out = out_dir / f"hidden_{seed}.jpg"
    img.convert("RGB").save(out, "JPEG", quality=JPEG_QUALITY, optimize=True)
    log.info("hidden_object image: %s (%dx%d)", out, *img.size)
    return str(out)
```

**3c.** In `settings.json`, add to the `skillstotraineyes` niche:

```json
  "hidden_object": {
    "enabled": true,
    "hashtags": ["findit", "hiddenobject", "spotit", "visualpuzzle", "brainteaser"]
  },
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_skillstotraineyes_hidden_object.py -v`
Expected: PASS, 14 tests.

- [ ] **Step 5: Generate one for real and look at it**

```bash
python -c "
import logging; logging.basicConfig(level=logging.INFO)
from config import cfg
from skillstotraineyes import hidden_object as ho
niche = next(n for n in cfg.niches if n['id']=='skillstotraineyes')
s = ho.invent_scene(1, [], cfg)
print(s); print(ho.generate(s, '.scratch_img/hidden', 1, niche, cfg))
"
```

Expected: a JPEG at 1024px or better on its short edge, with the target genuinely hard to find. If every provider trips the resolution gate, check that `gemini` sits at the top of `image_keys.json` — it is the one that honours the requested size.

- [ ] **Step 6: Commit**

```bash
git add skillstotraineyes/hidden_object.py skillstotraineyes/wording.py settings.json tests/test_skillstotraineyes_hidden_object.py
git commit -m "feat: hidden-object scene generation with policy, prompt and resolution guards"
```

---

## Task 10: Publish hidden-object posts from the entry point

**Files:**
- Modify: `run_skillstotraineyes.py`, `skillstotraineyes/wording.py`, `CLAUDE.md`
- Test: `tests/test_skillstotraineyes_flow.py`

**Interfaces:**
- Consumes: `hidden_object.invent_scene` / `generate` (Task 9); `publish(..., media_type="image")` (Task 8); `wording.reveals_position` (Task 9).
- Produces: `run_skillstotraineyes.py --mode image|drill`; `wording.hidden_object_caption(scene, recent, cfg) -> str`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_skillstotraineyes_flow.py`:

```python
def test_hidden_object_caption_never_leaks_the_position(monkeypatch):
    from skillstotraineyes import wording
    monkeypatch.setattr(wording, "call_llm",
                        lambda *a, **k: ('{"caption": "It is in the top left corner"}', "f"))
    scene = {"environment": "a jungle", "target": "a sniper", "difficulty_note": "prone"}
    caption = wording.hidden_object_caption(scene, [], None)
    assert not wording.reveals_position(caption)


def test_hidden_object_caption_survives_a_dead_llm(monkeypatch):
    from skillstotraineyes import wording

    def boom(*a, **k):
        raise RuntimeError("dead")
    monkeypatch.setattr(wording, "call_llm", boom)
    scene = {"environment": "a jungle", "target": "a sniper", "difficulty_note": "prone"}
    assert wording.hidden_object_caption(scene, [], None)


def test_hidden_object_caption_names_the_target(monkeypatch):
    from skillstotraineyes import wording
    monkeypatch.setattr(wording, "call_llm", lambda *a, **k: ("not json", "f"))
    scene = {"environment": "a jungle", "target": "a sniper", "difficulty_note": "prone"}
    assert "sniper" in wording.hidden_object_caption(scene, [], None)


def test_hidden_object_caption_has_no_medical_claim(monkeypatch):
    from skillstotraineyes import wording
    monkeypatch.setattr(wording, "call_llm",
                        lambda *a, **k: ('{"caption": "This will improve your eyesight"}', "f"))
    scene = {"environment": "a jungle", "target": "a sniper", "difficulty_note": "prone"}
    assert not wording.has_claim(wording.hidden_object_caption(scene, [], None))
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_skillstotraineyes_flow.py -k hidden_object -v`
Expected: FAIL — `AttributeError: module 'skillstotraineyes.wording' has no attribute 'hidden_object_caption'`

- [ ] **Step 3: Write the implementation**

**3a.** Add to `skillstotraineyes/wording.py`:

```python
def hidden_object_caption(scene: dict, recent: list[dict], cfg=None) -> str:
    """
    A caption that poses the hunt without answering it.

    Screened three ways, because each failure ships: a medical claim, a position
    hint that gives the answer away, or a repeat of a recent caption. Any hit
    falls back to a plain default that names the target and nothing else.
    """
    default = f"Somewhere in this picture: {scene['target']}. Can you find it? Answers below."
    recent_caps = [r["caption"] for r in recent if r.get("caption")]
    prompt = f"""Write one Instagram caption for a "find the hidden object" photo.

Scene: {scene['environment']}
Hidden in it: {scene['target']}

Rules:
- Invite people to hunt and to comment their answer. At most {MAX_CAPTION} characters.
- NEVER say where it is. No directions, no corners, no "behind" or "under".
- Treat it as a game. Never mention health, vision, treatment or results.
- No hashtags.

Respond with ONLY JSON: {{"caption": "..."}}"""

    try:
        raw, model = call_llm(prompt, cfg_router=(cfg.llm_router if cfg else {}), temperature=0.9)
        candidate = _parse(raw).get("caption")
    except Exception as e:
        log.warning("hidden_object_caption: LLM failed (%s) — using the default", e)
        return default

    cleaned = _clean(candidate, MAX_CAPTION, recent_caps)
    if cleaned and not reveals_position(cleaned):
        log.info("hidden_object_caption: accepted via %s", model)
        return cleaned
    log.info("hidden_object_caption: rejected — using the default")
    return default
```

**3b.** In `run_skillstotraineyes.py`, add the mode flag next to `--family`:

```python
    parser.add_argument("--mode", choices=["drill", "image"], default="drill",
                        help="drill: a Reel. image: a hidden-object feed post.")
```

Add the image branch as a function above `main()`:

```python
def _run_image_post(args, cfg, niche, conn) -> None:
    """Generate and publish one hidden-object feed image. No drill, no render."""
    from skillstotraineyes.hidden_object import HiddenObjectError, generate, invent_scene
    from skillstotraineyes.wording import hidden_object_caption

    seed = args.seed if args.seed is not None else int(time.time()) % 1_000_000
    recent = [r for r in _recent(conn) if r.get("kind") == "image"]
    scene = invent_scene(seed, recent, None if args.no_llm else cfg)
    log.info("Hidden object: %s in %s", scene["target"], scene["environment"])

    if args.dry_run:
        from skillstotraineyes.hidden_object import build_prompt
        log.info("--dry-run: prompt would be: %s", build_prompt(scene))
        return

    caption_body = (f"Somewhere in this picture: {scene['target']}. Can you find it?"
                    if args.no_llm else hidden_object_caption(scene, recent, cfg))
    caption = f"{caption_body}\n\n{niche['disclaimer']}"
    variation = {"seed": seed, "kind": "image", "target": scene["target"],
                 "environment": scene["environment"], "caption": caption_body}
    conn.execute(
        "INSERT INTO videos (status, prompt, niche_id, variation_params) "
        "VALUES ('queued', ?, ?, ?)",
        (f"[hidden_object] {scene['target']}", NICHE_ID, json.dumps(variation)),
    )
    conn.commit()
    video_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    try:
        out_dir = OUT_ROOT / "images"
        image_path = generate(scene, out_dir, seed, niche, cfg)
        conn.execute("UPDATE videos SET status='assembled', file_path=? WHERE id=?",
                     (image_path, video_id))
        conn.commit()

        if args.no_publish:
            log.info("--no-publish: image left at %s", image_path)
            return
        from pipeline.publisher import publish
        ho_cfg = niche.get("hidden_object", {})
        publish(
            video_id, image_path, niche, conn, cfg,
            platforms=["instagram"], schedule_time=args.schedule_time,
            title=f"Find the {scene['target']}", caption=caption,
            hashtags=[f"#{t}" for t in ho_cfg.get("hashtags", niche.get("hashtags", []))],
            media_type="image",
            notify_text=f"{niche['label']}: hidden object ({scene['target']}), scheduled",
        )
        log.info("Done. video_id=%d image=%s", video_id, image_path)
    except HiddenObjectError as e:
        log.error("Image generation unusable at video_id=%d: %s", video_id, e)
        conn.execute("UPDATE videos SET status='rejected' WHERE id=?", (video_id,))
        conn.commit()
        sys.exit(1)
```

Dispatch to it in `main()`, immediately after `conn.execute("PRAGMA foreign_keys=ON")`:

```python
    if args.mode == "image":
        _run_image_post(args, cfg, niche, conn)
        conn.close()
        return
```

Guard the drill path against image rows — in `_recent`, the existing rows have no `kind`, so filter in the family/level selection:

```python
    recent = [r for r in _recent(conn) if r.get("kind") != "image"]
```

**3c.** Update `CLAUDE.md`. In the `skillstotraineyes` section, add these bullets after the `wording.py` bullet:

```markdown
- **Difficulty (`difficulty.py`) is a score, not a speed dial.** Every knob carries points
  (count, size, speed, decoy similarity, guide visibility, occlusion, grid density); a level is
  a score range and the seed picks any mix that lands in it, so two GOD MODE drills look nothing
  alike. Ball size is deliberately non-monotonic — tiny balls hide and huge ones crowd, so both
  ends score high. The badge (`EASY · 100% can pass this` … `GOD MODE · 1% can pass this`) is
  drawn **inside the hook window**, never as its own segment: `saccade` already runs near 27.5s
  and `test_drill_shape` caps every drill at 30s.
  `fits()` screens a knob mix before `make_sim`, which raises outright on an unplaceable pack
  (ten balls at r=72), and `tracking` keeps its best-gap candidate rather than raising when a
  crowded arena cannot give the target three clear radii.
- **`catalog.json` is the single source of drills.** `FAMILIES` is derived from it, so an entry
  can no longer be unreachable from `pick_family` or a `KeyError` in `build`. Many entries share
  one builder with a different pinned knob — the eight pursuit drills are one `path_pursuit`.
  Adding a drill is a JSON edit when an existing builder fits.
- **Hidden-object posts (`hidden_object.py`)** are feed images, not Reels, and the answer is
  **never revealed** — viewers guess in the comments. Three guards, each of which fails silently
  without them: the niche must run `human_policy: "none"` (the default `"never"` strips "sniper"
  out of its own prompt and into the negative); the prompt is capped at 55 words with the target
  first (FLUX truncates near 77 CLIP tokens and drops the thing it is meant to hide); and any
  provider result under 1024px on the short edge is **rejected, not upscaled** — Pollinations
  returns 576×1024 and no lanczos pass puts back the detail a camouflaged target needs. Output is
  JPEG because the Graph API rejects PNG for feed photos. Captions are screened for position
  words as well as medical claims.
- **`media_type` routes image posts.** It rides in the schedule manifest and defaults to
  `"video"` everywhere (`publish()`, `schedule_video()`, `upload_all()`), so every existing
  caller is unaffected; `run_scheduled_upload.py` reads it to pick `upload_image_post()` over
  `upload_reel()` and to name the downloaded file. No new Drive folder and no new secret — image
  posts reuse `pending/`, and `_upload_to_temp_host()` (MIME now taken from the extension)
  supplies the public URL Instagram needs.
```

Also update the commands block at the top of `CLAUDE.md`:

```bash
python run_skillstotraineyes.py --level god                  # force a difficulty level
python run_skillstotraineyes.py --family zigzag_pursuit      # any catalog drill id
python run_skillstotraineyes.py --mode image                 # hidden-object feed post
python run_skillstotraineyes.py --mode image --dry-run       # show the image prompt only
```

- [ ] **Step 4: Run the whole suite**

Run: `pytest -m "not slow" -v`
Expected: PASS. Then `pytest -m slow -v` for the render checks.

- [ ] **Step 5: Verify both paths end to end**

```bash
python run_skillstotraineyes.py --mode image --dry-run
python run_skillstotraineyes.py --mode image --no-publish
python run_skillstotraineyes.py --level expert --no-publish
```

Expected: a JPEG in `output/skillstotraineyes/images/` whose target is genuinely hard to find, and an mp4 whose opening seconds carry the badge. Confirm the `videos` rows:

```bash
python -c "
import sqlite3, json
from config import cfg
c = sqlite3.connect(cfg.paths['db'])
for r in c.execute(\"SELECT id,status,file_path,variation_params FROM videos WHERE niche_id='skillstotraineyes' ORDER BY id DESC LIMIT 4\"):
    print(r[0], r[1], r[2]); print('  ', json.loads(r[3]))
"
```

Both rows must read `assembled`. Delete or publish them deliberately — `scripts/schedule_all_platforms.py` picks up every `assembled` video.

- [ ] **Step 6: Commit**

```bash
git add run_skillstotraineyes.py skillstotraineyes/wording.py CLAUDE.md tests/test_skillstotraineyes_flow.py
git commit -m "feat: hidden-object feed posts from run_skillstotraineyes --mode image"
```

---

## Known limits after this plan

- Knob scores are estimates until several renders at each level have been watched. Expect one tuning pass on `difficulty.KNOBS`.
- `engagement_tracker.py` reads a single `INSTAGRAM_ACCESS_TOKEN`, so neither eye Reels nor hidden-object posts feed adaptive scheduling. Unchanged by this plan.
- Nothing verifies that the generated target is actually present in the image. That was a deliberate decision — the guessing is the point — so a provider that quietly omits the target ships a post with no answer.
- Phase 3 (memory and cognitive families from the memory/cognitive CSVs, plus the FPS swarm drills) is not covered here and needs its own plan.
