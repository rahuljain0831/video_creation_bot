"""Tests for pipeline/scheduler.py — mock all external calls."""
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_db():
    """Return an in-memory DB initialised from schema.sql."""
    schema = (Path(__file__).parent.parent / "db" / "schema.sql").read_text()
    conn = sqlite3.connect(":memory:")
    conn.executescript(schema)
    return conn


_MINIMAL_SCHEDULER_CFG = {
    "enabled": True,
    "default_times_ist": {
        "youtube":   ["17:00", "20:00", "12:00"],
        "instagram": ["19:00", "21:00", "11:00"],
        "facebook":  ["13:00", "18:00", "21:00"],
    },
    "min_gap_minutes": 30,
    "exploration_rate": 0.0,   # disabled by default so tests are deterministic
    "trusted_sample_min": 3,
    "github_repo": "test-owner/test-repo",
}


def _patch_cfg(monkeypatch, cfg_override=None):
    """Patch _get_scheduler_config to return a controllable dict."""
    cfg = dict(_MINIMAL_SCHEDULER_CFG)
    if cfg_override:
        cfg.update(cfg_override)
    from pipeline import scheduler as sch
    monkeypatch.setattr(sch, "_get_scheduler_config", lambda: cfg)
    return cfg


# ---------------------------------------------------------------------------
# 1. get_next_platform — first call
# ---------------------------------------------------------------------------

def test_get_next_platform_first_call():
    """With no prior rotation data, should return 'youtube'."""
    from pipeline.scheduler import get_next_platform

    conn = make_db()
    result = get_next_platform("mythology", conn)
    assert result == "youtube"


# ---------------------------------------------------------------------------
# 2. get_next_platform — rotation cycle
# ---------------------------------------------------------------------------

def test_get_next_platform_rotates():
    """Should rotate youtube -> instagram -> facebook -> youtube."""
    from pipeline.scheduler import get_next_platform

    conn = make_db()
    niche = "mythology"
    results = [get_next_platform(niche, conn) for _ in range(4)]
    assert results == ["youtube", "instagram", "facebook", "youtube"]


# ---------------------------------------------------------------------------
# 3. pick_optimal_time — uses defaults when no perf data
# ---------------------------------------------------------------------------

def test_pick_optimal_time_uses_defaults(monkeypatch):
    """With no time_performance rows and exploration disabled, should pick from default IST hours."""
    from pipeline import scheduler as sch
    from pipeline.scheduler import _ist_to_utc_hour

    _patch_cfg(monkeypatch, {"exploration_rate": 0.0})
    conn = make_db()

    result = sch.pick_optimal_time("mythology", "youtube", conn)

    # result should be a timezone-aware datetime in the future
    assert isinstance(result, datetime)
    assert result.tzinfo is not None, "result must be timezone-aware"
    now = datetime.now(timezone.utc)
    assert result > now

    # Hour should be one of the default youtube IST slots converted to UTC
    default_ist = _MINIMAL_SCHEDULER_CFG["default_times_ist"]["youtube"]
    expected_utc_hours = [_ist_to_utc_hour(t) for t in default_ist]
    assert result.hour in expected_utc_hours


# ---------------------------------------------------------------------------
# 4. pick_optimal_time — avoids conflicts (min_gap)
# ---------------------------------------------------------------------------

def test_pick_optimal_time_avoids_conflicts(monkeypatch):
    """Should skip a slot already occupied within min_gap_minutes."""
    from pipeline import scheduler as sch
    from pipeline.scheduler import _ist_to_utc_hour

    _patch_cfg(monkeypatch, {"exploration_rate": 0.0, "min_gap_minutes": 30})
    conn = make_db()

    # Pre-occupy the first default youtube UTC hours for tomorrow
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    default_ist = _MINIMAL_SCHEDULER_CFG["default_times_ist"]["youtube"]
    utc_hours = [_ist_to_utc_hour(t) for t in default_ist]

    # Block the first default slot
    blocked_dt = tomorrow.replace(hour=utc_hours[0], minute=0, second=0, microsecond=0)
    conn.execute(
        "INSERT INTO upload_schedule (platform, niche_id, scheduled_at, status) "
        "VALUES ('youtube', 'mythology', ?, 'pending')",
        (blocked_dt.strftime("%Y-%m-%d %H:%M:%S"),),
    )
    conn.commit()

    result = sch.pick_optimal_time("mythology", "youtube", conn)

    # Result must be timezone-aware and not be the blocked hour (or within 30 min of it)
    assert result.tzinfo is not None, "result must be timezone-aware"
    diff = abs((result - blocked_dt).total_seconds() / 60)
    assert diff >= 30, f"Got slot {result} too close to blocked {blocked_dt}"



