"""3-day pipeline progress report — sent to Telegram.

Usage:
    python scripts/run_progress_report.py

In CI (GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON set): downloads schedule_db.sqlite from Drive.
Locally: reads cfg.paths["db"] directly.
Read-only — does not re-upload the DB.
"""
import asyncio
import json
import logging
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("progress_report")

_PLATFORMS = ["youtube", "instagram", "facebook"]
_PLATFORM_EMOJI = {"youtube": "[YT]", "instagram": "[IG]", "facebook": "[FB]"}
_NICHE_LABEL = {
    "mythology": "Mythology",
    "scary_stories": "Scary Stories",
    "heists": "Heists",
    "space_science": "Space & Science",
    "ai_tech_tools": "AI & Tech Tools",
    "finance_facts": "Finance Facts",
}
_IST = timedelta(hours=5, minutes=30)


# ---------------------------------------------------------------------------
# DB resolution
# ---------------------------------------------------------------------------

def _resolve_db() -> sqlite3.Connection:
    sa_json = os.getenv("GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON", "")
    if sa_json and Path(sa_json).exists():
        from pipeline.drive_storage import download_from_drive, _build_service, _get_subfolder

        service = _build_service()
        state_folder_id = _get_subfolder("state")
        results = service.files().list(
            q=f"'{state_folder_id}' in parents and name='schedule_db.sqlite' and trashed=false",
            spaces="drive",
            fields="files(id)",
        ).execute()
        files = results.get("files", [])
        if not files:
            raise RuntimeError("No schedule_db.sqlite found in Drive state/ folder")
        db_path = Path(tempfile.mkdtemp()) / "schedule.db"
        download_from_drive(files[0]["id"], db_path)
        log.info("Downloaded DB from Drive: %s", db_path)
    else:
        from config import cfg
        db_path = ROOT / cfg.paths["db"]
        if not db_path.exists():
            raise FileNotFoundError(f"Local DB not found: {db_path}")
        log.info("Using local DB: %s", db_path)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Hashtag bank
# ---------------------------------------------------------------------------

def _load_hashtags(active_niches: list[str]) -> dict:
    """Top 10 hashtags per (niche, platform) for active niches."""
    banks_path = ROOT / "hashtag_banks.json"
    if not banks_path.exists():
        return {}
    with open(banks_path) as f:
        banks = json.load(f)
    result = {}
    for niche in active_niches:
        if niche not in banks:
            continue
        niche_data = banks[niche]
        base = niche_data.get("base", [])
        for platform in _PLATFORMS:
            platform_tags = niche_data.get(platform, [])
            combined = list(dict.fromkeys(platform_tags + base))[:10]
            result[(niche, platform)] = combined
    return result


# ---------------------------------------------------------------------------
# Stats gathering
# ---------------------------------------------------------------------------

