"""Fetch engagement stats from social platforms and recalculate optimal time slots."""
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone

import requests

log = logging.getLogger(__name__)


def _fetch_youtube_stats(video_id: str) -> dict:
    """
    Fetch YouTube video stats: views and likes.

    Args:
        video_id: YouTube video ID

    Returns:
        {"views": int, "likes": int} or {} on failure
    """
    try:
        api_key = os.getenv("GOOGLE_AI_STUDIO_API_KEY")
        if not api_key:
            log.warning("GOOGLE_AI_STUDIO_API_KEY not set, skipping YouTube fetch")
            return {}

        url = f"https://www.googleapis.com/youtube/v3/videos"
        params = {
            "part": "statistics",
            "id": video_id,
            "key": api_key,
        }

        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()

        data = response.json()
        if not data.get("items"):
            log.warning("YouTube: no items found for video_id=%s", video_id)
            return {}

        stats = data["items"][0].get("statistics", {})
        views = int(stats.get("viewCount", 0))
        likes = int(stats.get("likeCount", 0))

        return {"views": views, "likes": likes}

    except Exception as e:
        log.warning("YouTube fetch failed for video_id=%s: %s", video_id, e)
        return {}


def _fetch_instagram_stats(media_id: str) -> dict:
    """
    Fetch Instagram media stats: views and likes via insights API.

    Args:
        media_id: Instagram media ID

    Returns:
        {"views": int, "likes": int} or {} on failure
    """
    try:
        access_token = os.getenv("INSTAGRAM_ACCESS_TOKEN")
        if not access_token:
            log.warning("INSTAGRAM_ACCESS_TOKEN not set, skipping Instagram fetch")
            return {}

        url = f"https://graph.instagram.com/{media_id}/insights"
        params = {
            "metric": "plays,likes",
            "access_token": access_token,
        }

        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()

        data = response.json()
        metrics = {}
        for item in data.get("data", []):
            metric_name = item.get("name")
            metric_value = item.get("values", [{}])[0].get("value", 0)
            metrics[metric_name] = metric_value

        views = int(metrics.get("plays", 0))
        likes = int(metrics.get("likes", 0))

        return {"views": views, "likes": likes}

    except Exception as e:
        log.warning("Instagram fetch failed for media_id=%s: %s", media_id, e)
        return {}


def _fetch_facebook_stats(post_id: str) -> dict:
    """
    Fetch Facebook post stats: views and likes via graph API.

    Args:
        post_id: Facebook post ID

    Returns:
        {"views": int, "likes": int} or {} on failure
    """
    try:
        access_token = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
        if not access_token:
            log.warning("FACEBOOK_PAGE_ACCESS_TOKEN not set, skipping Facebook fetch")
            return {}

        url = f"https://graph.facebook.com/v21.0/{post_id}"
        params = {
            "fields": "insights.metric(post_video_views_organic).period(lifetime),reactions.summary(true)",
            "access_token": access_token,
        }

        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()

        data = response.json()

        # Extract views from insights
        views = 0
        insights = data.get("insights", {}).get("data", [])
        for insight in insights:
            if insight.get("name") == "post_video_views_organic":
                views = int(insight.get("values", [{}])[0].get("value", 0))

        # Extract likes from reactions
        likes = int(data.get("reactions", {}).get("summary", {}).get("total_count", 0))

        return {"views": views, "likes": likes}

    except Exception as e:
        log.warning("Facebook fetch failed for post_id=%s: %s", post_id, e)
        return {}


_PLATFORM_FETCHERS = {
    "youtube": _fetch_youtube_stats,
    "instagram": _fetch_instagram_stats,
    "facebook": _fetch_facebook_stats,
}


def fetch_engagement(conn: sqlite3.Connection, lookback_hours: int = 48) -> int:
    """
    Fetch engagement stats for recent uploads missing engagement data.

    Queries upload_schedule for rows with:
        - status='done'
        - platform_post_id NOT NULL
        - engagement_views IS NULL
        - scheduled_at >= cutoff_datetime

    For each row, calls the appropriate platform fetcher and updates
    engagement_views and engagement_likes columns.

    Args:
        conn: SQLite connection
        lookback_hours: hours to look back from now (default 48)

    Returns:
        Count of rows updated
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")

    rows = conn.execute(
        """SELECT id, platform, platform_post_id FROM upload_schedule
           WHERE status='done'
           AND platform_post_id IS NOT NULL
           AND engagement_views IS NULL
           AND scheduled_at >= ?""",
        (cutoff_str,),
    ).fetchall()

    count = 0
    for row_id, platform, post_id in rows:
        fetcher = _PLATFORM_FETCHERS.get(platform)
        if not fetcher:
            log.warning("Unknown platform=%s for row_id=%s", platform, row_id)
            continue

        stats = fetcher(post_id)
        if not stats:
            log.debug("No stats fetched for platform=%s, post_id=%s", platform, post_id)
            continue

        conn.execute(
            """UPDATE upload_schedule
               SET engagement_views=?, engagement_likes=?
               WHERE id=?""",
            (stats["views"], stats["likes"], row_id),
        )
        count += 1
        log.info(
            "Updated engagement for row_id=%s: views=%s, likes=%s",
            row_id,
            stats["views"],
            stats["likes"],
        )

    if count > 0:
        conn.commit()

    return count


def recalculate_time_performance(conn: sqlite3.Connection, lookback_days: int = 30) -> int:
    """
    Recalculate rolling averages for time_performance table.

    Aggregates upload_schedule rows with:
        - status='done'
        - engagement_views IS NOT NULL
        - scheduled_at >= cutoff_datetime

    Groups by (niche_id, platform, hour_utc, day_of_week) and calculates:
        - AVG(engagement_views)
        - AVG(engagement_likes)
        - COUNT(*) as sample_count

    Inserts or replaces rows in time_performance table.

    Args:
        conn: SQLite connection
        lookback_days: days to look back from now (default 30)

    Returns:
        Count of time_performance rows updated
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")

    # Fetch aggregated data
    rows = conn.execute(
        """SELECT niche_id, platform,
                  CAST(strftime('%H', scheduled_at) AS INTEGER) AS hour_utc,
                  CAST(strftime('%w', scheduled_at) AS INTEGER) AS day_of_week,
                  AVG(engagement_views) AS avg_views,
                  AVG(engagement_likes) AS avg_likes,
                  COUNT(*) AS sample_count
           FROM upload_schedule
           WHERE status='done'
           AND engagement_views IS NOT NULL
           AND scheduled_at >= ?
           GROUP BY niche_id, platform, hour_utc, day_of_week""",
        (cutoff_str,),
    ).fetchall()

    count = 0
    for niche_id, platform, hour_utc, day_of_week, avg_views, avg_likes, sample_count in rows:
        conn.execute(
            """INSERT OR REPLACE INTO time_performance
               (niche_id, platform, hour_utc, day_of_week, avg_views, avg_likes, sample_count, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (niche_id, platform, hour_utc, day_of_week, avg_views, avg_likes, sample_count),
        )
        count += 1

    if count > 0:
        conn.commit()
        log.info("Recalculated time_performance: %d slots updated", count)

    return count
