import subprocess
from pathlib import Path
import pytest


def _ffprobe_sar(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error",
         "-select_streams", "v:0",
         "-show_entries", "stream=sample_aspect_ratio,display_aspect_ratio",
         "-of", "default=noprint_wrappers=1", path],
        capture_output=True, text=True, timeout=15,
    )
    return r.stdout


@pytest.mark.slow
def test_ken_burns_sets_sar(tmp_path):
    """Ken Burns output clip must have SAR=1:1."""
    pytest.importorskip("PIL")
    from PIL import Image
    img_path = str(tmp_path / "test.png")
    Image.new("RGB", (576, 1024), color=(100, 150, 200)).save(img_path)
    dest = str(tmp_path / "kb.mp4")
    from pipeline.ffmpeg_assembler import _ken_burns_image
    _ken_burns_image(img_path, dest, duration=2.0, width=1080, height=1920, fps=30)
    out = _ffprobe_sar(dest)
    assert "sample_aspect_ratio=1:1" in out, f"SAR not set to 1:1. ffprobe output: {out!r}"