def _rows(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _gather_stats(conn: sqlite3.Connection) -> dict:
    uploads_by_platform = _rows(conn,
        "SELECT platform, COUNT(*) AS total_done FROM upload_schedule "
        "WHERE status='done' GROUP BY platform ORDER BY platform")

    niche_platform_rows = _rows(conn,
        "SELECT niche_id, platform, COUNT(*) AS done_count FROM upload_schedule "
        "WHERE status='done' GROUP BY niche_id, platform ORDER BY niche_id, platform")
    niche_platform_counts = {
        (r["niche_id"], r["platform"]): r["done_count"] for r in niche_platform_rows
    }

    engagement_by_platform = _rows(conn,
        "SELECT platform, "
        "  SUM(engagement_views) AS total_views, "
        "  SUM(engagement_likes) AS total_likes, "
        "  ROUND(AVG(engagement_views), 1) AS avg_views, "
        "  COUNT(*) AS posts_with_data "
        "FROM upload_schedule "
        "WHERE status='done' AND engagement_views IS NOT NULL "
        "GROUP BY platform ORDER BY platform")

    best_alltime_rows = _rows(conn,
        "SELECT us.platform, us.niche_id, us.platform_post_id, "
        "  us.engagement_views, us.engagement_likes, v.prompt "
        "FROM upload_schedule us LEFT JOIN videos v ON v.id=us.video_id "
        "WHERE us.status='done' AND us.engagement_views IS NOT NULL "
        "ORDER BY us.engagement_views DESC LIMIT 1")
    best_alltime = best_alltime_rows[0] if best_alltime_rows else None

    failed_by_platform = _rows(conn,
        "SELECT platform, COUNT(*) AS failed_count FROM upload_schedule "
        "WHERE status='failed' GROUP BY platform ORDER BY platform")

    total_videos = conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]

    uploads_30d = _rows(conn,
        "SELECT platform, COUNT(*) AS done_30d FROM upload_schedule "
        "WHERE status='done' AND created_at >= datetime('now','-30 days') "
        "GROUP BY platform ORDER BY platform")

    engagement_30d = _rows(conn,
        "SELECT platform, "
        "  COALESCE(SUM(engagement_views), 0) AS views_30d, "
        "  ROUND(AVG(engagement_views), 1) AS avg_views_30d "
        "FROM upload_schedule "
        "WHERE status='done' AND engagement_views IS NOT NULL "
        "  AND created_at >= datetime('now','-30 days') "
        "GROUP BY platform ORDER BY platform")

    best_30d_rows = _rows(conn,
        "SELECT us.platform, us.niche_id, us.platform_post_id, "
        "  us.engagement_views, us.engagement_likes, v.prompt "
        "FROM upload_schedule us LEFT JOIN videos v ON v.id=us.video_id "
        "WHERE us.status='done' AND us.engagement_views IS NOT NULL "
        "  AND us.created_at >= datetime('now','-30 days') "
        "ORDER BY us.engagement_views DESC LIMIT 1")
    best_30d = best_30d_rows[0] if best_30d_rows else None

    failed_30d_map = {
        r["platform"]: r["failed_30d"]
        for r in _rows(conn,
            "SELECT platform, COUNT(*) AS failed_30d FROM upload_schedule "
            "WHERE status='failed' AND created_at >= datetime('now','-30 days') "
            "GROUP BY platform")
    }
    for row in failed_by_platform:
        row["failed_30d"] = failed_30d_map.get(row["platform"], 0)

    active_niches = [
        r["niche_id"]
        for r in _rows(conn, "SELECT DISTINCT niche_id FROM upload_schedule WHERE status='done'")
    ]

    hashtags = _load_hashtags(active_niches)

    return {
        "uploads_by_platform": uploads_by_platform,
        "niche_platform_counts": niche_platform_counts,
        "engagement_by_platform": engagement_by_platform,
        "best_alltime": best_alltime,
        "failed_by_platform": failed_by_platform,
        "total_videos": total_videos,
        "uploads_30d": uploads_30d,
        "engagement_30d": engagement_30d,
        "best_30d": best_30d,
        "active_niches": active_niches,
        "hashtags": hashtags,
    }


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _fmt_best(b: dict, label: str) -> list[str]:
    lines = [f"<b>{label}</b>"]
    emoji = _PLATFORM_EMOJI.get(b["platform"], "")
    niche_label = _NICHE_LABEL.get(b["niche_id"], b["niche_id"])
    views = b["engagement_views"] or 0
    likes = b["engagement_likes"] or 0
    lines.append(f"  {emoji} {b['platform'].title()} | {niche_label} | {views:,} views | {likes:,} likes")
    if b["platform_post_id"]:
        if b["platform"] == "youtube":
            lines.append(f"  https://youtu.be/{b['platform_post_id']}")
        else:
            lines.append(f"  Post ID: {b['platform_post_id']}")
    if b.get("prompt"):
        p = b["prompt"]
        lines.append(f"  <i>{p[:80]}...</i>" if len(p) > 80 else f"  <i>{p}</i>")
    return lines


