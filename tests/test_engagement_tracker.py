"""Tests for pipeline/engagement_tracker.py — mock all external calls."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

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


# ---------------------------------------------------------------------------
# fetch_engagement: per-account credentials, daily refresh, never zero a post
# ---------------------------------------------------------------------------

def _insert(conn, platform="youtube", niche="mythology", status="done", post_id="p1",
            hours_ago=24, views=None, likes=None):
    scheduled_at = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """INSERT INTO upload_schedule
           (platform, niche_id, scheduled_at, status, platform_post_id, engagement_views, engagement_likes)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (platform, niche, scheduled_at, status, post_id, views, likes),
    )
    conn.commit()


@pytest.fixture
def tracker(monkeypatch, tmp_path):
    """engagement_tracker with credentials resolved to a temp file and fetchers stubbed per test."""
    import pipeline.engagement_tracker as t
    creds = tmp_path / "creds.json"
    creds.write_text("{}")
    monkeypatch.setattr(t, "_account_creds", lambda platform, niche_id: creds)
    return t


def _stub(monkeypatch, t, platform, result):
    calls = []

    def fetch(post_id, creds_path):
        calls.append(post_id)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setitem(t._PLATFORM_FETCHERS, platform, fetch)
    return calls


def test_fetch_engagement_updates_rows(monkeypatch, tracker):
    conn = make_db()
    _insert(conn, post_id="test-video-123")
    _stub(monkeypatch, tracker, "youtube", {"views": 1500, "likes": 45, "avg_view_percentage": 61.2})

    assert tracker.fetch_engagement(conn, lookback_days=14) == 1

    views, likes, blob, updated = conn.execute(
        "SELECT engagement_views, engagement_likes, engagement_json, engagement_updated_at "
        "FROM upload_schedule WHERE platform_post_id='test-video-123'").fetchone()
    assert (views, likes) == (1500, 45)
    assert json.loads(blob)["avg_view_percentage"] == 61.2
    assert updated is not None


def test_fetch_engagement_refreshes_already_fetched(monkeypatch, tracker):
    # A Reel keeps collecting views for days; the first number must not be frozen.
    conn = make_db()
    _insert(conn, post_id="seen-before", views=2000, likes=100)
    _stub(monkeypatch, tracker, "youtube", {"views": 2600, "likes": 130})

    assert tracker.fetch_engagement(conn, lookback_days=14) == 1
    assert conn.execute("SELECT engagement_views, engagement_likes FROM upload_schedule").fetchone() == (2600, 130)


def test_fetch_engagement_skips_pending_and_old_uploads(monkeypatch, tracker):
    conn = make_db()
    _insert(conn, status="pending", post_id="pending-1")
    _insert(conn, post_id="too-old", hours_ago=24 * 20)
    calls = _stub(monkeypatch, tracker, "youtube", {"views": 1, "likes": 1})

    assert tracker.fetch_engagement(conn, lookback_days=14) == 0
    assert calls == []


@pytest.mark.parametrize("result", [{}, RuntimeError("API down")])
def test_fetch_engagement_handles_api_failure(monkeypatch, tracker, result):
    conn = make_db()
    _insert(conn, post_id="failed-fetch-123", views=500)
    _stub(monkeypatch, tracker, "youtube", result)

    assert tracker.fetch_engagement(conn, lookback_days=14) == 0
    assert conn.execute("SELECT engagement_views FROM upload_schedule").fetchone() == (500,)


def test_partial_stats_never_zero_known_views(monkeypatch, tracker):
    # Without instagram_manage_insights only public likes/comments come back.
    conn = make_db()
    _insert(conn, platform="instagram", views=1200, likes=3)
    _stub(monkeypatch, tracker, "instagram", {"likes": 9, "comments": 1})

    assert tracker.fetch_engagement(conn, lookback_days=14) == 1
    assert conn.execute("SELECT engagement_views, engagement_likes FROM upload_schedule").fetchone() == (1200, 9)


def test_rows_read_with_their_own_accounts_credentials(monkeypatch):
    # The eye niche posts to its exclusive account; reading it with the shared
    # story token is what kept its views invisible.
    import pipeline.engagement_tracker as t
    import pipeline.social_accounts as sa
    cfg = {
        "platforms": {"instagram": {"enabled": True}},
        "accounts": [
            {"account_id": "all_ig", "platform": "instagram", "niche": "all", "enabled": True,
             "credentials_file": "credentials/all.json"},
            {"account_id": "eye_ig", "platform": "instagram", "niche": "skillstotraineyes",
             "enabled": True, "exclusive": True, "credentials_file": "credentials/eye.json"},
        ],
    }
    monkeypatch.setattr(sa, "load_social_config", lambda: cfg)
    assert t._account_creds("instagram", "skillstotraineyes").name == "eye.json"
    assert t._account_creds("instagram", "space_science").name == "all.json"
    assert t._account_creds("youtube", "skillstotraineyes") is None


