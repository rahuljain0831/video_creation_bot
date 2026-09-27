"""
Closed arenas for ball drills: a catalog of 100+ polygons plus the geometry to
bounce balls inside them.

Every shape is a polygon (curves are sampled), stored at unit scale: centred on
its bounding box, then scaled so its farthest vertex sits at radius 1. `pick_shape`
scales one to pixels; `boundary_query` is what `sim.py` uses for walls.

Concave shapes are fine: a ball only has to keep `radius` clear of the boundary, so
a pocket narrower than the ball is simply unreachable rather than a trap.
"""

import math
import random
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

_NAMES = {3: "triangle", 4: "square", 5: "pentagon", 6: "hexagon", 7: "heptagon",
          8: "octagon", 9: "nonagon", 10: "decagon", 11: "hendecagon", 12: "dodecagon"}

MAX_HALF_W, MAX_HALF_H = 490.0, 480.0     # keeps the arena inside the frame and off the text bands
INRADIUS_MIN = 3.0                        # widest open spot, in ball radii
MIN_ID = "circle"                         # drawn as a true circle, not a polygon


@dataclass(frozen=True, eq=False)
class Shape:
    id: str
    name: str
    verts: np.ndarray        # (m, 2) unit scale, bbox-centred

    @property
    def area(self) -> float:
        return polygon_area(self.verts)

    @property
    def perimeter(self) -> float:
        return float(np.linalg.norm(np.roll(self.verts, -1, axis=0) - self.verts, axis=1).sum())

    @property
    def inradius(self) -> float:
        return _inradius(self)


# ── geometry ──────────────────────────────────────────────────────────────────

def polygon_area(v: np.ndarray) -> float:
    x, y = v[:, 0], v[:, 1]
    return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2)


def boundary_query(pts: np.ndarray, poly: np.ndarray):
    """For each point: (distance to the boundary, nearest boundary point, is-inside)."""
    a, b = poly, np.roll(poly, -1, axis=0)
    ab = b - a
    ap = pts[:, None, :] - a[None]
    t = np.clip((ap * ab[None]).sum(2) / np.maximum((ab ** 2).sum(1), 1e-12), 0.0, 1.0)
    c = a[None] + t[..., None] * ab[None]
    d = np.linalg.norm(pts[:, None, :] - c, axis=2)
    k = d.argmin(1)
    idx = np.arange(len(pts))
    px, py = pts[:, 0:1], pts[:, 1:2]
    ay, by, ax, bx = a[:, 1][None], b[:, 1][None], a[:, 0][None], b[:, 0][None]
    dy = np.where(by - ay == 0, 1e-12, by - ay)
    crossing = ((ay > py) != (by > py)) & (px < (bx - ax) * (py - ay) / dy + ax)
    return d[idx, k], c[idx, k], crossing.sum(1) % 2 == 1


@lru_cache(maxsize=None)
def _inradius(shape: Shape) -> float:
    """Radius of the biggest disc that fits, at unit scale (grid estimate)."""
    g = np.linspace(-1, 1, 41)
    pts = np.stack(np.meshgrid(g, g), -1).reshape(-1, 2)
    d, _, inside = boundary_query(pts, shape.verts)
    return float(d[inside].max()) if inside.any() else 0.0


def _unit(pts) -> np.ndarray:
    p = np.asarray(pts, dtype=np.float64)
    p = p - (p.min(0) + p.max(0)) / 2
    return p / np.linalg.norm(p, axis=1).max()


def _chaikin(ctrl, iters: int = 3) -> np.ndarray:
    """Corner cutting: turns a control polygon into a smooth closed curve."""
    p = np.asarray(ctrl, dtype=np.float64)
    for _ in range(iters):
        q = np.roll(p, -1, axis=0)
        p = np.stack([0.75 * p + 0.25 * q, 0.25 * p + 0.75 * q], 1).reshape(-1, 2)
    return p


# ── generators ────────────────────────────────────────────────────────────────

