"""facebook_upload: 3-90s videos publish as Reels, everything else as a page video."""
import json

import pytest

import pipeline.facebook_upload as fb


class _Resp:
    def __init__(self, payload):
        self._payload = payload
        self.ok = True
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


@pytest.fixture
def page(monkeypatch, tmp_path):
    creds = tmp_path / "creds.json"
    creds.write_text(json.dumps({"access_token": "U", "page_id": "P", "app_id": "a", "app_secret": "s"}))
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x" * 10)
    monkeypatch.setattr(fb, "_get_page_access_token", lambda user_token, page_id: "PT")
    return creds, video


def _route(monkeypatch, duration):
    monkeypatch.setattr(fb, "_video_duration", lambda path: duration)
    calls = {}
    monkeypatch.setattr(fb, "_upload_reel", lambda *a: calls.setdefault("reel", a) and "R1")
    monkeypatch.setattr(fb, "_upload_video_direct", lambda **kw: calls.setdefault("direct", kw) and "V1")
    return calls


@pytest.mark.parametrize("duration, kind", [(20.0, "reel"), (90.0, "reel"), (100.7, "direct"),
                                            (2.0, "direct"), (None, "direct")])
def test_routes_by_duration(monkeypatch, page, duration, kind):
    creds, video = page
    calls = _route(monkeypatch, duration)
    fb.upload_video(video, "Hook", "Body #tag", credentials_file=creds)
    assert list(calls) == [kind]


def test_reel_text_leads_with_the_title_once(monkeypatch, page):
    creds, video = page
    calls = _route(monkeypatch, 20.0)
    fb.upload_video(video, "Hook", "Body #tag", credentials_file=creds)
    assert calls["reel"][3] == "Hook\n\nBody #tag"

    calls.clear()
    fb.upload_video(video, "Hook", "Hook\n\nBody #tag", credentials_file=creds)
    assert calls["reel"][3] == "Hook\n\nBody #tag"


def test_upload_reel_start_upload_finish(monkeypatch, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x" * 10)
    posts = []

    def fake_post(url, data=None, headers=None, timeout=None):
        posts.append((url, data if isinstance(data, dict) else None, headers))
        if url.endswith("/video_reels") and data["upload_phase"] == "start":
            return _Resp({"video_id": "R1", "upload_url": "https://rupload.facebook.com/x"})
        return _Resp({"success": True})

    monkeypatch.setattr(fb.requests, "post", fake_post)
    assert fb._upload_reel("P", "PT", video, "Hook\n\nBody") == "R1"

    (u1, d1, _), (u2, _, h2), (u3, d3, _) = posts
    assert u1.endswith("/P/video_reels") and d1["upload_phase"] == "start"
    assert u2 == f"{fb.RUPLOAD_BASE}/R1"
    assert h2 == {"Authorization": "OAuth PT", "offset": "0", "file_size": "10"}
    assert u3.endswith("/P/video_reels")
    assert d3["upload_phase"] == "finish" and d3["video_id"] == "R1"
    assert d3["video_state"] == "PUBLISHED" and d3["description"] == "Hook\n\nBody"
