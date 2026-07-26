"""
Tests for pipeline/background.py (Phase 2).
MoviePy rendering is tested at unit level — no actual video encode in fast tests.
Slow tests (write_videofile) are marked with @pytest.mark.slow.
"""
import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np
import PIL.Image
import pytest

# Patch Pillow ANTIALIAS before moviepy import
if not hasattr(PIL.Image, "ANTIALIAS"):
    PIL.Image.ANTIALIAS = PIL.Image.LANCZOS

from pipeline.background import (
    _random_variation,
    _apply_tint,
    _apply_overlay,
    ken_burns_fallback,
    pick_clip,
    get_background,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def empty_db():
    schema = (Path(__file__).parent.parent / "db" / "schema.sql").read_text()
    conn = sqlite3.connect(":memory:")
    conn.executescript(schema)
    yield conn
    conn.close()


@pytest.fixture
def db_with_clips(empty_db):
    clips = [
        ("/fake/clip1.mp4", "veo",   "calm",      "blue",   6.0, 0),
        ("/fake/clip2.mp4", "kling", "energetic", "warm",   5.0, 3),
        ("/fake/clip3.mp4", "veo",   "calm",      "golden", 6.0, 1),
    ]
    for path, provider, mood, color, dur, used in clips:
        empty_db.execute(
            """INSERT INTO background_clips
               (file_path, provider, mood_tag, color_tag, duration, times_used)
               VALUES (?,?,?,?,?,?)""",
            (path, provider, mood, color, dur, used),
        )
    empty_db.commit()
    return empty_db


# ── _random_variation ─────────────────────────────────────────────────────────

class TestRandomVariation:
    def test_returns_all_keys(self):
        v = _random_variation()
        assert set(v.keys()) == {"crop_x", "crop_y", "speed", "tint", "overlay", "start_offset"}

    def test_crop_in_bounds(self):
        for _ in range(20):
            v = _random_variation()
            assert 0.0 <= v["crop_x"] <= 0.15
            assert 0.0 <= v["crop_y"] <= 0.10

    def test_start_offset_in_bounds(self):
        for _ in range(20):
            v = _random_variation()
            assert 0.0 <= v["start_offset"] <= 0.3

    def test_speed_valid_choice(self):
        valid = {0.75, 0.85, 1.0, 1.15, 1.25}
        for _ in range(20):
            assert _random_variation()["speed"] in valid

    def test_tint_valid_choice(self):
        valid = {None, "warm", "cool", "muted", "vivid"}
        for _ in range(20):
            assert _random_variation()["tint"] in valid

    def test_overlay_valid_choice(self):
        valid = {None, "grain", "vignette", "gradient"}
        for _ in range(20):
            assert _random_variation()["overlay"] in valid


# ── _apply_tint ───────────────────────────────────────────────────────────────

class TestApplyTint:
    def _solid_clip(self, color=(128, 128, 128), duration=1.0):
        from moviepy.editor import ColorClip
        return ColorClip(size=(64, 64), color=color, duration=duration)

    def test_none_tint_returns_unchanged_type(self):
        clip = self._solid_clip()
        result = _apply_tint(clip, None)
        assert result is clip  # same object, no transform

    def test_warm_tint_boosts_red(self):
        clip = self._solid_clip(color=(100, 100, 100))
        result = _apply_tint(clip, "warm")
        frame = result.get_frame(0)
        assert frame[0, 0, 0] > frame[0, 0, 2]  # red > blue

    def test_cool_tint_boosts_blue(self):
        clip = self._solid_clip(color=(100, 100, 100))
        result = _apply_tint(clip, "cool")
        frame = result.get_frame(0)
        assert frame[0, 0, 2] > frame[0, 0, 0]  # blue > red

    def test_muted_darkens_frame(self):
        clip = self._solid_clip(color=(200, 200, 200))
        result = _apply_tint(clip, "muted")
        orig_mean = 200
        muted_mean = result.get_frame(0).mean()
        assert muted_mean < orig_mean

    def test_vivid_brightens_frame(self):
        clip = self._solid_clip(color=(100, 100, 100))
        result = _apply_tint(clip, "vivid")
        vivid_mean = result.get_frame(0).mean()
        assert vivid_mean > 100

    def test_output_stays_uint8(self):
        clip = self._solid_clip(color=(240, 10, 10))
        for tint in ["warm", "cool", "muted", "vivid"]:
            frame = _apply_tint(clip, tint).get_frame(0)
            assert frame.dtype == np.uint8
            assert frame.max() <= 255
            assert frame.min() >= 0


# ── _apply_overlay ────────────────────────────────────────────────────────────

class TestApplyOverlay:
    def _solid_clip(self, color=(128, 128, 128), size=(64, 64), duration=1.0):
        from moviepy.editor import ColorClip
        return ColorClip(size=size, color=color, duration=duration)

    def test_none_overlay_returns_same(self):
        clip = self._solid_clip()
        result = _apply_overlay(clip, None)
        assert result is clip

    def test_vignette_darkens_corners(self):
        clip = self._solid_clip(color=(200, 200, 200), size=(64, 64))
        result = _apply_overlay(clip, "vignette")
        frame = result.get_frame(0)
        center = frame[32, 32].mean()
        corner = frame[0, 0].mean()
        assert center > corner  # center brighter than corner

    def test_grain_changes_pixel_values(self):
        clip = self._solid_clip(color=(128, 128, 128), size=(64, 64))
        result = _apply_overlay(clip, "grain")
        orig = clip.get_frame(0)
        noisy = result.get_frame(0)
        assert not np.array_equal(orig, noisy)

    def test_grain_output_in_uint8_range(self):
        clip = self._solid_clip(color=(5, 5, 5), size=(64, 64))
        result = _apply_overlay(clip, "grain")
        frame = result.get_frame(0)
        assert frame.dtype == np.uint8
        assert frame.min() >= 0
        assert frame.max() <= 255

    def test_gradient_clip_has_correct_duration(self):
        clip = self._solid_clip(color=(200, 200, 200), size=(64, 64), duration=2.0)
        result = _apply_overlay(clip, "gradient")
        assert result.duration == pytest.approx(2.0)


# ── ken_burns_fallback ────────────────────────────────────────────────────────

class TestKenBurnsFallback:
    def test_output_size(self):
        clip = ken_burns_fallback(duration=2.0)
        assert clip.size == (1080, 1920)

    def test_output_duration(self):
        clip = ken_burns_fallback(duration=3.0)
        assert clip.duration == pytest.approx(3.0)

    def test_frame_is_valid(self):
        clip = ken_burns_fallback(duration=1.0)
        frame = clip.get_frame(0)
        assert frame.shape == (1920, 1080, 3)
        assert frame.dtype == np.uint8

    @pytest.mark.slow
    def test_writes_video_file(self, tmp_path):
        clip = ken_burns_fallback(duration=2.0)
        out = str(tmp_path / "test.mp4")
        clip.write_videofile(out, fps=24, logger=None)
        assert Path(out).exists()
        assert Path(out).stat().st_size > 0


# ── pick_clip ─────────────────────────────────────────────────────────────────

class TestPickClip:
    def test_returns_none_on_empty_library(self, empty_db):
        assert pick_clip(empty_db) is None

    def test_returns_clip_dict(self, db_with_clips):
        result = pick_clip(db_with_clips)
        assert result is not None
        assert "id" in result
        assert "file_path" in result
        assert "mood_tag" in result

    def test_mood_hint_filters_results(self, db_with_clips):
        result = pick_clip(db_with_clips, mood_hint="energetic")
        assert result["mood_tag"] == "energetic"

    def test_unknown_mood_hint_falls_back_to_any(self, db_with_clips):
        result = pick_clip(db_with_clips, mood_hint="nonexistent_mood")
        assert result is not None  # falls back to any clip

    def test_prefers_least_used(self, db_with_clips):
        # clip1 has times_used=0, clip3 has times_used=1, clip2 has times_used=3
        # pick_clip without mood should return clip1 (least used)
        result = pick_clip(db_with_clips)
        assert result["file_path"] == "/fake/clip1.mp4"


# ── get_background ────────────────────────────────────────────────────────────

class TestGetBackground:
    def test_empty_library_returns_ken_burns(self, empty_db):
        clip, params = get_background(empty_db, target_duration=2.0)
        assert params.get("fallback") == "ken_burns"
        assert clip.size == (1080, 1920)
        assert clip.duration == pytest.approx(2.0)

    def test_returns_tuple(self, empty_db):
        result = get_background(empty_db, target_duration=2.0)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_params_contain_clip_id_when_library_has_clips(self, db_with_clips, tmp_path):
        # Create a real short video clip for the test
        from moviepy.editor import ColorClip
        clip_path = tmp_path / "real_clip.mp4"
        ColorClip(size=(720, 1280), color=(50, 100, 150), duration=4.0).write_videofile(
            str(clip_path), fps=24, logger=None
        )
        # Update DB to point to real file
        db_with_clips.execute(
            "UPDATE background_clips SET file_path = ? WHERE id = 1",
            (str(clip_path),),
        )
        db_with_clips.commit()

        clip, params = get_background(db_with_clips, mood_hint="calm", target_duration=3.0)
        assert "clip_id" in params

    def test_fallback_on_missing_file(self, db_with_clips):
        # All clips point to non-existent files → should fall back gracefully
        clip, params = get_background(db_with_clips, target_duration=2.0)
        assert params.get("fallback") == "ken_burns"
