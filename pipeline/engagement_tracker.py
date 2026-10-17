"""Fetch engagement stats from social platforms and recalculate optimal time slots.

Every row is read with the credentials of the account it was posted from (the
uploader's social_config.json routing), so the eye-training account's Reels are
read with its own token, not a shared env token. A Reel keeps collecting views
for days, so each run re-reads every post in the lookback window instead of
freezing its first number.

Needs read scopes beyond what uploading needs: instagram_manage_insights and
read_insights on the Meta token, youtube.readonly + yt-analytics.readonly on
the YouTube one (scripts/instagram_auth_setup.py, scripts/youtube_auth_setup.py).
Until those are granted, Instagram and Facebook fall back to the public
like/comment/view fields and YouTube rows are skipped.
"""
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import requests

log = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
GRAPH_API_BASE = "https://graph.facebook.com/v21.0"

# Reel insights. Meta marks the last two "in development" and an unsupported
# metric fails the whole request, so a failure retries with the core set.
_IG_METRICS = ("views,reach,likes,comments,shares,saved,ig_reels_avg_watch_time,reels_skip_rate",
               "views,reach,likes,comments,shares,saved")
# Reels first, then page videos: each metric set is only valid for its kind.
_FB_METRICS = ("blue_reels_play_count,post_video_avg_time_watched",
               "total_video_views,total_video_avg_time_watched")
_FB_VIEW_METRICS = ("blue_reels_play_count", "total_video_views")


def _graph_get(url: str, **params) -> dict:
    """GET a Graph API URL; {} on any error (logged), so one bad post never stops the run."""
    try:
        resp = requests.get(url, params=params, timeout=15)
    except requests.RequestException as exc:
        log.warning("Graph request failed for %s: %s", url, exc)
        return {}
    if not resp.ok:
        log.warning("Graph %s -> %s: %s", url.split("/v21.0/")[-1], resp.status_code, resp.text[:200])
        return {}
    return resp.json()


def _insights(url: str, metrics: str, token: str) -> dict:
    """{metric: value} from an insights edge; {} when unavailable (Meta returns no data, not 0)."""
    out = {}
    for item in _graph_get(url, metric=metrics, access_token=token).get("data", []):
        values = item.get("values") or [{}]
        value = values[0].get("value", item.get("total_value", {}).get("value"))
        if value is not None:
            out[item["name"]] = value
    return out


def _fetch_instagram_stats(media_id: str, creds_path: Path) -> dict:
    """Reel insights; without instagram_manage_insights, just the public like/comment counts."""
    from pipeline.instagram_upload import _load_credentials

    token = _load_credentials(creds_path)["access_token"]
    for metrics in _IG_METRICS:
        stats = _insights(f"{GRAPH_API_BASE}/{media_id}/insights", metrics, token)
        if stats:
            return stats
    media = _graph_get(f"{GRAPH_API_BASE}/{media_id}", fields="like_count,comments_count", access_token=token)
    if not media:
        return {}
    return {"likes": media.get("like_count", 0), "comments": media.get("comments_count", 0)}


@lru_cache(maxsize=None)
def _fb_page_token(creds_path: str) -> str:
    from pipeline.facebook_upload import _get_page_access_token, _load_credentials

    creds = _load_credentials(Path(creds_path))
    return _get_page_access_token(creds["access_token"], creds["page_id"])


def _fetch_facebook_stats(video_id: str, creds_path: Path) -> dict:
    """Public views/likes/comments, overridden by video insights when read_insights is granted."""
    token = _fb_page_token(str(creds_path))
    base = _graph_get(f"{GRAPH_API_BASE}/{video_id}",
                      fields="views,likes.summary(true),comments.summary(true)", access_token=token)
    stats = {}
    if base:
        stats = {
            "views": base.get("views", 0),
            "likes": base.get("likes", {}).get("summary", {}).get("total_count", 0),
            "comments": base.get("comments", {}).get("summary", {}).get("total_count", 0),
        }
    for metrics in _FB_METRICS:
        extra = _insights(f"{GRAPH_API_BASE}/{video_id}/video_insights", metrics, token)
        if extra:
            stats.update(extra)
            for name in _FB_VIEW_METRICS:
                if name in extra:
                    stats["views"] = extra[name]
            break
    return stats


