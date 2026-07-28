"""Tests for pipeline/audio_bg.py — Pixabay fetch + cache logic."""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


def _make_cfg(api_key="testkey", audio_dir=None, tmp_path=None):
    cfg = MagicMock()
    cfg.PIXABAY_API_KEY = api_key
    cfg.paths = {"audio": str(tmp_path / "audio") if tmp_path else "data/audio"}
    return cfg


def _pixabay_response(audio_url="https://cdn.pixabay.com/audio/2024/chant.mp3"):
    resp = MagicMock()
    resp.json.return_value = {
        "total": 1,
        "hits": [{"id": 123, "name": "Om Chanting", "audio": audio_url}],
    }
    resp.raise_for_status.return_value = None
    return resp


def test_returns_none_when_no_api_key(tmp_path):
    from pipeline.audio_bg import fetch_bg_audio
    cfg = _make_cfg(api_key="", tmp_path=tmp_path)
    result = fetch_bg_audio("chanting meditation", cfg=cfg)
    assert result is None


def test_returns_cached_path_without_network(tmp_path):
    from pipeline.audio_bg import fetch_bg_audio
    cfg = _make_cfg(tmp_path=tmp_path)
    # Pre-populate cache
    cache_dir = tmp_path / "audio" / "bg"
    cache_dir.mkdir(parents=True)
    cached = cache_dir / "chanting-meditation.mp3"
    cached.write_bytes(b"FAKEMP3")

    with patch("pipeline.audio_bg.requests.get") as mock_get:
        result = fetch_bg_audio("chanting meditation", cfg=cfg)
        mock_get.assert_not_called()

    assert result == str(cached)


def test_downloads_and_caches_on_miss(tmp_path):
    from pipeline.audio_bg import fetch_bg_audio
    cfg = _make_cfg(tmp_path=tmp_path)

    with patch("pipeline.audio_bg.requests.get", return_value=_pixabay_response()) as mock_get, \
         patch("pipeline.audio_bg.urllib.request.urlretrieve") as mock_dl:
        mock_dl.side_effect = lambda url, path: Path(path).write_bytes(b"MP3DATA")
        result = fetch_bg_audio("chanting meditation", cfg=cfg)

    assert result is not None
    assert result.endswith("chanting-meditation.mp3")
    mock_get.assert_called_once()
    mock_dl.assert_called_once()


def test_returns_none_on_empty_hits(tmp_path):
    from pipeline.audio_bg import fetch_bg_audio
    cfg = _make_cfg(tmp_path=tmp_path)

    empty_resp = MagicMock()
    empty_resp.json.return_value = {"total": 0, "hits": []}
    empty_resp.raise_for_status.return_value = None

    with patch("pipeline.audio_bg.requests.get", return_value=empty_resp):
        result = fetch_bg_audio("chanting meditation", cfg=cfg)

    assert result is None


def test_returns_none_on_network_error(tmp_path):
    from pipeline.audio_bg import fetch_bg_audio
    cfg = _make_cfg(tmp_path=tmp_path)

    with patch("pipeline.audio_bg.requests.get", side_effect=Exception("timeout")):
        result = fetch_bg_audio("chanting meditation", cfg=cfg)

    assert result is None