def _regular(n, rot=0.0, r=1.0):
    a = rot - math.pi / 2 + 2 * math.pi * np.arange(n) / n
    return np.stack([r * np.cos(a), r * np.sin(a)], 1)


def _star(n, inner, rot=0.0):
    a = rot - math.pi / 2 + math.pi * np.arange(2 * n) / n
    r = np.where(np.arange(2 * n) % 2 == 0, 1.0, inner)
    return np.stack([r * np.cos(a), r * np.sin(a)], 1)


def _ellipse(rx, ry, m=72):
    t = 2 * math.pi * np.arange(m) / m
    return np.stack([rx * np.cos(t), ry * np.sin(t)], 1)


def _superellipse(p, m=96):
    t = 2 * math.pi * np.arange(m) / m
    c, s = np.cos(t), np.sin(t)
    return np.stack([np.sign(c) * np.abs(c) ** (2 / p), np.sign(s) * np.abs(s) ** (2 / p)], 1)


def _flower(k, amp, m=120):
    t = 2 * math.pi * np.arange(m) / m
    r = 1 + amp * np.cos(k * t)
    return np.stack([r * np.cos(t), r * np.sin(t)], 1)


def _arc(cx, cy, r, a0, a1, m=24):
    a = np.linspace(a0, a1, m)
    return np.stack([cx + r * np.cos(a), cy + r * np.sin(a)], 1)


def _stadium(w, h, m=18):
    """Pill: `h` is the full height, `w` the full width (either may be the long side)."""
    if w >= h:
        r, half = h / 2, (w - h) / 2
        return np.vstack([_arc(half, 0, r, -math.pi / 2, math.pi / 2, m),
                          _arc(-half, 0, r, math.pi / 2, 3 * math.pi / 2, m)])
    r, half = w / 2, (h - w) / 2
    return np.vstack([_arc(0, half, r, 0, math.pi, m), _arc(0, -half, r, math.pi, 2 * math.pi, m)])


def _heart(m=120):
    t = 2 * math.pi * np.arange(m) / m
    return np.stack([16 * np.sin(t) ** 3,
                     -(13 * np.cos(t) - 5 * np.cos(2 * t) - 2 * np.cos(3 * t) - np.cos(4 * t))], 1)


def _cross(w):
    h = w / 2
    return [(-h, -1), (h, -1), (h, -h), (1, -h), (1, h), (h, h),
            (h, 1), (-h, 1), (-h, h), (-1, h), (-1, -h), (-h, -h)]


def _crescent():
    c, r = 0.45, 0.8
    x = (1 - r * r + c * c) / (2 * c)
    y = math.sqrt(1 - x * x)
    t1, f1 = math.atan2(y, x), math.atan2(y, x - c)
    # Outer arc round the left, then back along the inner circle's far side.
    return np.vstack([_arc(0, 0, 1, t1, 2 * math.pi - t1, 40),
                      _arc(c, 0, r, 2 * math.pi - f1, f1, 40)])


def _sector(deg):
    half = math.radians(deg) / 2
    return np.vstack([[[0.0, 0.0]], _arc(0, 0, 1, -half, half, 40)])


def _teardrop(m=100):
    t = 2 * math.pi * np.arange(m) / m
    return np.stack([np.cos(t), np.sin(t) * np.sin(t / 2) ** 1.5], 1)


def _lens():
    return np.vstack([_arc(-0.5, 0, 1, -math.pi / 3, math.pi / 3, 24),
                      _arc(0.5, 0, 1, 2 * math.pi / 3, 4 * math.pi / 3, 24)[1:-1]])


def _d_shape():
    return np.vstack([[[-1, -1]], _arc(0, 0, 1, -math.pi / 2, math.pi / 2, 30), [[-1, 1]]])


def _arch():
    return np.vstack([[[-0.8, 1], [0.8, 1]], _arc(0, -0.2, 0.8, 0, -math.pi, 30)])


