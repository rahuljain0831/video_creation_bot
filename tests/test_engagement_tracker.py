"""Tests for pipeline/engagement_tracker.py — mock all external calls."""
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
# test_fetch_engagement_updates_rows
# ---------------------------------------------------------------------------

def test_fetch_engagement_updates_rows(monkeypatch):
    """
    Insert a done upload with platform_post_id, no engagement yet.
    Mock the YouTube API request to return {"views": 1500, "likes": 45}.
    Call fetch_engagement, assert count=1.
    Verify DB row updated.
    """
    from pipeline.engagement_tracker import fetch_engagement

    # Set env var so the fetcher doesn't early-exit
    monkeypatch.setenv("GOOGLE_AI_STUDIO_API_KEY", "test-key")

    conn = make_db()

    # Insert a done upload with platform_post_id but no engagement
    scheduled_at = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """INSERT INTO upload_schedule
           (platform, niche_id, scheduled_at, status, platform_post_id)
           VALUES (?, ?, ?, ?, ?)""",
        ("youtube", "mythology", scheduled_at, "done", "test-video-123"),
    )
    conn.commit()

    # Mock requests.get to return YouTube stats
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "items": [
            {
                "statistics": {
                    "viewCount": "1500",
                    "likeCount": "45",
                }
            }
        ]
    }

    with patch("pipeline.engagement_tracker.requests.get", return_value=mock_response):
        count = fetch_engagement(conn, lookback_hours=48)

    assert count == 1

    # Verify DB row was updated
    row = conn.execute(
        "SELECT engagement_views, engagement_likes FROM upload_schedule WHERE platform_post_id='test-video-123'"
    ).fetchone()
    assert row is not None
    assert row[0] == 1500
    assert row[1] == 45


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
# test_fetch_engagement_skips_already_fetched
# ---------------------------------------------------------------------------

def test_fetch_engagement_skips_already_fetched():
    """
    Insert upload with engagement_views already set.
    Call fetch_engagement.
    Assert count=0, mock not called.
    """
    from pipeline.engagement_tracker import fetch_engagement

    conn = make_db()

    # Insert upload with engagement already fetched
    scheduled_at = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """INSERT INTO upload_schedule
           (platform, niche_id, scheduled_at, status, platform_post_id, engagement_views, engagement_likes)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("youtube", "mythology", scheduled_at, "done", "already-fetched-123", 2000, 100),
    )
    conn.commit()

    # Mock should not be called
    with patch("pipeline.engagement_tracker._fetch_youtube_stats") as mock_fetch:
        count = fetch_engagement(conn, lookback_hours=48)

    assert count == 0
    mock_fetch.assert_not_called()


# ---------------------------------------------------------------------------
# test_fetch_engagement_skips_pending_uploads
# ---------------------------------------------------------------------------

def test_fetch_engagement_skips_pending_uploads():
    """
    Insert upload with status='pending' (not 'done').
    Call fetch_engagement.
    Assert count=0.
    """
    from pipeline.engagement_tracker import fetch_engagement

    conn = make_db()

    # Insert upload with status='pending'
    scheduled_at = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """INSERT INTO upload_schedule
           (platform, niche_id, scheduled_at, status, platform_post_id)
           VALUES (?, ?, ?, ?, ?)""",
        ("youtube", "mythology", scheduled_at, "pending", "pending-video-123"),
    )
    conn.commit()

    with patch("pipeline.engagement_tracker._fetch_youtube_stats") as mock_fetch:
        count = fetch_engagement(conn, lookback_hours=48)

    assert count == 0
    mock_fetch.assert_not_called()


# ---------------------------------------------------------------------------
# test_fetch_engagement_handles_api_failure
# ---------------------------------------------------------------------------

def test_fetch_engagement_handles_api_failure():
    """
    Insert upload and mock fetcher to return empty dict (API failure).
    Call fetch_engagement.
    Assert count=0, no DB update.
    """
    from pipeline.engagement_tracker import fetch_engagement

    conn = make_db()

    scheduled_at = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """INSERT INTO upload_schedule
           (platform, niche_id, scheduled_at, status, platform_post_id)
           VALUES (?, ?, ?, ?, ?)""",
        ("youtube", "mythology", scheduled_at, "done", "failed-fetch-123"),
    )
    conn.commit()

    with patch("pipeline.engagement_tracker._fetch_youtube_stats") as mock_fetch:
        mock_fetch.return_value = {}  # Simulate API failure

        count = fetch_engagement(conn, lookback_hours=48)

    assert count == 0

    # Verify row was not updated
    row = conn.execute(
        "SELECT engagement_views FROM upload_schedule WHERE platform_post_id='failed-fetch-123'"
    ).fetchone()
    assert row[0] is None


# ---------------------------------------------------------------------------
# test_fetch_instagram_stats
# ---------------------------------------------------------------------------

def test_fetch_instagram_stats(monkeypatch):
    """Test _fetch_instagram_stats with mocked requests."""
    from pipeline.engagement_tracker import _fetch_instagram_stats

    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "test-token")

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "data": [
            {"name": "plays", "values": [{"value": 2500}]},
            {"name": "likes", "values": [{"value": 75}]},
        ]
    }

    with patch("pipeline.engagement_tracker.requests.get", return_value=mock_response):
        result = _fetch_instagram_stats("media-123")

    assert result == {"views": 2500, "likes": 75}


# ---------------------------------------------------------------------------
# test_fetch_facebook_stats
# ---------------------------------------------------------------------------

def test_fetch_facebook_stats(monkeypatch):
    """Test _fetch_facebook_stats with mocked requests."""
    from pipeline.engagement_tracker import _fetch_facebook_stats

    monkeypatch.setenv("FACEBOOK_PAGE_ACCESS_TOKEN", "test-token")

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "insights": {
            "data": [
                {
                    "name": "post_video_views_organic",
                    "values": [{"value": 3000}],
                }
            ]
        },
        "reactions": {"summary": {"total_count": 120}},
    }

    with patch("pipeline.engagement_tracker.requests.get", return_value=mock_response):
        result = _fetch_facebook_stats("post-123")

    assert result == {"views": 3000, "likes": 120}


# ---------------------------------------------------------------------------
# test_fetch_youtube_stats
# ---------------------------------------------------------------------------

def test_fetch_youtube_stats(monkeypatch):
    """Test _fetch_youtube_stats with mocked requests."""
    from pipeline.engagement_tracker import _fetch_youtube_stats

    monkeypatch.setenv("GOOGLE_AI_STUDIO_API_KEY", "test-key")

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "items": [
            {
                "statistics": {
                    "viewCount": "5000",
                    "likeCount": "200",
                }
            }
        ]
    }

    with patch("pipeline.engagement_tracker.requests.get", return_value=mock_response):
        result = _fetch_youtube_stats("video-123")

    assert result == {"views": 5000, "likes": 200}


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
