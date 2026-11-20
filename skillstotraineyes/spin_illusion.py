"""
Spin Illusion drill: a dot shape spinning on a vertical axis that can be seen
turning either way, then "which way did it spin for you?".

The ambiguity is the whole drill, so the projection carries no depth cue:
orthographic (no perspective shrink), every dot the same size, and dots drawn in
a fixed index order, never depth-sorted, so a near dot never reliably covers a
far one. Colour is fixed per dot, so it moves with the shape and says nothing
about which side is near.

No levels: everyone sees the illusion. Variety is shape, colour scheme, speed
and dot count, all from the seed.
"""

import colorsys
import math
import random

import numpy as np

from skillstotraineyes.drills import ACCENTS, CX, CY, FPS, Drill, _text, _text_ops, entry

SHAPES = ("sphere", "ring", "cylinder", "helix", "cube")
SCHEMES = ("single", "rainbow", "split")
HOOK_S, SPIN_S, HOLD_S = 3.0, 17.0, 4.0
RADIUS_PX = 380                 # unit shape -> pixels; keeps y inside 520..1280
DOT_R = 8
TILT_DEG = {"ring": 35}         # others 20 degrees
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
    if shape == "ring":                                 # torus, tube radius 0.3
        k = max(8, int(math.sqrt(n / 3)))               # floor: 3k^2 never exceeds n
        u, v = np.meshgrid(np.linspace(0, 2 * math.pi, 3 * k, endpoint=False),
                           np.linspace(0, 2 * math.pi, k, endpoint=False))
        u, v = u.ravel(), v.ravel()
        rr = 0.7 + 0.3 * np.cos(v)
        return np.stack([rr * np.cos(u), 0.3 * np.sin(v), rr * np.sin(u)], 1)
    if shape == "cylinder":
        rows = max(6, round(math.sqrt(n / 2)))
        a, y = np.meshgrid(np.linspace(0, 2 * math.pi, n // rows, endpoint=False),
                           np.linspace(-1, 1, rows))
        a, y = a.ravel(), y.ravel()
        return np.stack([0.7 * np.cos(a), y, 0.7 * np.sin(a)], 1)
    if shape == "helix":                                # two strands, two full turns
        t = np.linspace(0, 1, n // 2)
        a = 4 * math.pi * t
        y = -1 + 2 * t
        s1 = np.stack([0.7 * np.cos(a), y, 0.7 * np.sin(a)], 1)
        s2 = np.stack([0.7 * np.cos(a + math.pi), y, 0.7 * np.sin(a + math.pi)], 1)
        return np.concatenate([s1, s2])
    if shape == "cube":                                 # dots along the 12 edges
        per = n // 12
        t = np.linspace(-1, 1, per) * 0.6
        out = []
        for axis in range(3):
            for s1 in (-0.6, 0.6):
                for s2 in (-0.6, 0.6):
                    p = np.zeros((per, 3))
                    p[:, axis] = t
                    p[:, (axis + 1) % 3] = s1
                    p[:, (axis + 2) % 3] = s2
                    out.append(p)
        return np.concatenate(out)
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
    pts = shape_points(shape, rng.randint(150, 350))
    cols = _colours(scheme, pts, rng)
    direction = rng.choice((-1, 1))             # irrelevant to the viewer, varied anyway

    t_q = HOOK_S + SPIN_S
    total = t_q + HOLD_S
    hook = _text(text, "hook", HOOK)
    question = _text(text, "question", QUESTION)
    badge = entry_cfg.get("badge", "")
    tilt = math.radians(TILT_DEG.get(shape, 20))
    cos_t, sin_t = math.cos(tilt), math.sin(tilt)

    def ops(f: int) -> list:
        t = f / FPS
        th = direction * 2 * math.pi * t / period
        c, s = math.cos(th), math.sin(th)
        xs = CX + RADIUS_PX * (pts[:, 0] * c + pts[:, 2] * s)
        z = -pts[:, 0] * s + pts[:, 2] * c
        # Tilt toward the camera so a ring is an ellipse, not a line. Orthographic,
        # so toward vs away stays ambiguous and the illusion survives.
        ys = CY - RADIUS_PX * (pts[:, 1] * cos_t - z * sin_t)
        out = [("disc", float(x), float(y), DOT_R, col) for x, y, col in zip(xs, ys, cols)]
        return out + _text_ops(t, hook, HOOK_S, question, t_q, "", total + 1, badge=badge)

    return Drill("spin_illusion", total, ops,
                 {"family": "spin_illusion", "level": "", "shape": shape, "scheme": scheme,
                  "period": round(period, 2), "n": len(pts)},
                 voice=[(0.3, INTRO), (t_q + 0.2, question)], level="")