def _semicircle():
    return _arc(0, 0, 1, math.pi, 2 * math.pi, 40)


_POLYOMINOES = {
    "L tetromino": [(0, 0), (2, 0), (2, 1), (1, 1), (1, 3), (0, 3)],
    "T tetromino": [(0, 0), (3, 0), (3, 1), (2, 1), (2, 2), (1, 2), (1, 1), (0, 1)],
    "S tetromino": [(1, 0), (3, 0), (3, 1), (2, 1), (2, 2), (0, 2), (0, 1), (1, 1)],
    "Z tetromino": [(0, 0), (2, 0), (2, 1), (3, 1), (3, 2), (1, 2), (1, 1), (0, 1)],
    "U pentomino": [(0, 0), (3, 0), (3, 2), (2, 2), (2, 1), (1, 1), (1, 2), (0, 2)],
    "X pentomino": [(1, 0), (2, 0), (2, 1), (3, 1), (3, 2), (2, 2), (2, 3), (1, 3), (1, 2), (0, 2), (0, 1), (1, 1)],
    "P pentomino": [(0, 0), (2, 0), (2, 2), (1, 2), (1, 3), (0, 3)],
    "W pentomino": [(0, 0), (1, 0), (1, 1), (2, 1), (2, 2), (3, 2), (3, 3), (1, 3), (1, 2), (0, 2)],
}


def _specials():
    yield "heart", "heart", _heart()
    yield "cross_thin", "thin plus", _cross(0.34)
    yield "cross_fat", "fat plus", _cross(0.5)
    yield "t_bar", "T bar", [(-1, -1), (1, -1), (1, -0.4), (0.3, -0.4), (0.3, 1), (-0.3, 1), (-0.3, -0.4), (-1, -0.4)]
    yield "arrow", "arrow", [(-1, -0.35), (0.1, -0.35), (0.1, -0.85), (1, 0), (0.1, 0.85), (0.1, 0.35), (-1, 0.35)]
    yield "chevron", "chevron", [(-1, -1), (0.2, -1), (1, 0), (0.2, 1), (-1, 1), (-0.2, 0)]
    yield "kite", "kite", [(0, -1), (0.6, -0.2), (0, 1), (-0.6, -0.2)]
    yield "trapezoid_tall", "trapezoid", [(-0.6, -0.7), (0.6, -0.7), (1, 0.7), (-1, 0.7)]
    yield "trapezoid_wide", "wide trapezoid", [(-0.8, -0.5), (0.8, -0.5), (1, 0.5), (-1, 0.5)]
    yield "parallelogram_r", "parallelogram", [(-0.6, -0.5), (1, -0.5), (0.6, 0.5), (-1, 0.5)]
    yield "parallelogram_l", "parallelogram (left)", [(-1, -0.5), (0.6, -0.5), (1, 0.5), (-0.6, 0.5)]
    yield "rhombus", "rhombus", [(0, -1), (0.67, 0), (0, 1), (-0.67, 0)]
    yield "house", "house", [(-0.8, 1), (0.8, 1), (0.8, -0.1), (0, -1), (-0.8, -0.1)]
    yield "crescent", "crescent", _crescent()
    yield "semicircle", "semicircle", _semicircle()
    yield "sector_90", "quarter circle", _sector(90)
    yield "sector_120", "pie slice", _sector(120)
    yield "sector_270", "three-quarter circle", _sector(270)
    yield "teardrop", "teardrop", _teardrop()
    yield "lens", "lens", _lens()
    yield "d_shape", "D shape", _d_shape()
    yield "arch", "arch", _arch()


