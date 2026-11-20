"""Tests for skillstotraineyes/spin_illusion.py."""
import pytest

from skillstotraineyes.drills import build
from skillstotraineyes.spin_illusion import SHAPES, shape_points, spin_illusion

Q = "Which way did it spin for you? Comment LEFT or RIGHT"


def _discs(ops):
    return [op for op in ops if op[0] == "disc"]


@pytest.mark.parametrize("shape", SHAPES)
def test_every_shape_has_points_inside_unit_box(shape):
    pts = shape_points(shape, 300)
    assert 150 <= len(pts) <= 350
    assert abs(pts).max() <= 1.0 + 1e-9


def test_shapes_cover_all_five():
    assert set(SHAPES) == {"sphere", "ring", "cylinder", "helix", "cube"}


def test_deterministic_and_varies_by_seed():
    a, b = spin_illusion(3), spin_illusion(3)
    assert a.params == b.params and a.ops(100) == b.ops(100)
    assert len({tuple(sorted(spin_illusion(s).params.items())) for s in range(10)}) > 1


def test_all_shapes_reachable():
    assert {spin_illusion(s).params["shape"] for s in range(60)} == set(SHAPES)


@pytest.mark.parametrize("seed", range(5))
def test_no_depth_cues(seed):
    """Same radius for every dot, and draw order never changes (no depth sort)."""
    d = spin_illusion(seed)
    f0, f1 = _discs(d.ops(0)), _discs(d.ops(90))
    assert len({op[3] for op in f0}) == 1
    assert [op[4] for op in f0] == [op[4] for op in f1]     # colours stay in index order


def test_period_and_dots_in_range():
    for s in range(20):
        p = spin_illusion(s).params
        assert 3.0 <= p["period"] <= 6.0
        assert 150 <= p["n"] <= 350


def test_no_level_badge_and_question():
    d = spin_illusion(1, level="god")
    assert d.level == "" and d.params["level"] == ""
    first = [op[1] for op in d.ops(0) if op[0] == "text"]
    assert "BRAIN CHALLENGE" in first and "Reverse the spin with your mind" in first
    last = [op[1] for op in d.ops(d.frames - 1) if op[0] == "text"]
    assert Q in last
    assert d.voice[1][1] == Q and 15 <= d.duration <= 30


def test_dots_stay_on_screen():
    d = spin_illusion(2)
    for f in range(0, d.frames, 15):
        for _, x, y, r, _c in _discs(d.ops(f)):
            assert r <= x <= 1080 - r and 300 <= y <= 1450


def test_built_via_catalog():
    d = build("spin_illusion", 4, level="hard")
    assert d.family == "spin_illusion" and d.params["drill_id"] == "spin_illusion"