# ---------------------------------------------------------------------------
# Platform fetchers
# ---------------------------------------------------------------------------

def _resp(payload, ok=True):
    r = MagicMock()
    r.ok = ok
    r.status_code = 200 if ok else 400
    r.text = json.dumps(payload)
    r.json.return_value = payload
    return r


def test_fetch_instagram_stats_reads_reel_insights(monkeypatch, tmp_path):
    import pipeline.engagement_tracker as t
    monkeypatch.setattr("pipeline.instagram_upload._load_credentials", lambda p: {"access_token": "T"})
    responses = iter([
        _resp({"error": {"message": "metric in development"}}, ok=False),   # full set rejected
        _resp({"data": [{"name": "views", "values": [{"value": 2154}]},
                        {"name": "reach", "values": [{"value": 1800}]},
                        {"name": "likes", "values": [{"value": 30}]}]}),
    ])
    seen = []
    monkeypatch.setattr(t.requests, "get", lambda url, params, timeout: seen.append(params["metric"]) or next(responses))

    assert t._fetch_instagram_stats("m1", tmp_path / "c.json") == {"views": 2154, "reach": 1800, "likes": 30}
    assert seen == list(t._IG_METRICS)


def test_fetch_instagram_stats_falls_back_to_public_counts(monkeypatch, tmp_path):
    import pipeline.engagement_tracker as t
    monkeypatch.setattr("pipeline.instagram_upload._load_credentials", lambda p: {"access_token": "T"})
    responses = iter([
        _resp({"error": {"code": 10}}, ok=False),
        _resp({"error": {"code": 10}}, ok=False),
        _resp({"like_count": 30, "comments_count": 1}),
    ])
    monkeypatch.setattr(t.requests, "get", lambda url, params, timeout: next(responses))

    assert t._fetch_instagram_stats("m1", tmp_path / "c.json") == {"likes": 30, "comments": 1}


def test_fetch_facebook_stats_prefers_reel_plays(monkeypatch, tmp_path):
    import pipeline.engagement_tracker as t
    monkeypatch.setattr(t, "_fb_page_token", lambda creds_path: "PT")
    responses = iter([
        _resp({"views": 3, "likes": {"summary": {"total_count": 1}},
               "comments": {"summary": {"total_count": 0}}}),
        _resp({"data": [{"name": "blue_reels_play_count", "values": [{"value": 1200}]},
                        {"name": "post_video_avg_time_watched", "values": [{"value": 9100}]}]}),
    ])
    monkeypatch.setattr(t.requests, "get", lambda url, params, timeout: next(responses))

    assert t._fetch_facebook_stats("v1", tmp_path / "c.json") == {
        "views": 1200, "likes": 1, "comments": 0,
        "blue_reels_play_count": 1200, "post_video_avg_time_watched": 9100,
    }


def test_fetch_facebook_stats_without_read_insights(monkeypatch, tmp_path):
    import pipeline.engagement_tracker as t
    monkeypatch.setattr(t, "_fb_page_token", lambda creds_path: "PT")
    responses = iter([
        _resp({"views": 7, "likes": {"summary": {"total_count": 2}},
               "comments": {"summary": {"total_count": 0}}}),
        _resp({"error": {"code": 10}}, ok=False),
        _resp({"error": {"code": 10}}, ok=False),
    ])
    monkeypatch.setattr(t.requests, "get", lambda url, params, timeout: next(responses))

    assert t._fetch_facebook_stats("v1", tmp_path / "c.json") == {"views": 7, "likes": 2, "comments": 0}


def test_fetch_youtube_stats(monkeypatch, tmp_path):
    import pipeline.engagement_tracker as t
    data_api, analytics = MagicMock(), MagicMock()
    data_api.videos().list().execute.return_value = {"items": [{
        "statistics": {"viewCount": "540", "likeCount": "18", "commentCount": "0"},
        "snippet": {"publishedAt": "2026-09-20T09:56:05Z"},
    }]}
    analytics.reports().query().execute.return_value = {"rows": [[41.5, 48.9]]}
    monkeypatch.setattr(t, "_youtube_services", lambda creds_path: (data_api, analytics))

    assert t._fetch_youtube_stats("SCqGwqs5QPw", tmp_path / "c.json") == {
        "views": 540, "likes": 18, "comments": 0,
        "avg_view_duration_s": 41.5, "avg_view_percentage": 48.9,
    }


# ---------------------------------------------------------------------------
# test_recalculate_time_performance
# ---------------------------------------------------------------------------