@lru_cache(maxsize=1)
def catalog() -> tuple[Shape, ...]:
    """Every arena, in a stable order. 100+ entries, all simple closed polygons."""
    out: list[Shape] = []

    def add(sid, name, pts):
        out.append(Shape(sid, name, _unit(pts)))

    add(MIN_ID, "circle", _ellipse(1, 1, 96))
    for n in range(3, 13):
        add(f"poly{n}_up", f"{_NAMES[n]} (point up)", _regular(n))
        add(f"poly{n}_flip", f"{_NAMES[n]} (turned)", _regular(n, math.pi / n))
    for n in range(3, 13):
        add(f"star{n}_sharp", f"{n}-point star", _star(n, 0.45))
        add(f"star{n}_fat", f"{n}-point fat star", _star(n, 0.62))
    for ratio in (1.25, 1.5, 1.8, 2.2):
        add(f"ellipse_h{ratio}", f"wide ellipse {ratio}", _ellipse(ratio, 1))
        add(f"ellipse_v{ratio}", f"tall ellipse {ratio}", _ellipse(1, ratio))
    for ratio in (1.3, 1.6, 2.0):
        rect = [(-ratio, -1), (ratio, -1), (ratio, 1), (-ratio, 1)]
        add(f"rect_h{ratio}", f"wide rectangle {ratio}", rect)
        add(f"rect_v{ratio}", f"tall rectangle {ratio}", [(y, x) for x, y in rect])
        add(f"rrect_h{ratio}", f"wide rounded rectangle {ratio}", _chaikin(rect))
        add(f"rrect_v{ratio}", f"tall rounded rectangle {ratio}", _chaikin([(y, x) for x, y in rect]))
    for ratio in (1.5, 2.0):
        add(f"pill_h{ratio}", f"wide pill {ratio}", _stadium(ratio, 1))
        add(f"pill_v{ratio}", f"tall pill {ratio}", _stadium(1, ratio))
    for p in (2.5, 3, 4, 6):
        add(f"squircle{p}", f"squircle {p}", _superellipse(p))
    for n in range(3, 9):
        add(f"soft{n}", f"rounded {_NAMES[n]}", _chaikin(_regular(n)))
    for n in range(5, 9):
        add(f"softstar{n}", f"soft {n}-point star", _chaikin(_star(n, 0.5)))
    for k in range(3, 11):
        add(f"flower{k}_a", f"{k}-petal flower", _flower(k, 0.18))
        add(f"flower{k}_b", f"{k}-petal deep flower", _flower(k, 0.32))
    for name, pts in _POLYOMINOES.items():
        add("mino_" + name.split()[0].lower() + name.split()[1][:3], name, pts)
    for sid, name, pts in _specials():
        add(sid, name, pts)
    return tuple(out)


def by_id(shape_id: str) -> Shape:
    for s in catalog():
        if s.id == shape_id:
            return s
    raise KeyError(f"no shape {shape_id!r}")


# ── choosing a shape for a drill ──────────────────────────────────────────────

def scale_for(shape: Shape, arena_r: float) -> float:
    """Pixels per unit: the area of the circular arena it replaces, capped to stay in frame."""
    by_area = math.sqrt(math.pi * arena_r ** 2 / shape.area)
    half = np.abs(shape.verts).max(0)
    return float(min(by_area, MAX_HALF_W / half[0], MAX_HALF_H / half[1]))


def feasible(shape: Shape, s: float, n: int, ball_r: float) -> bool:
    """Room for `n` balls: a wide-enough open spot, and the packing density the circle needed."""
    if shape.inradius * s < INRADIUS_MIN * ball_r:
        return False
    eroded = shape.area * s * s - shape.perimeter * s * ball_r
    return eroded > 0 and n * (1.1 * ball_r) ** 2 <= 0.35 * eroded / math.pi


def pick_shape(seed: int, n: int, ball_r: float, arena_r: float) -> tuple[Shape, float]:
    """A seeded arena that can hold the balls, and its pixel scale. Falls back to the circle."""
    rng = random.Random(seed * 7919 + 13)
    options = [(sh, scale_for(sh, arena_r)) for sh in catalog()]
    options = [(sh, s) for sh, s in options if feasible(sh, s, n, ball_r)]
    if not options:
        return by_id(MIN_ID), arena_r
    return rng.choice(options)
