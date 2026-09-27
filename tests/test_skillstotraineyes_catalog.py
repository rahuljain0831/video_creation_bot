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
    for seed in (5, 6, 7, 8):
        d = build(drill_id, seed, level=level)
        assert 15 <= d.duration <= 30, f"{drill_id}/{level}/{seed} ran {d.duration}s"
        assert d.level == level
        assert d.params["level"] == level


# {drill: {knob: (derived params keys, transform of the rolled value)}}.
# Asserted against what the builder COMPUTED, using the value _knobs really
# returned (spied), so a builder that ignores a knob cannot pass.
_same = lambda v: (v,)
DERIVED = {
    "tracking": {},   # locked per level in drills.py; nothing is rolled
    "saccade": {"cells": (["cols", "rows"], tuple), "step": (["step"], _same)},
    "peripheral": {"gap": (["gap"], _same), "flash": (["flash"], _same),
                   "radius": (["radius"], _same)},
    "search": {"density": (["cols", "rows"], tuple), "look_s": (["look"], _same)},
}
_PATH_KNOBS = {"tempo": (["period"], lambda v: (round(6.5 / v, 3),)),
               "guide": (["guide"], _same), "occlude": (["occlude"], _same),
               "ghosts": (["ghosts"], _same), "shift": (["shift"], _same)}
for _id in ("slow_pursuit", "sine_pursuit", "triangle_pursuit", "zigzag_pursuit",
            "predictive_pursuit", "spatial_shift_pursuit", "ghosting_pursuit",
            "vertical_pursuit"):
    DERIVED[_id] = {n: _PATH_KNOBS[n] for n in knob_names(_id)}


@pytest.fixture
def spy(monkeypatch):
    from skillstotraineyes import drills
    rec = []
    real = drills._knobs
    monkeypatch.setattr(drills, "_knobs", lambda *a: rec.append(real(*a)) or rec[-1])
    return rec


def test_every_catalog_knob_is_in_the_derived_table():
    for e in load_catalog():
        if e["id"] not in ("pursuit_dual", "figure8", "grid_memory"):
            assert set(knob_names(e["id"])) <= set(DERIVED[e["id"]]), e["id"]


@pytest.mark.parametrize("drill_id", list(DERIVED))
def test_every_declared_knob_is_consumed(drill_id, spy):
    """Each rolled knob must equal the derived value the builder produced."""
    for level in LEVELS:
        for seed in range(4):
            spy.clear()
            d = build(drill_id, seed, level=level)
            for name, (keys, tf) in DERIVED[drill_id].items():
                assert tuple(d.params[k] for k in keys) == tf(spy[-1][name]),                     f"{drill_id}/{level}/{seed}: {name} not honoured"


@pytest.mark.parametrize("drill_id,base", [
    ("pursuit_dual", lambda r: (r.choice([(2, 3), (3, 4), (3, 2), (1, 2)]), r.uniform(0.55, 0.85))[1]),
    ("figure8", lambda r: r.uniform(0.7, 1.1)),
])
def test_tempo_scales_the_angular_rate(drill_id, base, spy):
    import random
    for level in LEVELS:
        for seed in range(4):
            spy.clear()
            d = build(drill_id, seed, level=level)
            assert d.params["w"] == round(base(random.Random(seed)) * spy[-1]["tempo"], 3)


def test_search_at_level_none_matches_the_original_code():
    """Pinned from search() at commit bed4b22: same RNG order and layout."""
    from skillstotraineyes.drills import search
    d = search(3)
    assert d.params == {"family": "search", "level": "", "cols": 5, "rows": 8, "look": 10}
    assert d.duration == 19.0
    o = d.ops(120)
    assert len(o) == 40
    assert o[0] == ("disc", 249.651838290384, 456.64645472846746, 32, (245, 245, 245))
    assert ("square", 807.8651364407907, 1236.1186924295234, 30, (245, 245, 245)) in o
    d4 = search(4)
    assert d4.params["cols"] == 6 and d4.params["rows"] == 8 and d4.params["look"] == 10
    assert ("square", 183.3281482011319, 587.4028620866635, 30, (245, 245, 245)) in d4.ops(120)


@pytest.mark.parametrize("drill_id,knobs", [
    ("peripheral", {"gap": 0.95}),
    ("search", {"look_s": 6}),
    ("saccade", {"step": 0.42, "cells": (2, 4)}),
    ("saccade", {"step": 1.0, "cells": (4, 6)}),
])
def test_tightest_knob_mixes_stay_in_the_duration_window(drill_id, knobs):
    e = entry(drill_id)
    cfg = {**e, "knobs": knobs}
    for seed in range(20):
        d = BUILDERS[e["builder"]](seed, None, "god", cfg)
        assert 15 <= d.duration <= 30, f"{drill_id}/{knobs}/{seed} ran {d.duration}s"


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


def _guide_dots(guide, extra=None, seed=4):
    from skillstotraineyes.drills import GUIDE_R, path_pursuit
    cfg = {**entry("sine_pursuit"), "knobs": {"path": "sine", "guide": guide, **(extra or {})}}
    d = path_pursuit(seed, None, "easy", cfg)
    return [op for op in d.ops(d.frames // 2) if op[0] == "disc" and op[3] == GUIDE_R]


def test_guide_visible_faint_hidden():
    vis, faint = _guide_dots("visible"), _guide_dots("faint")
    assert len(vis) > 30 and not _guide_dots("hidden")
    assert len(faint) > 30 and {op[4] for op in faint} != {op[4] for op in vis}
    assert {op[4] for op in faint} == {(44, 48, 62)}


def test_shift_guide_is_the_unwarped_base_curve():
    a = _guide_dots("visible", {"shift": 0.5})
    b = _guide_dots("visible", {"shift": 0.0})
    assert a == b


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
