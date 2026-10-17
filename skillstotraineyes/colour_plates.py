"""
Colour Plates drill: Ishihara-style dot plates, each hiding a number.

Each plate packs a disc with dots, and the dots inside the number's glyph get the
figure colour. For a type-specific plate, figure and background differ only along
that type's confusion direction: the null space of its Machado (2009) simulation
matrix, so the simulated eye sees one colour where a typical eye sees two. Every
dot is also scaled by a random brightness so the number cannot be read from
lightness alone. A control plate's figure is twice as bright as its field instead, readable by anyone.

A fun check, not a diagnosis: phone screens and brightness vary.

Plates are drawn once per video and pasted as an "image" op, never as ~800 discs
per frame.
"""

import random
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw

from skillstotraineyes.drills import (
    BG, CX, CY, TEXT_TOP_Y, WHITE, Drill, FPS, _badge, _text, _text_ops, entry,
)

# Machado, Oliveira & Fernandes (2009), severity 1.0, linear RGB.
SIM = {
    "protan": np.array([[0.152286, 1.052583, -0.204868],
                        [0.114503, 0.786281, 0.099216],
                        [-0.003882, -0.048116, 1.051998]]),
    "deutan": np.array([[0.367322, 0.860646, -0.227968],
                        [0.280085, 0.672501, 0.047413],
                        [-0.011820, 0.042940, 0.968881]]),
    "tritan": np.array([[1.255528, -0.076749, -0.178779],
                        [-0.078411, 0.930809, 0.147602],
                        [0.004733, 0.691367, 0.303900]]),
}
# Exact null space for protan/deutan; tritan's matrix is full rank, so its
# number keeps ~16% of its contrast under simulation (the model's own limit).
CONFUSION = {k: np.linalg.svd(m)[2][-1] for k, m in SIM.items()}

PLATE_R = 420                     # plate radius in px; 840px disc fits the 880px text width
JITTER = (0.82, 1.12)             # per-dot brightness scale, hides the figure from lightness
CONTROL_DARKEN = 0.5

# level -> plates, seconds per plate, confusion step t, control plates, types, number style.
# Approved 2026-10-17. Tune by watching renders, like tracking.
PLATES_LOCKED = {
    "easy":   (7, 3.2, 0.30, 5, ("protan", "deutan"), "one"),
    "medium": (8, 2.8, 0.26, 4, ("protan", "deutan"), "one"),
    "hard":   (9, 2.5, 0.21, 2, ("protan", "deutan", "tritan"), "one"),
    "expert": (10, 2.2, 0.17, 0, ("protan", "deutan", "tritan"), "mixed"),
    "god":    (12, 1.9, 0.13, 0, ("protan", "deutan", "tritan"), "two_small"),
}
HOOK_S = 3.0
END_S = 3.0
_PACKINGS = 6


def to_srgb8(lin: np.ndarray) -> np.ndarray:
    lin = np.clip(lin, 0.0, 1.0)
    s = np.where(lin <= 0.0031308, 12.92 * lin, 1.055 * lin ** (1 / 2.4) - 0.055)
    return np.round(s * 255).astype(np.uint8)


@lru_cache(maxsize=_PACKINGS)
def _packing(k: int) -> np.ndarray:
    """Greedy largest-first circle packing inside the plate: rows of (x, y, r) about the centre."""
    rng = np.random.default_rng(1000 + k)
    n = 60000
    r = np.sort(rng.choice([17, 14, 12, 10, 8, 6], n, p=[.05, .1, .17, .22, .24, .22]))[::-1]
    ang, rad = rng.uniform(0, 2 * np.pi, n), np.sqrt(rng.uniform(0, 1, n))
    x, y = (PLATE_R - r) * rad * np.cos(ang), (PLATE_R - r) * rad * np.sin(ang)
    keep, m = np.empty((n, 3)), 0
    for xi, yi, ri in zip(x, y, r):
        k = keep[:m]
        if m and np.any(np.hypot(k[:, 0] - xi, k[:, 1] - yi) < k[:, 2] + ri + 2.5):
            continue
        keep[m] = (xi, yi, ri)
        m += 1
    # ponytail: O(n * kept) greedy; cached and reused (rotated, mirrored) across plates.
    return keep[:m]


@lru_cache(maxsize=64)
def _glyph(number: int, size: int) -> np.ndarray:
    from skillstotraineyes.renderer import _font
    d = 2 * PLATE_R
    m = Image.new("L", (d, d), 0)
    ImageDraw.Draw(m).text((d / 2, d / 2), str(number), font=_font(size), fill=255, anchor="mm")
    return np.asarray(m) > 127


@dataclass
class Plate:
    kind: str                 # "control" | "protan" | "deutan" | "tritan"
    number: int
    lin: np.ndarray           # (n, 3) linear-RGB dot colours
    figure: np.ndarray        # (n,) bool, dot lies inside the number
    image: Image.Image


