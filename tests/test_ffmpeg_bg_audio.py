"""Test that assemble_from_images passes bg_audio_path to ffmpeg filter_complex."""
import subprocess
from pathlib import Path
from unittest.mock import patch, call, MagicMock
import pytest


def _make_cfg(volume=0.12):
    cfg = MagicMock()
    cfg.video = {"resolution": [1080, 1920], "fps": 30, "caption_style": {}}
    cfg.background_audio = {"volume": volume}
    return cfg


def test_bg_audio_path_none_uses_simple_mux(tmp_path):
    """Without bg_audio_path, assembler must NOT use filter_complex."""
    from pipeline.ffmpeg_assembler import assemble_from_images

    img = tmp_path / "scene_0.png"
    img.write_bytes(b"PNG")
    audio = tmp_path / "narration.mp3"
    audio.write_bytes(b"MP3")
    out = str(tmp_path / "out.mp4")

    ffmpeg_calls = []

    def fake_ffmpeg(*args, check=True):
        ffmpeg_calls.append(list(args))
        result = MagicMock()
        result.returncode = 0
        return result

    with patch("pipeline.ffmpeg_assembler._ffmpeg", side_effect=fake_ffmpeg), \
         patch("pipeline.ffmpeg_assembler._get_duration", return_value=5.0):
        assemble_from_images(
            scene_images=[str(img)],
            audio_path=str(audio),
            output_path=out,
            scenes=None,
            cfg=_make_cfg(),
            bg_audio_path=None,
        )

    # No call should contain filter_complex
    all_args = [arg for call_args in ffmpeg_calls for arg in call_args]
    assert "-filter_complex" not in all_args


def test_bg_audio_path_provided_uses_filter_complex(tmp_path):
    """With bg_audio_path, assembler must use filter_complex with amix."""
    from pipeline.ffmpeg_assembler import assemble_from_images

    img = tmp_path / "scene_0.png"
    img.write_bytes(b"PNG")
    audio = tmp_path / "narration.mp3"
    audio.write_bytes(b"MP3")
    bg = tmp_path / "chanting.mp3"
    bg.write_bytes(b"MP3")
    out = str(tmp_path / "out.mp4")

    ffmpeg_calls = []

    def fake_ffmpeg(*args, check=True):
        ffmpeg_calls.append(list(args))
        result = MagicMock()
        result.returncode = 0
        return result

    with patch("pipeline.ffmpeg_assembler._ffmpeg", side_effect=fake_ffmpeg), \
         patch("pipeline.ffmpeg_assembler._get_duration", return_value=5.0), \
         patch("pipeline.ffmpeg_assembler.Path") as mock_path:
        mock_path.return_value.exists.return_value = True
        mock_path.side_effect = lambda p: Path(p)
        assemble_from_images(
            scene_images=[str(img)],
            audio_path=str(audio),
            output_path=out,
            scenes=None,
            cfg=_make_cfg(),
            bg_audio_path=str(bg),
        )

    all_args = [arg for call_args in ffmpeg_calls for arg in call_args]
    assert "-filter_complex" in all_args
    # Volume and amix must appear in the filter_complex string
    filter_str = next(a for a in all_args if "amix" in str(a))
    assert "amix" in filter_str
    assert "aloop" in filter_str
