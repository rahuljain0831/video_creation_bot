"""
Spin Illusion drill: a dot shape spinning on a vertical axis that can be seen
turning either way, then "which way did it spin for you?".

The ambiguity is the whole drill, so the projection carries no depth cue:
orthographic (no perspective shrink), every dot the same size, and dots drawn in
a fixed index order, never depth-sorted, so a near dot never reliably covers a
far one. Colour is fixed per dot, so it moves with the shape and says nothing
about which side is near.

Made harder on purpose (user: "too easy to tell"): a sphere nested inside,
spinning the other way; dots pulse in brightness with random phases; the spin
speed drifts. Brightness is per-dot random, never tied to depth.

No levels: everyone sees the illusion. Variety is shape, colour scheme, speed
and dot count, all from the seed.
"""

import colorsys
import math
import random

import numpy as np

from skillstotraineyes.drills import ACCENTS, CX, CY, FPS, Drill, _text, _text_ops, entry

# Side-on only. A tilt shows the top of the shape and the "seen from above" prior
# then decides the direction; ring and cube need a tilt (or show edges), so they went.
SHAPES = ("sphere", "helix")   # cylinder dropped by user review 2026-11-20
SCHEMES = ("single", "rainbow", "split")
HOOK_S, SPIN_S, HOLD_S = 3.0, 17.0, 4.0
RADIUS_PX = 380                 # unit shape -> pixels; keeps y inside 520..1280
INNER_SCALE, INNER_N = 0.5, 120 # nested sphere, spinning the other way
DOT_R = 8
DRIFT, DRIFT_S = 0.45, 6.0      # spin speed swings +-45% over 6s, never reverses
PULSE_S, PULSE_MIN = 1.5, 0.5   # per-dot brightness pulse, random phase
HOOK = "Reverse the spin with your mind"
INTRO = "Can you reverse the spin with your mind?"
QUESTION = "Which way did it spin for you? Comment LEFT or RIGHT"


def shape_points(shape: str, n: int) -> np.ndarray:
    """About `n` points (x, y, z) on `shape`, inside the unit box, y up."""
    if shape == "sphere":
        i = np.arange(n) + 0.5
        y = 1 - 2 * i / n
        r = np.sqrt(1 - y * y)
        a = math.pi * (3 - math.sqrt(5)) * i
        return np.stack([r * np.cos(a), y, r * np.sin(a)], 1)
    if shape == "helix":                                # two strands, two full turns
        t = np.linspace(0, 1, n // 2)
        a = 4 * math.pi * t
        y = -1 + 2 * t
        s1 = np.stack([0.7 * np.cos(a), y, 0.7 * np.sin(a)], 1)
        s2 = np.stack([0.7 * np.cos(a + math.pi), y, 0.7 * np.sin(a + math.pi)], 1)
        return np.concatenate([s1, s2])
    raise ValueError(f"unknown shape {shape!r}")


def _colours(scheme: str, pts: np.ndarray, rng: random.Random) -> list[tuple]:
    if scheme == "single":
        return [rng.choice(ACCENTS)] * len(pts)
    if scheme == "split":
        a, b = rng.sample(ACCENTS, 2)
        return [a if x >= 0 else b for x in pts[:, 0]]
    # rainbow by height: same hue round the axis, so it never marks a side
    return [tuple(round(c * 255) for c in colorsys.hsv_to_rgb((y + 1) / 2 * 0.85, 0.75, 1.0))
            for y in pts[:, 1]]


def spin_illusion(seed: int, text: dict | None = None, level: str | None = None,
                  entry_cfg: dict | None = None) -> Drill:
    entry_cfg = entry_cfg or entry("spin_illusion")
    rng = random.Random(seed)
    shape = rng.choice(SHAPES)
    scheme = rng.choice(SCHEMES)
    turns = rng.choice((3, 4, 5))               # whole turns in the spin window
    period = SPIN_S / turns                     # 5.67 / 4.25 / 3.4 s per turn
    outer = shape_points(shape, rng.randint(150, 350))
    # Nested counter-spinning sphere only inside the sphere (user: not in the helix).
    nested = shape == "sphere"
    inner = INNER_SCALE * shape_points("sphere", INNER_N) if nested else np.zeros((0, 3))
    cols = _colours(scheme, outer, rng) + _colours(scheme, inner / INNER_SCALE, rng)
    pts = np.concatenate([outer, inner])
    spin = np.concatenate([np.ones(len(outer)), -np.ones(len(inner))])   # inner turns the other way
    # One fixed shuffled draw order for both layers: if one layer were always on
    # top, overlaps would say which is in front.
    order = list(range(len(pts)))
    rng.shuffle(order)
    phase = np.array([rng.uniform(0, 2 * math.pi) for _ in pts])
    direction = rng.choice((-1, 1))
    w0 = direction * 2 * math.pi / period

    t_q = HOOK_S + SPIN_S
    total = t_q + HOLD_S
    hook = _text(text, "hook", HOOK)
    question = _text(text, "question", QUESTION)
    badge = entry_cfg.get("badge", "")

    def ops(f: int) -> list:
        t = f / FPS
        # Angle under a drifting speed w0 * (1 + DRIFT sin(2 pi t / DRIFT_S)), integrated.
        th = w0 * (t + DRIFT * DRIFT_S / (2 * math.pi) * (1 - math.cos(2 * math.pi * t / DRIFT_S)))
        a = th * spin
        xs = CX + RADIUS_PX * (pts[:, 0] * np.cos(a) + pts[:, 2] * np.sin(a))
        ys = CY - RADIUS_PX * pts[:, 1]
        glow = PULSE_MIN + (1 - PULSE_MIN) * 0.5 * (1 + np.sin(2 * math.pi * t / PULSE_S + phase))
        out = [("disc", float(xs[i]), float(ys[i]), DOT_R,
                tuple(round(ch * glow[i]) for ch in cols[i])) for i in order]
        return out + _text_ops(t, hook, HOOK_S, question, t_q, "", total + 1, badge=badge)

    return Drill("spin_illusion", total, ops,
                 {"family": "spin_illusion", "level": "", "shape": shape, "scheme": scheme,
                  "period": round(period, 2), "n": len(outer), "inner": "sphere" if nested else ""},
                 voice=[(0.3, INTRO), (t_q + 0.2, question)], level="")
