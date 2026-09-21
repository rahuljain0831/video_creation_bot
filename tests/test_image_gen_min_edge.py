"""generate_image(min_short_edge=...) falls through to the next provider. No network."""
import io

import pytest
from PIL import Image

from pipeline import image_gen
from pipeline.image_gen import ImageGenError, generate_image


def _png(size):
    buf = io.BytesIO()
    Image.new("RGB", size, "green").save(buf, "PNG")
    return buf.getvalue()


def _setup(monkeypatch, sizes):
    calls = []
    providers = [{"name": n, "type": n} for n in sizes]
    monkeypatch.setattr(image_gen, "_load_providers", lambda: providers)
    monkeypatch.setattr(image_gen, "_HANDLERS", {
        n: (lambda p, *a, _n=n: (calls.append(_n), _png(sizes[_n]))[1]) for n in sizes})
    monkeypatch.setattr("pipeline.image_critic.nsfw_flagged", lambda *a, **k: False)
    return calls


def _gen(tmp_path, **kw):
    niche = {"id": "t", "allow_local_generation": False}
    return generate_image("a cat", niche, str(tmp_path), 0, cfg=None, **kw)


def test_small_provider_falls_through_to_the_next(monkeypatch, tmp_path):
    calls = _setup(monkeypatch, {"a": (576, 1024), "b": (1080, 1920)})
    out = _gen(tmp_path, min_short_edge=1024)
    assert Image.open(out).size == (1080, 1920)
    assert calls == ["a", "b"]


def test_all_small_raises(monkeypatch, tmp_path):
    _setup(monkeypatch, {"a": (576, 1024), "b": (600, 1000)})
    with pytest.raises(ImageGenError):
        _gen(tmp_path, min_short_edge=1024)


def test_default_accepts_any_size(monkeypatch, tmp_path):
    _setup(monkeypatch, {"a": (576, 1024)})
    assert Image.open(_gen(tmp_path)).size == (576, 1024)
