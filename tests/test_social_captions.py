"""Tests for pipeline/social_captions.py"""
import pytest
from unittest.mock import patch, MagicMock


_SCRIPT = {
    "story_title": "The Birth of a Quasar",
    "scenes": [
        {"narration": "In the early universe, a black hole began to feed.", "image_prompt": "x"},
        {"narration": "Energy erupted across billions of light-years.", "image_prompt": "x"},
    ],
}
_NICHE = {"id": "space_science", "tone": "awe-inspiring, curious"}

_LLM_JSON = """{
  "youtube":   {"caption": "Quasars: the brightest objects in the universe.", "hashtags": ["#space", "#quasar", "#science", "#cosmos", "#universe"]},
  "instagram": {"caption": "When black holes feast, the cosmos lights up.", "hashtags": ["#space", "#quasar", "#blackhole", "#astronomy", "#cosmos", "#universe", "#nasa", "#sciencefacts", "#astrophysics", "#deepspace", "#milkyway", "#galaxies", "#spaceexploration", "#sciencelovers", "#cosmology"]},
  "facebook":  {"caption": "Did you know quasars outshine entire galaxies?", "hashtags": ["#space", "#quasar", "#science"]},
  "tiktok":    {"caption": "The universe's most powerful flashlights. #quasar", "hashtags": ["#space", "#quasar", "#learnontiktok", "#sciencetok", "#universe", "#blackhole", "#fyp"]},
  "pinterest": {"caption": "Quasars — ancient cosmic beacons from the dawn of time.", "hashtags": ["#space", "#quasar", "#astronomy", "#cosmos", "#science"]},
  "linkedin":  {"caption": "Quasars remind us how little we know about the cosmos.", "hashtags": ["#space", "#science", "#learning"]}
}"""


def test_returns_all_six_platforms():
    from pipeline.social_captions import generate_social_captions
    with patch("pipeline.social_captions.call_llm", return_value=(_LLM_JSON, "gemini/test")):
        result = generate_social_captions(_SCRIPT, _NICHE)
    assert set(result.keys()) == {"youtube", "instagram", "facebook", "tiktok", "pinterest", "linkedin"}


def test_each_platform_has_caption_and_hashtags():
    from pipeline.social_captions import generate_social_captions
    with patch("pipeline.social_captions.call_llm", return_value=(_LLM_JSON, "gemini/test")):
        result = generate_social_captions(_SCRIPT, _NICHE)
    for platform, data in result.items():
        assert "caption" in data, f"{platform} missing caption"
        assert "hashtags" in data, f"{platform} missing hashtags"
        assert isinstance(data["hashtags"], list), f"{platform} hashtags not a list"


def test_returns_empty_dict_on_llm_failure():
    from pipeline.social_captions import generate_social_captions
    with patch("pipeline.social_captions.call_llm", side_effect=Exception("LLM timeout")):
        result = generate_social_captions(_SCRIPT, _NICHE)
    assert result == {}


def test_returns_empty_dict_on_bad_json():
    from pipeline.social_captions import generate_social_captions
    with patch("pipeline.social_captions.call_llm", return_value=("not json at all", "gemini/test")):
        result = generate_social_captions(_SCRIPT, _NICHE)
    assert result == {}


def test_format_telegram_message():
    from pipeline.social_captions import format_telegram_message
    captions = {
        "youtube":   {"caption": "Test caption", "hashtags": ["#space", "#test"]},
        "instagram": {"caption": "Insta caption", "hashtags": ["#a", "#b"]},
        "facebook":  {"caption": "FB caption",   "hashtags": ["#x"]},
        "tiktok":    {"caption": "TT caption",   "hashtags": ["#y"]},
        "pinterest": {"caption": "Pin caption",  "hashtags": ["#z"]},
        "linkedin":  {"caption": "LI caption",   "hashtags": ["#w"]},
    }
    msg = format_telegram_message("The Birth of a Quasar", captions)
    assert "YouTube" in msg
    assert "Instagram" in msg
    assert "TikTok" in msg
    assert "#space" in msg
    assert "The Birth of a Quasar" in msg