def _pair(kind: str, t: float, rng: random.Random) -> tuple[np.ndarray, np.ndarray]:
    """(figure, background) linear colours that stay in gamut under the brightness jitter."""
    hi = 1.0 / JITTER[1]
    for _ in range(200):
        c = np.array([rng.uniform(0.06, 0.7) for _ in range(3)])
        if kind == "control":
            fig, bg = c, c * CONTROL_DARKEN
        else:
            step = CONFUSION[kind] * t / 2 * rng.choice([-1, 1])
            fig, bg = c + step, c - step
        if min(fig.min(), bg.min()) >= 0.02 and max(fig.max(), bg.max()) <= hi:
            return fig, bg
    raise ValueError(f"no in-gamut colour pair for {kind} at t={t}")


def make_plate(seed: int, kind: str, number: int, t: float, size: int) -> Plate:
    rng = random.Random(seed)
    dots = _packing(seed % _PACKINGS)
    a = rng.uniform(0, 2 * np.pi)
    ca, sa = np.cos(a), np.sin(a)
    x = dots[:, 0] * ca - dots[:, 1] * sa
    y = dots[:, 0] * sa + dots[:, 1] * ca
    if rng.random() < 0.5:
        x = -x
    glyph = _glyph(number, size)
    ix = np.clip((x + PLATE_R).astype(int), 0, 2 * PLATE_R - 1)
    iy = np.clip((y + PLATE_R).astype(int), 0, 2 * PLATE_R - 1)
    figure = glyph[iy, ix]

    fig, bg = _pair(kind, t, rng)
    jitter = np.array([rng.uniform(*JITTER) for _ in range(len(dots))])[:, None]
    lin = np.where(figure[:, None], fig, bg) * jitter
    rgb = to_srgb8(lin)

    ss, d = 2, 2 * PLATE_R
    im = Image.new("RGB", (d * ss, d * ss), BG)
    dr = ImageDraw.Draw(im)
    for (xi, yi, ri), col in zip(np.column_stack([x, y, dots[:, 2]]), rgb):
        cx, cy = (xi + PLATE_R) * ss, (yi + PLATE_R) * ss
        dr.ellipse([cx - ri * ss, cy - ri * ss, cx + ri * ss, cy + ri * ss], fill=tuple(int(v) for v in col))
    return Plate(kind, number, lin, figure, im.resize((d, d), Image.LANCZOS))


def separation(plate: Plate, sim: str | None = None) -> float:
    """Distance between mean figure and mean background colour, as `sim` would see it."""
    lin = plate.lin if sim is None else plate.lin @ SIM[sim].T
    return float(np.linalg.norm(lin[plate.figure].mean(0) - lin[~plate.figure].mean(0)))


def _numbers(style: str, n: int, rng: random.Random) -> list[int]:
    if style == "one":
        return rng.sample(range(1, 10), n)
    if style == "mixed":
        return rng.sample(list(range(2, 10)) + rng.sample(range(10, 100), 6), n)
    return rng.sample(range(12, 99), n)


def colour_plates(seed: int, text: dict | None = None, level: str | None = None,
                  entry_cfg: dict | None = None) -> Drill:
    entry_cfg = entry_cfg or entry("colour_plates")
    rng = random.Random(seed)
    n, sec, t, controls, types, style = PLATES_LOCKED[level or "medium"]

    kinds = ["control"] * controls + [rng.choice(types) for _ in range(n - controls)]
    rng.shuffle(kinds)
    numbers = _numbers(style, n, rng)
    plates = [make_plate(seed * 100 + i, k, num, t,
                         460 if style == "two_small" else (700 if num < 10 else 560))
              for i, (k, num) in enumerate(zip(kinds, numbers))]

    t_end = HOOK_S + n * sec
    total = t_end + END_S
    hook = _text(text, "hook", "Can you spot all the numbers?")
    question = _text(text, "question", f"How many of the {n} did you find? Comment and let us know!")
    badge = _badge(level)

    def ops(f: int) -> list:
        tt = f / FPS
        if tt < HOOK_S:
            return _text_ops(tt, hook, HOOK_S, question, total, "", total, badge=badge)
        if tt < t_end:
            k = min(int((tt - HOOK_S) / sec), n - 1)
            return [("image", CX, CY, plates[k].image),
                    ("text", f"{k + 1} / {n}", CX, TEXT_TOP_Y, 64, WHITE)]
        return [("text", question, CX, CY, 84, WHITE)]

    return Drill("colour_plates", total, ops,
                 {"family": "colour_plates", "level": level or "", "n": n, "sec": sec,
                  "t": t, "plates": [[k, num] for k, num in zip(kinds, numbers)]},
                 voice=[(0.3, hook), (t_end + 0.2, question)],
                 level=level or "")
