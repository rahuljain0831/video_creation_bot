"""Tests for skillstotraineyes/hidden_object.py. No network: image_gen is stubbed."""
import json

import pytest
from PIL import Image

from skillstotraineyes import hidden_object as ho
from skillstotraineyes.wording import reveals_position

SCENE = {"environment": "a dry rocky mountainside at noon",
         "target": "a sand-coloured snake",
         "difficulty_note": "coiled in shadow between two boulders"}


def test_prompt_stays_under_the_flux_ceiling():
    """FLUX truncates near 77 CLIP tokens; a long prompt loses the subject."""
    for seed in range(20):
        scene = {**SCENE, "environment": "a " + "very dense tangled overgrown " * 6 + "jungle"}
        assert len(ho.build_prompt(scene).split()) <= ho.MAX_PROMPT_WORDS


def test_prompt_names_the_target_early():
    words = ho.build_prompt(SCENE).split()
    assert "snake" in " ".join(words[:25]), "the target must survive truncation"


def test_prompt_asks_for_a_hidden_target():
    p = ho.build_prompt(SCENE).lower()
    assert "camouflaged" in p or "hidden" in p


def test_human_target_survives_the_policy():
    """R1: human_policy 'never' would strip 'sniper' out of its own prompt."""
    from pipeline.image_policy import apply_human_policy, resolve_human_policy
    cfgd = json.loads(open("settings.json", encoding="utf-8").read())
    niche = next(n for n in cfgd["niches"] if n["id"] == "skillstotraineyes")
    policy = resolve_human_policy(niche, {})
    scene = {**SCENE, "target": "a sniper in a ghillie suit"}
    positive, _neg = apply_human_policy(ho.build_prompt(scene), "", policy)
    assert "sniper" in positive


def test_generate_rejects_a_low_resolution_result(monkeypatch, tmp_path):
    """R3: an upscaled 576px frame cannot hide a small object."""
    small = tmp_path / "small.png"
    Image.new("RGB", (576, 1024), "green").save(small)
    monkeypatch.setattr(ho, "generate_image", lambda **kw: str(small))
    with pytest.raises(ho.HiddenObjectError, match="1024"):
        ho.generate(SCENE, tmp_path, seed=1, niche={"id": "skillstotraineyes"}, cfg=None)


def test_generate_converts_to_jpeg(monkeypatch, tmp_path):
    """R4: the Graph API rejects PNG for a feed photo."""
    big = tmp_path / "big.png"
    Image.new("RGB", (1080, 1350), "green").save(big)
    monkeypatch.setattr(ho, "generate_image", lambda **kw: str(big))
    out = ho.generate(SCENE, tmp_path, seed=1, niche={"id": "skillstotraineyes"}, cfg=None)
    assert out.endswith(".jpg")
    assert Image.open(out).format == "JPEG"


def test_invent_scene_falls_back_when_the_llm_dies(monkeypatch):
    monkeypatch.setattr(ho, "call_llm", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("dead")))
    scene = ho.invent_scene(3, [], None)
    assert scene["environment"] and scene["target"]


def test_invent_scene_rejects_junk(monkeypatch):
    monkeypatch.setattr(ho, "call_llm", lambda *a, **k: ('{"environment": "", "target": ""}', "f"))
    scene = ho.invent_scene(3, [], None)
    assert scene["environment"] and scene["target"], "empty fields must fall back"


def test_invent_scene_avoids_recent_targets(monkeypatch):
    monkeypatch.setattr(ho, "call_llm",
                        lambda p, **k: ('{"environment": "x", "target": "a snake"}', "f")
                        if "snake" not in p else ('{"environment": "y", "target": "a fox"}', "f"))
    scene = ho.invent_scene(3, [{"target": "a snake"}], None)
    assert scene["target"] != "a snake"


@pytest.mark.parametrize("bad", [
    "It is in the top left corner", "look behind the rock",
    "hiding under the third tree", "check the bottom-right",
])
def test_position_words_are_caught(bad):
    """R10: the caption must never give the answer away."""
    assert reveals_position(bad)


@pytest.mark.parametrize("ok", [
    "Can you find it? Most people need three tries.",
    "One snake, one photo. Drop your answer below.",
])
def test_ordinary_captions_pass(ok):
    assert not reveals_position(ok)


def test_wide_result_is_cropped_to_4_5_only_if_taller(monkeypatch, tmp_path):
    tall = tmp_path / "tall.png"
    Image.new("RGB", (1080, 1920), "green").save(tall)
    monkeypatch.setattr(ho, "generate_image", lambda **kw: str(tall))
    out = ho.generate(SCENE, tmp_path, seed=2, niche={"id": "skillstotraineyes"}, cfg=None)
    w, h = Image.open(out).size
    assert (w, h) == (1080, 1350)


def test_landscape_result_is_left_alone(monkeypatch, tmp_path):
    wide = tmp_path / "wide.png"
    Image.new("RGB", (1600, 1100), "green").save(wide)
    monkeypatch.setattr(ho, "generate_image", lambda **kw: str(wide))
    out = ho.generate(SCENE, tmp_path, seed=3, niche={"id": "skillstotraineyes"}, cfg=None)
    assert Image.open(out).size == (1600, 1100)


def test_import_does_not_load_llm_router_or_image_gen():
    import subprocess, sys
    code = ("import sys, skillstotraineyes.hidden_object;"
            "assert 'llm_router' not in sys.modules;"
            "assert 'pipeline.image_gen' not in sys.modules;"
            "assert 'config' not in sys.modules")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_generate_asks_the_provider_chain_for_the_minimum_edge(monkeypatch, tmp_path):
    seen = {}
    big = tmp_path / "big.png"
    Image.new("RGB", (1080, 1350), "green").save(big)
    monkeypatch.setattr(ho, "generate_image", lambda **kw: (seen.update(kw), str(big))[1])
    ho.generate(SCENE, tmp_path, seed=1, niche={"id": "skillstotraineyes"}, cfg=None)
    assert seen["min_short_edge"] == ho.MIN_SHORT_EDGE
