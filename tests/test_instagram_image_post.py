"""Tests for the Instagram feed-image publish path. No network: requests is stubbed."""
import json

import pytest

from pipeline import instagram_upload as ig


@pytest.fixture
def creds(tmp_path):
    p = tmp_path / "ig.json"
    p.write_text(json.dumps({"access_token": "tok", "ig_user_id": "123",
                             "app_id": "a", "app_secret": "s"}))
    return p


def test_temp_host_mime_follows_the_file():
    assert ig._mime_for("a.jpg") == "image/jpeg"
    assert ig._mime_for("a.jpeg") == "image/jpeg"
    assert ig._mime_for("a.png") == "image/png"
    assert ig._mime_for("a.mp4") == "video/mp4"


def test_image_container_uses_image_url_and_no_reels_type(monkeypatch):
    sent = {}

    class Resp:
        def raise_for_status(self): pass
        def json(self): return {"id": "container-1"}

    def fake_post(url, data=None, timeout=None):
        sent.update(data)
        return Resp()

    monkeypatch.setattr(ig.requests, "post", fake_post)
    cid = ig._create_media_container("123", "tok", caption="hi",
                                     image_url="https://x/y.jpg", media_type="IMAGE")
    assert cid == "container-1"
    assert sent["image_url"] == "https://x/y.jpg"
    assert "video_url" not in sent
    assert "media_type" not in sent, "IG rejects media_type=IMAGE on a feed photo"


def test_reel_container_is_unchanged(monkeypatch):
    sent = {}

    class Resp:
        def raise_for_status(self): pass
        def json(self): return {"id": "c2"}

    monkeypatch.setattr(ig.requests, "post",
                        lambda url, data=None, timeout=None: (sent.update(data), Resp())[1])
    ig._create_media_container("123", "tok", video_url="https://x/y.mp4", caption="c")
    assert sent["media_type"] == "REELS"
    assert sent["video_url"] == "https://x/y.mp4"


def test_upload_image_post_publishes(monkeypatch, tmp_path, creds):
    img = tmp_path / "scene.jpg"
    img.write_bytes(b"\xff\xd8\xff\x00")
    monkeypatch.setattr(ig, "_upload_to_temp_host", lambda p, mime=None: "https://x/scene.jpg")
    monkeypatch.setattr(ig, "_create_media_container", lambda **kw: "c3")
    monkeypatch.setattr(ig, "_poll_container_status", lambda *a: "FINISHED")
    monkeypatch.setattr(ig, "_publish_container", lambda *a: "media-9")
    assert ig.upload_image_post(img, "find it", ["eyes"], credentials_file=creds) == "media-9"


def test_upload_image_post_rejects_a_missing_file(tmp_path, creds):
    with pytest.raises(FileNotFoundError):
        ig.upload_image_post(tmp_path / "gone.jpg", "c", credentials_file=creds)