def test_recalculate_time_performance():
    """
    Insert 5 done uploads with engagement data at hour 14 UTC on the same day of week.
    Call recalculate_time_performance.
    Verify time_performance row exists with correct avg and sample_count=5.
    """
    from pipeline.engagement_tracker import recalculate_time_performance

    conn = make_db()

    # Create a base datetime at hour 14 UTC on a Sunday (day_of_week=0)
    # 2026-08-02 is a Sunday
    base = datetime(2026, 8, 2, 14, 30, 0, tzinfo=timezone.utc)

    # Insert 5 uploads at hour 14 UTC on same day-of-week with engagement data
    # Each one is 7 days apart so they all have day_of_week=0
    for i in range(5):
        scheduled_at = (base + timedelta(weeks=i)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """INSERT INTO upload_schedule
               (platform, niche_id, scheduled_at, status, engagement_views, engagement_likes)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("youtube", "mythology", scheduled_at, "done", 1000 + i * 100, 50 + i * 5),
        )
    conn.commit()

    # Call recalculate_time_performance with lookback_days=30
    count = recalculate_time_performance(conn, lookback_days=30)

    # Should have updated 1 row (one slot: niche=mythology, platform=youtube, hour=14, day_of_week=0)
    assert count == 1

    # Verify time_performance row
    row = conn.execute(
        """SELECT avg_views, avg_likes, sample_count FROM time_performance
           WHERE niche_id='mythology' AND platform='youtube' AND hour_utc=14"""
    ).fetchone()
    assert row is not None

    # Average views: (1000 + 1100 + 1200 + 1300 + 1400) / 5 = 1200
    # Average likes: (50 + 55 + 60 + 65 + 70) / 5 = 60
    expected_avg_views = 1200.0
    expected_avg_likes = 60.0

    assert abs(row[0] - expected_avg_views) < 0.1, f"Expected {expected_avg_views}, got {row[0]}"
    assert abs(row[1] - expected_avg_likes) < 0.1, f"Expected {expected_avg_likes}, got {row[1]}"
    assert row[2] == 5


# ---------------------------------------------------------------------------
# test_recalculate_respects_lookback_days
# ---------------------------------------------------------------------------

def test_recalculate_respects_lookback_days():
    """
    Insert two uploads: one within lookback period, one outside.
    Call recalculate_time_performance with lookback_days=7.
    Verify only the recent one is included.
    """
    from pipeline.engagement_tracker import recalculate_time_performance

    conn = make_db()

    now = datetime.now(timezone.utc)

    # Insert one upload 5 days ago (within 7-day lookback)
    recent = (now - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """INSERT INTO upload_schedule
           (platform, niche_id, scheduled_at, status, engagement_views, engagement_likes)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("youtube", "mythology", recent, "done", 1000, 50),
    )

    # Insert one upload 10 days ago (outside 7-day lookback)
    old = (now - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """INSERT INTO upload_schedule
           (platform, niche_id, scheduled_at, status, engagement_views, engagement_likes)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("youtube", "mythology", old, "done", 500, 25),
    )
    conn.commit()

    count = recalculate_time_performance(conn, lookback_days=7)

    # Only 1 row should be aggregated (the recent one)
    assert count == 1

    # Verify time_performance has average from recent upload only
    row = conn.execute(
        """SELECT avg_views FROM time_performance
           WHERE niche_id='mythology' AND platform='youtube'"""
    ).fetchone()
    assert row is not None
    assert row[0] == 1000.0  # Only recent upload counted


# ---------------------------------------------------------------------------
# test_multiple_platforms_independent
# ---------------------------------------------------------------------------

def test_multiple_platforms_independent():
    """
    Insert uploads for youtube, instagram, facebook in same hour slot.
    Verify recalculate creates separate time_performance rows per platform.
    """
    from pipeline.engagement_tracker import recalculate_time_performance

    conn = make_db()

    base = datetime(2026, 8, 10, 14, 0, 0, tzinfo=timezone.utc)

    platforms = ["youtube", "instagram", "facebook"]
    for platform in platforms:
        scheduled_at = base.strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """INSERT INTO upload_schedule
               (platform, niche_id, scheduled_at, status, engagement_views, engagement_likes)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (platform, "mythology", scheduled_at, "done", 1000, 50),
        )
    conn.commit()

    count = recalculate_time_performance(conn, lookback_days=30)

    # Should have 3 separate rows in time_performance
    assert count == 3

    # Verify each platform has its own row
    rows = conn.execute(
        """SELECT platform FROM time_performance
           WHERE niche_id='mythology' AND hour_utc=14"""
    ).fetchall()
    assert len(rows) == 3
    platforms_found = sorted([row[0] for row in rows])
    assert platforms_found == ["facebook", "instagram", "youtube"]