def _fmt_report(stats: dict) -> str:
    now_ist = datetime.now(timezone.utc) + _IST
    lines = []

    lines.append("<b>Pipeline Progress Report</b>")
    lines.append(f"<i>{now_ist.strftime('%d %b %Y, %I:%M %p IST')}</i>")
    lines.append(f"Total videos created: {stats['total_videos']}")
    lines.append("")

    # All-time uploads
    lines.append("<b>All-time Uploads (done)</b>")
    if stats["uploads_by_platform"]:
        for r in stats["uploads_by_platform"]:
            emoji = _PLATFORM_EMOJI.get(r["platform"], "")
            lines.append(f"  {emoji} {r['platform'].title()}: {r['total_done']}")
    else:
        lines.append("  No uploads yet")
    lines.append("")

    # All-time engagement
    if stats["engagement_by_platform"]:
        lines.append("<b>Engagement (All-time)</b>")
        for r in stats["engagement_by_platform"]:
            emoji = _PLATFORM_EMOJI.get(r["platform"], "")
            lines.append(
                f"  {emoji} {r['platform'].title()}: "
                f"{(r['total_views'] or 0):,} views | {(r['total_likes'] or 0):,} likes | "
                f"avg {r['avg_views']} views ({r['posts_with_data']} posts tracked)"
            )
        lines.append("")

    # Last 30 days
    lines.append("<b>Last 30 Days</b>")
    if stats["uploads_30d"]:
        for r in stats["uploads_30d"]:
            emoji = _PLATFORM_EMOJI.get(r["platform"], "")
            lines.append(f"  {emoji} {r['platform'].title()}: {r['done_30d']} uploads")
    else:
        lines.append("  No uploads in last 30 days")
    if stats["engagement_30d"]:
        for r in stats["engagement_30d"]:
            emoji = _PLATFORM_EMOJI.get(r["platform"], "")
            lines.append(
                f"  {emoji} {r['platform'].title()}: "
                f"{int(r['views_30d'] or 0):,} views | avg {r['avg_views_30d']} views/post"
            )
    lines.append("")

    # Best video all-time
    if stats["best_alltime"]:
        lines.extend(_fmt_best(stats["best_alltime"], "Best Video (All-time)"))
        lines.append("")

    # Best video 30 days
    if stats["best_30d"]:
        lines.extend(_fmt_best(stats["best_30d"], "Best Video (30 Days)"))
        lines.append("")

    # Failed uploads
    total_failed = sum(r["failed_count"] for r in stats["failed_by_platform"])
    if total_failed > 0:
        lines.append("<b>Failed Uploads</b>")
        for r in stats["failed_by_platform"]:
            if r["failed_count"] > 0:
                lines.append(
                    f"  {r['platform'].title()}: {r['failed_count']} total "
                    f"({r['failed_30d']} in 30d)"
                )
        lines.append("")

    # Per-niche breakdown
    if stats["niche_platform_counts"]:
        lines.append("<b>Per-niche Breakdown</b>")
        for (niche_id, platform), count in sorted(stats["niche_platform_counts"].items()):
            emoji = _PLATFORM_EMOJI.get(platform, "")
            niche_label = _NICHE_LABEL.get(niche_id, niche_id)
            lines.append(f"  {niche_label} / {emoji} {platform.title()}: {count}")
        lines.append("")

    # Hashtag strategy
    if stats["hashtags"]:
        lines.append("<b>Hashtag Strategy (active niches)</b>")
        for (niche_id, platform), tags in sorted(stats["hashtags"].items()):
            emoji = _PLATFORM_EMOJI.get(platform, "")
            niche_label = _NICHE_LABEL.get(niche_id, niche_id)
            tag_str = " ".join(tags)
            lines.append(f"<b>{niche_label} / {emoji} {platform.title()}</b>")
            lines.append(f"<code>{tag_str}</code>")

    msg = "\n".join(lines)
    if len(msg) > 4000:
        msg = msg[:3950] + "\n\n<i>[truncated]</i>"
    return msg


# ---------------------------------------------------------------------------
# Telegram send
# ---------------------------------------------------------------------------

def _send_telegram(msg: str) -> None:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not bot_token or not chat_id:
        log.warning("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — skipping Telegram send")
        return

    async def _send():
        from telegram import Bot
        from telegram.request import HTTPXRequest
        async with Bot(
            token=bot_token,
            request=HTTPXRequest(connect_timeout=30, read_timeout=60),
        ) as bot:
            await bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")

    asyncio.run(_send())
    log.info("Telegram message sent")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    conn = _resolve_db()
    try:
        stats = _gather_stats(conn)
    finally:
        conn.close()

    msg = _fmt_report(stats)
    print(msg)
    _send_telegram(msg)
    log.info("Progress report done")


if __name__ == "__main__":
    main()
