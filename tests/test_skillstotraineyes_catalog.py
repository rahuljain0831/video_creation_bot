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


# knob -> the params keys that report it
PARAM_KEYS = {"ball_r": ["ball"], "move_s": ["move"], "cells": ["cols", "rows"],
              "density": ["cols", "rows"], "look_s": ["look"]}


def _seen(drill_id, knob, level):
    keys = PARAM_KEYS.get(knob, [knob])
    return {tuple(build(drill_id, s, level=level).params[k] for k in keys) for s in range(6)}


@pytest.mark.parametrize("drill_id", FAMILIES)
def test_every_declared_knob_is_consumed(drill_id):
    """Same seed, different level: a knob the builder ignores yields identical
    params at easy and god (the seed alone decides), so the sets would be equal."""
    for knob in knob_names(drill_id):
        assert _seen(drill_id, knob, "easy") != _seen(drill_id, knob, "god"),             f"{drill_id} ignores knob {knob}"


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
