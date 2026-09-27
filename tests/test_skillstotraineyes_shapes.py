"""Arena shapes: the catalog, the polygon walls in sim.py, and tracking's use of them."""
import numpy as np
import pytest

from skillstotraineyes.drills import CX, CY, build
from skillstotraineyes.shapes import (
    boundary_query, by_id, catalog, feasible, pick_shape, scale_for,
)
from skillstotraineyes.sim import make_sim, run

LEVEL_BALLS = {"medium": (8, 44, 450), "hard": (8, 32, 420), "expert": (8, 26, 405)}


def _simple(v):
    a, b = v, np.roll(v, -1, 0)

    def ccw(p, q, r):
        return (r[1] - p[1]) * (q[0] - p[0]) - (q[1] - p[1]) * (r[0] - p[0])
    n = len(v)
    for i in range(n):
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            p, q, r, s = a[i], b[i], a[j], b[j]
            if ccw(p, r, s) * ccw(q, r, s) < 0 and ccw(p, q, r) * ccw(p, q, s) < 0:
                return False
    return True


def test_catalog_has_100_unique_simple_closed_shapes():
    shapes = catalog()
    assert len(shapes) >= 100
    assert len({s.id for s in shapes}) == len(shapes)
    for s in shapes:
        assert s.area > 0.2 and _simple(s.verts), s.id
    # triangle up to dodecagon are all present, plus the circle
    assert {f"poly{n}_up" for n in range(3, 13)} <= {s.id for s in shapes} and by_id("circle")


@pytest.mark.parametrize("level", LEVEL_BALLS)
def test_most_shapes_can_hold_the_locked_balls(level):
    n, r, radius = LEVEL_BALLS[level]
    ok = [s for s in catalog() if feasible(s, scale_for(s, radius), n, r)]
    assert len(ok) >= 100


@pytest.mark.parametrize("sid", ["poly3_up", "star5_sharp", "heart", "crescent", "mino_upen",
                                 "flower6_b", "sector_270", "rect_h2.0", "poly12_up", "arch"])
def test_balls_stay_inside_the_polygon(sid):
    shape = by_id(sid)
    s = scale_for(shape, 405)
    poly = np.array([CX, CY]) + s * shape.verts
    sim = make_sim(3, 6, 405, 26, 640, arena_c=(CX, CY), poly=poly)
    sim.vel *= 2.5                                     # fastest ball tracking can roll
    pts = np.concatenate(run(sim, 150))
    dist, _, inside = boundary_query(pts, poly)
    assert inside.all()
    assert (dist - 26).min() > -4                      # ball edge never more than a few px past the line


def test_pick_shape_is_seeded_and_feasible():
    a, b = pick_shape(5, 8, 32, 420), pick_shape(5, 8, 32, 420)
    assert a[0].id == b[0].id and a[1] == b[1]
    assert feasible(a[0], a[1], 8, 32)


def test_tracking_uses_varied_shapes_and_keeps_the_locked_numbers():
    shapes = set()
    for seed in range(25):
        p = build("tracking", seed, level="hard").params
        assert (p["n"], p["ball"], p["arena"], p["speed"]) == (8, 32, 420, 520.0)
        shapes.add(p["shape"])
    assert len(shapes) >= 12


def test_polygon_arena_is_drawn_as_a_poly_op_inside_the_frame():
    from skillstotraineyes.drills import W, H
    d = build("tracking", 11, level="hard")
    assert d.params["shape"] != "circle"
    poly = [op for op in d.ops(0) if op[0] == "poly"]
    assert len(poly) == 1
    assert all(0 < x < W and 0 < y < H for x, y in poly[0][3])
