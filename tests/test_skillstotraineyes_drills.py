"""Tests for skillstotraineyes/drills.py and renderer.py"""
import json
import subprocess

import pytest

from skillstotraineyes.drills import (
    BUILDERS, FAMILIES, H, SAFE_BOTTOM, W, build, pick_family,
)


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_drill_shape(family, seed):
    d = build(family, seed)
    assert 15 <= d.duration <= 30, f"{family} duration {d.duration}"
    assert d.frames == round(d.duration * d.fps)
    for f in range(0, d.frames, 7):
        for op in d.ops(f):
            if op[0] == "text":
                _, s, x, y, size, _c = op
                # Text must clear the platform UI band (centre + half a line).
                assert y + size * 0.6 < SAFE_BOTTOM, f"{family} text at y={y}"
            else:
                x, y = op[1], op[2]
            assert 0 < x < W and 0 < y < H, f"{family} op off-frame: {op}"


@pytest.mark.parametrize("family", FAMILIES)
def test_same_seed_same_drill(family):
    a, b = build(family, 11), build(family, 11)
    assert a.params == b.params and a.duration == b.duration
    assert all(a.ops(f) == b.ops(f) for f in range(0, a.frames, 13))


def test_ends_with_cta():
    d = build("tracking", 1)
    texts = [op[1] for op in d.ops(d.frames - 1) if op[0] == "text"]
    assert texts == ["Follow for more"]


def test_text_override_reaches_frames():
    d = build("search", 1, {"hook": "Spot the odd one"})
    assert any(op[:2] == ("text", "Spot the odd one") for op in d.ops(0))


def test_tracking_target_is_id_and_recolours():
    d = build("tracking", 5)
    def red_discs(f):
        return [op for op in d.ops(f) if op[0] == "disc" and op[4] == (235, 40, 45)]
    assert len(red_discs(0)) == 1            # one red ball at the start
    assert len(red_discs(int(5 * d.fps))) == 0   # turned white mid-run


def test_pick_family_never_repeats():
    last = ["saccade"]
    assert all(pick_family(s, last) != "saccade" for s in range(50))


@pytest.mark.slow
def test_smoke_render_dimensions_and_audio_length(tmp_path):
    from skillstotraineyes.renderer import render
    d = build("tracking", 1)
    out = render(d, tmp_path / "t.mp4", max_frames=90)   # 3s
    probe = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", out],
        capture_output=True, text=True).stdout)
    v = next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (1080, 1920)
    assert v["r_frame_rate"] == "30/1"
    assert int(v["nb_frames"]) == 90


@pytest.mark.slow
def test_audio_length_matches_video(tmp_path):
    from skillstotraineyes.renderer import render
    from pipeline.horror_audio import build_ambience
    d = build("search", 1)
    bed, _ = build_ambience(4.0, tmp_path / "bed.wav", seed=1, bed="musicbox")
    out = render(d, tmp_path / "t.mp4", bed=bed, max_frames=120)   # 4s
    probe = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", out],
        capture_output=True, text=True).stdout)
    dur = {s["codec_type"]: float(s["duration"]) for s in probe["streams"]}
    assert abs(dur["audio"] - dur["video"]) < 0.1


from skillstotraineyes.difficulty import LEVELS, LEVEL_LABEL


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("level", LEVELS)
def test_badge_shows_at_the_start(family, level):
    d = build(family, 3, level=level)
    first = [op[1] for op in d.ops(0) if op[0] == "text"]
    assert LEVEL_LABEL[level] in first
    assert d.level == level


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("level", LEVELS)
def test_badge_costs_no_duration(family, level):
    assert 15 <= build(family, 3, level=level).duration <= 30


@pytest.mark.parametrize("family", FAMILIES)
def test_badge_is_gone_by_the_end(family):
    d = build(family, 3, level="god")
    last = [op[1] for op in d.ops(d.frames - 1) if op[0] == "text"]
    assert LEVEL_LABEL["god"] not in last
