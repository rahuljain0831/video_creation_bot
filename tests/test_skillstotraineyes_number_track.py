"""Tests for skillstotraineyes/number_track.py."""
import pytest

from skillstotraineyes.drills import RED, build
from skillstotraineyes.number_track import NUMBER_LOCKED, number_track

TABLE = {"medium": (8, 44, 420, 11.5), "hard": (8, 32, 520, 15.0), "expert": (8, 26, 640, 18.5)}


def _last(d):
    return d.ops(d.frames - 1)


def _labels(ops):
    return [op[1] for op in ops if op[0] == "label"]


@pytest.mark.parametrize("level", list(TABLE))
def test_levels_match_table(level):
    n, ball, speed, move = TABLE[level]
    d = number_track(3, level=level)
    p = d.params
    assert (p["n"], p["ball"], p["speed"], p["move"]) == (n, ball, speed, move)
    assert NUMBER_LOCKED[level][:3] == (n, ball, float(speed))


@pytest.mark.parametrize("level,mapped", [("easy", "medium"), ("god", "expert")])
def test_easy_and_god_map_to_neighbours(level, mapped):
    assert number_track(2, level=level).params["move"] == TABLE[mapped][3]


def test_deterministic():
    a, b = number_track(7, level="hard"), number_track(7, level="hard")
    assert a.params == b.params
    assert a.ops(200) == b.ops(200)


@pytest.mark.parametrize("seed", range(4))
def test_numbers_are_a_permutation_and_answer_matches(seed):
    d = number_track(seed, level="medium")
    labels = _labels(_last(d))
    assert sorted(int(s) for s in labels) == list(range(1, 9))
    assert 1 <= d.params["answer"] <= 8


@pytest.mark.parametrize("level", list(TABLE))
def test_answer_never_revealed(level):
    ops = _last(number_track(5, level=level))
    assert not any(op[0] == "ring" for op in ops)
    assert not any(op[0] == "disc" and op[4] == RED for op in ops)


def test_target_red_at_start():
    ops = number_track(1, level="medium").ops(0)
    assert sum(1 for op in ops if op[0] == "disc" and op[4] == RED) == 1


def test_only_expert_flashes_and_never_red():
    assert number_track(1, level="medium").params["flashes"] == 0
    assert number_track(1, level="hard").params["flashes"] == 0
    d = number_track(1, level="expert")
    assert d.params["flashes"] == 3
    colours = {op[4] for f in range(d.frames) for op in d.ops(f) if op[0] == "disc"}
    assert len(colours) > 2               # white + red + at least one flash colour
    t_red_end = 5.0                       # 2s still + 3s red moving
    late = {op[4] for f in range(round(t_red_end * 30) + 1, d.frames)
            for op in d.ops(f) if op[0] == "disc"}
    assert RED not in late


def test_question_and_voice_hide_answer():
    d = number_track(4, level="medium")
    q = "Which number was it? Comment below!"
    assert any(op[0] == "text" and op[1] == q for op in _last(d))
    assert len(d.voice) == 2
    assert d.voice[1][1] == q


def test_built_via_catalog():
    d = build("number_track", 1, level="medium")
    assert d.family == "number_track" and d.params["drill_id"] == "number_track"