@lru_cache(maxsize=None)
def _youtube_services(creds_path: str):
    from googleapiclient.discovery import build
    from pipeline.youtube_upload import _load_youtube_credentials

    creds = _load_youtube_credentials(Path(creds_path))
    return (build("youtube", "v3", credentials=creds, cache_discovery=False),
            build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False))


def _fetch_youtube_stats(video_id: str, creds_path: Path) -> dict:
    """Public counts (youtube.readonly) + average view percentage (yt-analytics.readonly)."""
    data_api, analytics = _youtube_services(str(creds_path))
    items = data_api.videos().list(part="statistics,snippet", id=video_id).execute().get("items", [])
    if not items:
        log.warning("YouTube: no items for video_id=%s", video_id)
        return {}
    s = items[0]["statistics"]
    stats = {"views": int(s.get("viewCount", 0)), "likes": int(s.get("likeCount", 0)),
             "comments": int(s.get("commentCount", 0))}
    try:
        report = analytics.reports().query(
            ids="channel==MINE",
            startDate=items[0]["snippet"]["publishedAt"][:10],
            endDate=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            metrics="averageViewDuration,averageViewPercentage",
            filters=f"video=={video_id}",
        ).execute()
        if report.get("rows"):
            stats["avg_view_duration_s"], stats["avg_view_percentage"] = report["rows"][0][:2]
    except Exception as exc:
        log.warning("YouTube Analytics failed for video_id=%s: %s", video_id, exc)
    return stats


_PLATFORM_FETCHERS = {
    "youtube": _fetch_youtube_stats,
    "instagram": _fetch_instagram_stats,
    "facebook": _fetch_facebook_stats,
}


def _account_creds(platform: str, niche_id: str) -> Path | None:
    """Credentials file of the account this niche posts to on `platform` (uploader routing)."""
    from pipeline.social_accounts import load_social_config
    from scripts.upload_all_platforms import _get_accounts_for_platform

    accounts = _get_accounts_for_platform(platform, load_social_config(), niche_id)
    return ROOT / accounts[0]["credentials_file"] if accounts else None


def fetch_engagement(conn: sqlite3.Connection, lookback_days: int = 14) -> int:
    """
    Re-read engagement for every done upload posted in the last `lookback_days`.

    Writes engagement_views / engagement_likes (time_performance reads those)
    plus every metric the platform returned to engagement_json. A post the
    platform has no numbers for yet is left as it was, never zeroed.

    Returns:
        Count of rows updated
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    rows = conn.execute(
        """SELECT id, platform, niche_id, platform_post_id FROM upload_schedule
           WHERE status='done'
           AND platform_post_id IS NOT NULL AND platform_post_id != ''
           AND scheduled_at >= ?""",
        (cutoff.strftime("%Y-%m-%d %H:%M:%S"),),
    ).fetchall()

    count = 0
    for row_id, platform, niche_id, post_id in rows:
        fetcher = _PLATFORM_FETCHERS.get(platform)
        if not fetcher:
            log.warning("Unknown platform=%s for row_id=%s", platform, row_id)
            continue
        creds_path = _account_creds(platform, niche_id)
        if not creds_path or not creds_path.exists():
            log.warning("No credentials for %s/%s (row_id=%s), skipped", platform, niche_id, row_id)
            continue

        try:
            stats = fetcher(post_id, creds_path)
        except Exception as exc:
            log.warning("Fetch failed for %s post_id=%s: %s", platform, post_id, exc)
            continue
        if not stats:
            continue

        conn.execute(
            """UPDATE upload_schedule
               SET engagement_views=COALESCE(?, engagement_views),
                   engagement_likes=COALESCE(?, engagement_likes),
                   engagement_json=?, engagement_updated_at=datetime('now')
               WHERE id=?""",
            (stats.get("views"), stats.get("likes"), json.dumps(stats), row_id),
        )
        count += 1
        log.info("Engagement row_id=%s %s/%s: %s", row_id, platform, niche_id, stats)

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
