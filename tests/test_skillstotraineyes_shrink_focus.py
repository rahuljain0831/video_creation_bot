"""Tests for skillstotraineyes/shrink_focus.py."""
from skillstotraineyes.drills import build, params_key
from skillstotraineyes.shrink_focus import circle_at, shrink_focus

Q = "When did it disappear for you? Comment your number!"


def _circle(ops):
    return [op for op in ops if op[0] == "disc"][0]


def _counter(ops):
    return [op[1] for op in ops if op[0] == "text" and op[1].isdigit()]


def test_duration_and_badge():
    d = shrink_focus(1, level="hard")
    assert d.duration == 26.0 and d.level == ""
    first = [op[1] for op in d.ops(0) if op[0] == "text"]
    assert "FOCUS CHALLENGE" in first and "How small can you see?" in first


def test_circle_shrinks_and_fades_monotonically():
    d = shrink_focus(2)
    prev_r, prev_c = 1e9, 1e9
    for f in range(60, 22 * 30, 15):
        r, contrast = circle_at(d, f / 30)
        assert r <= prev_r and contrast <= prev_c
        prev_r, prev_c = r, contrast
    assert circle_at(d, 2.0)[0] >= 140 and circle_at(d, 22.0)[0] <= 1.5
    assert circle_at(d, 22.0)[1] < 0.1


def test_counter_runs_1_to_100():
    d = shrink_focus(3)
    seen = [int(c) for f in range(60, 22 * 30 + 1) for c in _counter(d.ops(f))]
    assert seen[0] == 1 and max(seen) == 100
    assert seen == sorted(seen)


def test_question_at_end_and_no_answer():
    d = shrink_focus(4)
    last = [op[1] for op in d.ops(d.frames - 1) if op[0] == "text"]
    assert Q in last
    assert d.voice[1][1] == Q


def test_deterministic_and_varies():
    assert shrink_focus(5).params == shrink_focus(5).params
    assert len({params_key(shrink_focus(s).params) for s in range(10)}) > 1


def test_circle_never_below_one_pixel():
    d = shrink_focus(6)
    assert all(_circle(d.ops(f))[3] >= 1 for f in range(d.frames))


def test_built_via_catalog():
    d = build("shrink_focus", 7, level="god")
    assert d.family == "shrink_focus" and d.params["drill_id"] == "shrink_focus"
