"""Tests for skillstotraineyes/colour_plates.py."""
import pytest

from skillstotraineyes.colour_plates import PLATES_LOCKED, SIM, make_plate, separation
from skillstotraineyes.difficulty import LEVELS
from skillstotraineyes.drills import build

# Share of a typical eye's figure/background contrast the targeted eye keeps.
# Protan/deutan are rank-2 in Machado's model, so only jitter noise remains;
# tritan's matrix is full rank, which caps how well any tritan plate can hide.
MAX_RESIDUAL = {"protan": 0.06, "deutan": 0.06, "tritan": 0.2}


@pytest.mark.parametrize("level", LEVELS)
def test_level_table_fits_30s_and_matches_build(level):
    n, sec, _t, controls, _types, _style = PLATES_LOCKED[level]
    d = build("colour_plates", 2, level=level)
    assert 15 <= d.duration <= 30
    assert d.params["n"] == n and d.params["sec"] == sec
    kinds = [k for k, _ in d.params["plates"]]
    assert len(kinds) == n and kinds.count("control") == controls


@pytest.mark.parametrize("kind", ["protan", "deutan", "tritan"])
@pytest.mark.parametrize("level", LEVELS)
def test_type_plate_hides_from_its_type_only(kind, level):
    t = PLATES_LOCKED[level][2]
    for seed in range(3):
        p = make_plate(seed, kind, 8, t, 700)
        normal = separation(p)
        assert normal > 0.1, f"{kind}/{level}: too faint for typical vision ({normal:.3f})"
        assert separation(p, kind) / normal < MAX_RESIDUAL[kind]


@pytest.mark.parametrize("seed", range(3))
def test_control_plate_reads_for_every_type(seed):
    p = make_plate(seed, "control", 5, 0.3, 700)
    normal = separation(p)
    assert all(separation(p, s) > 0.5 * normal for s in SIM)


@pytest.mark.parametrize("number,size", [(8, 700), (74, 560), (47, 460)])
def test_number_has_enough_dots_to_read(number, size):
    assert make_plate(1, "protan", number, 0.2, size).figure.sum() >= 40


def test_god_uses_two_digit_numbers():
    assert all(num >= 10 for _, num in build("colour_plates", 4, level="god").params["plates"])


def test_frames_walk_hook_plates_end():
    d = build("colour_plates", 5, level="medium")
    n, sec = d.params["n"], d.params["sec"]
    texts = lambda f: [o[1] for o in d.ops(f) if o[0] == "text"]
    assert "Can you spot all the numbers?" in texts(0)
    mid = d.ops(int((3.0 + 2.5 * sec) * d.fps))
    assert [o[0] for o in mid] == ["image", "text"] and mid[1][1] == f"3 / {n}"
    assert texts(d.frames - 1) == [f"How many of the {n} did you find? Comment and let us know!"]