# ---------------------------------------------------------------------------
# media_type routing (image posts)
# ---------------------------------------------------------------------------

def _ig_setup(monkeypatch, tmp_path, media_name):
    """One instagram account whose credentials file really exists under tmp_path."""
    from scripts import upload_all_platforms as uap

    (tmp_path / "creds.json").write_text("{}")
    media = tmp_path / media_name
    media.write_bytes(b"\x00")
    monkeypatch.setattr(uap, "ROOT", tmp_path)
    monkeypatch.setattr(uap, "load_social_config", lambda: {
        "platforms": {"instagram": {"enabled": True}},
        "accounts": [{"account_id": "ig", "platform": "instagram", "enabled": True,
                      "credentials_file": "creds.json"}],
    })
    calls = []

    def fake_image(**kw):
        calls.append(("image", kw))
        return "m1"

    def fake_reel(**kw):
        calls.append(("reel", kw))
        return "m2"

    import pipeline.instagram_upload as ig
    monkeypatch.setattr(ig, "upload_image_post", fake_image)
    monkeypatch.setattr(ig, "upload_reel", fake_reel)
    return uap, media, calls


def test_upload_all_routes_an_image_to_the_feed(monkeypatch, tmp_path):
    uap, img, calls = _ig_setup(monkeypatch, tmp_path, "scene.jpg")
    out = uap.upload_all(video_path=img, title="t", platforms_filter=["instagram"],
                         media_type="image")
    assert [c[0] for c in calls] == ["image"]
    assert calls[0][1]["image_path"] == img
    assert out[0]["status"] == "success"


def test_upload_all_still_defaults_to_a_reel(monkeypatch, tmp_path):
    uap, vid, calls = _ig_setup(monkeypatch, tmp_path, "v.mp4")
    uap.upload_all(video_path=vid, title="t", platforms_filter=["instagram"])
    assert [c[0] for c in calls] == ["reel"]


@pytest.mark.parametrize("name", ["_upload_youtube", "_upload_facebook"])
def test_video_only_platforms_reject_an_image(name):
    from scripts import upload_all_platforms as uap

    res = getattr(uap, name)(Path("x.jpg"), "t", "d", [], {"account_id": "a"}, False, "image")
    assert res["status"] == "error" and "video only" in res["error"]


def _captured_manifest(monkeypatch, **kw):
    import json
    import pipeline.drive_storage as ds
    from pipeline.scheduler import schedule_video

    _patch_cfg(monkeypatch)
    seen = {}

    def fake_upload(path, folder_name=None):
        seen.update(json.loads(Path(path).read_text()))
        return "id"

    monkeypatch.setattr(ds, "upload_to_drive", fake_upload)
    conn = make_db()
    vid = conn.execute("INSERT INTO videos (niche_id) VALUES ('skillstotraineyes')").lastrowid
    schedule_video(vid, "skillstotraineyes", "f", "m", conn,
                   force_platform="instagram",
                   force_time=datetime.now(timezone.utc) + timedelta(days=2),
                   title="t", caption="c", hashtags=["#x"], **kw)
    return seen


def test_manifest_carries_media_type(monkeypatch):
    assert _captured_manifest(monkeypatch, media_type="image")["media_type"] == "image"
    assert _captured_manifest(monkeypatch)["media_type"] == "video"


@pytest.mark.parametrize("media_type,suffix", [("image", ".jpg"), ("video", ".mp4")])
def test_process_schedule_picks_suffix_from_media_type(monkeypatch, media_type, suffix):
    import pipeline.drive_storage as ds
    import scripts.run_scheduled_upload as rsu
    import scripts.upload_all_platforms as uap

    got = {}

    def fake_download(file_id, dest):
        got["dest"] = Path(dest)
        Path(dest).write_bytes(b"x")

    def fake_upload_all(**kw):
        got["media_type"] = kw["media_type"]
        return []

    monkeypatch.setattr(ds, "download_from_drive", fake_download)
    monkeypatch.setattr(ds, "move_drive_file", lambda *a, **k: None)
    monkeypatch.setattr(uap, "upload_all", fake_upload_all)
    for n in ("_notify_telegram", "_notify_token_alert"):
        monkeypatch.setattr(rsu, n, lambda *a, **k: None, raising=False)
    try:
        rsu.process_schedule({"schedule_id": 1, "platform": "instagram",
                              "drive_file_id": "f", "media_type": media_type}, "mid", None)
    except Exception:
        pass  # post-upload bookkeeping is out of scope; routing was captured above
    assert got["dest"].suffix == suffix
    assert got["media_type"] == media_type
