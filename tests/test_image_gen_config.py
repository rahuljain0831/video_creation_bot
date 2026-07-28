"""Test that image_gen reads resolution/steps from cfg.image_gen."""
import types
from unittest.mock import patch, MagicMock
import pytest


def _make_cfg(hf_w=1024, hf_h=1792, hf_steps=28, poll_w=1080, poll_h=1920):
    cfg = MagicMock()
    cfg.HF_API_TOKEN = "tok"
    cfg.GOOGLE_AI_STUDIO_API_KEY = ""
    cfg.image_provider = {}
    cfg.image_gen = {
        "huggingface": {"width": hf_w, "height": hf_h, "num_inference_steps": hf_steps},
        "pollinations": {"width": poll_w, "height": poll_h},
    }
    return cfg


def test_hf_uses_config_dims():
    """_hf_generate must be called with width/height/steps from cfg.image_gen."""
    from pipeline import image_gen

    captured = {}

    def fake_hf(prompt, seed, token, width, height, num_inference_steps):
        captured.update({"w": width, "h": height, "steps": num_inference_steps})
        return b"\x89PNG", None

    with patch.object(image_gen, "_hf_generate", side_effect=fake_hf), \
         patch.object(image_gen, "load_quota_config", return_value={
             "fallback_chains": {"image_generation": ["huggingface"]}
         }), \
         patch.object(image_gen, "_save_image", return_value="/tmp/out.png"):
        image_gen.generate_scene_image(
            image_prompt="test",
            art_style_suffix="",
            seed=1,
            output_dir="/tmp",
            scene_index=0,
            video_id=1,
            cfg=_make_cfg(),
            conn=None,
        )

    assert captured["w"] == 1024
    assert captured["h"] == 1792
    assert captured["steps"] == 28


def test_pollinations_uses_config_dims():
    """_pollinations_generate must receive width/height from cfg.image_gen."""
    from pipeline import image_gen

    captured = {}

    def fake_poll(prompt, seed, width, height):
        captured.update({"w": width, "h": height})
        return b"\x89PNG", None

    with patch.object(image_gen, "_pollinations_generate", side_effect=fake_poll), \
         patch.object(image_gen, "load_quota_config", return_value={
             "fallback_chains": {"image_generation": ["pollinations"]}
         }), \
         patch.object(image_gen, "_save_image", return_value="/tmp/out.png"):
        image_gen.generate_scene_image(
            image_prompt="test",
            art_style_suffix="",
            seed=1,
            output_dir="/tmp",
            scene_index=0,
            video_id=1,
            cfg=_make_cfg(),
            conn=None,
        )

    assert captured["w"] == 1080
    assert captured["h"] == 1920
