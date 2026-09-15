"""
pipeline/scheduler.py — Platform rotation, time selection, and upload_schedule bookkeeping.

Provides:
    get_next_platform(niche_id, conn) -> str
    pick_optimal_time(niche_id, platform, conn) -> datetime
    schedule_video(video_id, niche_id, drive_file_id, drive_manifest_id, conn) -> dict
"""

import logging
import random
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_PLATFORMS = ["youtube", "instagram", "facebook"]
_IST_OFFSET = timedelta(hours=5, minutes=30)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_scheduler_config() -> dict:
    """Return the scheduler config section from settings.json via cfg."""
    from config import cfg  # local import keeps module importable standalone

    return cfg.scheduler


def _ist_to_utc_hour(ist_time_str: str) -> int:
    """Convert IST time string like '17:00' to UTC hour integer."""
    h, m = map(int, ist_time_str.split(":"))
    ist_dt = datetime(2000, 1, 1, h, m, tzinfo=timezone.utc) - _IST_OFFSET
    return ist_dt.hour % 24


def _slot_available(candidate: datetime, min_gap_minutes: int, conn, platform=None) -> bool:
    """
    Return True if no pending upload is within ±min_gap_minutes of candidate.

    platform=None keeps the original global check (any platform occupies the slot).
    Passing a platform scopes the check to that platform, which is what a batch
    schedule needs — otherwise 3 platforms × N videos starve each other out.
    """
    delta = timedelta(minutes=min_gap_minutes)
    low = (candidate - delta).strftime("%Y-%m-%d %H:%M:%S")
    high = (candidate + delta).strftime("%Y-%m-%d %H:%M:%S")

    sql = ("SELECT COUNT(*) FROM upload_schedule WHERE status='pending' "
           "AND scheduled_at BETWEEN ? AND ?")
    params = [low, high]
    if platform is not None:
        sql += " AND platform=?"
        params.append(platform)

    row = conn.execute(sql, params).fetchone()
    return row[0] == 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_next_platform(niche_id: str, conn) -> str:
    """
    Rotate to the next platform in round-robin order.

    Reads `platform_rotation` for last_platform. If no row exists returns
    'youtube'. Updates the table and commits before returning.
    """
    row = conn.execute(
        "SELECT last_platform FROM platform_rotation WHERE niche_id = ?",
        (niche_id,),
    ).fetchone()

    if row is None:
        next_platform = _PLATFORMS[0]  # "youtube"
    else:
        last_idx = _PLATFORMS.index(row[0])
        next_platform = _PLATFORMS[(last_idx + 1) % len(_PLATFORMS)]

    conn.execute(
        "INSERT OR REPLACE INTO platform_rotation (niche_id, last_platform, updated_at) "
        "VALUES (?, ?, datetime('now'))",
        (niche_id, next_platform),
    )
    conn.commit()
    return next_platform


def pick_optimal_time(
    niche_id: str,
    platform: str,
    conn,
    *,
    on_date=None,
    earliest: datetime | None = None,
):
    """
    Choose the best UTC datetime for the next upload.

    Strategy (in order):
    1. Exploration — with probability exploration_rate, pick a random hour 6-22 UTC.
    2. Adaptive  — query time_performance for best hours with enough samples.
    3. Defaults  — fall back to default IST times from config.
    4. For the winning hour, find the next available slot (today or tomorrow).
       Respects min_gap_minutes via _slot_available().
    5. Hard fallback: default hour tomorrow at :00.

    Batch mode (on_date given, a datetime.date in UTC):
        Every candidate is built on that exact date instead of rolling to
        tomorrow, exploration is skipped (a random 6-22 UTC hour would scatter
        across days), and the slot check is scoped to this platform. Returns
        None when nothing on that date qualifies, so the caller can apply its
        own fallback rather than silently landing on another day.

    earliest: reject any candidate before this aware-UTC datetime.
    """
    cfg = _get_scheduler_config()
    exploration_rate: float = cfg.get("exploration_rate", 0.2)
    trusted_sample_min: int = cfg.get("trusted_sample_min", 3)
    min_gap: int = cfg.get("min_gap_minutes", 30)
    default_times_ist: dict = cfg.get("default_times_ist", {})

    # Build default UTC hours for this platform
    ist_slots = default_times_ist.get(platform, ["17:00", "20:00", "12:00"])
    default_utc_hours = [_ist_to_utc_hour(t) for t in ist_slots]

    now_utc = datetime.now(timezone.utc)
    batch_mode = on_date is not None
    # In batch mode the slot check is per-platform; the legacy path stays global.
    slot_platform = platform if batch_mode else None

    def _build(hour: int) -> datetime | None:
        """Materialise a candidate for `hour`, or None if it cannot be used."""
        if batch_mode:
            candidate = datetime(
                on_date.year, on_date.month, on_date.day, hour, tzinfo=timezone.utc
            )
            if candidate <= now_utc:
                return None            # that hour is already gone on that date
        else:
            candidate = now_utc.replace(hour=hour, minute=0, second=0, microsecond=0)
            if candidate <= now_utc:
                candidate += timedelta(days=1)
        if earliest is not None and candidate < earliest:
            return None
        return candidate

    # --- Exploration (never in batch mode — it would jump days) ---
    if not batch_mode and random.random() < exploration_rate:
        hour = random.randint(6, 22)
        candidate = _build(hour)
        if candidate is not None and _slot_available(candidate, min_gap, conn, slot_platform):
            return candidate
        # If slot taken during exploration, fall through to defaults

    # --- Adaptive from time_performance ---
    rows = conn.execute(
        "SELECT hour_utc, avg_views FROM time_performance "
        "WHERE niche_id=? AND platform=? AND sample_count>=? "
        "ORDER BY avg_views DESC LIMIT 5",
        (niche_id, platform, trusted_sample_min),
    ).fetchall()

    adaptive_hours = [r[0] for r in rows]

    # Blend: adaptive first, then defaults
    candidate_hours = adaptive_hours + [h for h in default_utc_hours if h not in adaptive_hours]

    # Find first available slot
    for hour in candidate_hours:
        candidate = _build(hour)
        if candidate is not None and _slot_available(candidate, min_gap, conn, slot_platform):
            return candidate

    if batch_mode:
        return None

    # Hard fallback: first default hour, tomorrow
    fallback_hour = default_utc_hours[0] if default_utc_hours else 12
    fallback = now_utc.replace(hour=fallback_hour, minute=0, second=0, microsecond=0) + timedelta(
        days=1
    )
    return fallback


def schedule_video(
    video_id: int,
    niche_id: str,
    drive_file_id: str,
    drive_manifest_id: str,
    conn,
    *,
    force_platform: str | None = None,
    force_time: datetime | None = None,
) -> dict:
    """
    Full scheduling flow for a video:
    1. Rotate platform (or use force_platform to target a specific one).
    2. Pick optimal upload time.
    3. Insert into upload_schedule.
    4. Write the schedule manifest to Drive pending/ (GitHub Actions cron polls for it).
    5. Return summary dict.

    force_platform: when set, skip round-robin and schedule for exactly this
    platform ("youtube" | "instagram" | "facebook"). Existing callers are
    unaffected (they pass nothing, so round-robin behaviour is unchanged).
    """
    if force_platform is not None:
        if force_platform not in _PLATFORMS:
            raise ValueError(f"force_platform must be one of {_PLATFORMS}, got {force_platform!r}")
        platform = force_platform
    else:
        platform = get_next_platform(niche_id, conn)
    if force_time is not None:
        scheduled_at = force_time if force_time.tzinfo else force_time.replace(tzinfo=timezone.utc)
    else:
        scheduled_at = pick_optimal_time(niche_id, platform, conn)
    if scheduled_at <= datetime.now(timezone.utc):
        logger.warning("Scheduled time %s is in the past — pushing to tomorrow", scheduled_at)
        scheduled_at = scheduled_at + timedelta(days=1)
    caption_variant = random.choice(["A", "B"])

    scheduled_at_str = scheduled_at.strftime("%Y-%m-%d %H:%M:%S")

    cur = conn.execute(
        "INSERT INTO upload_schedule "
        "(video_id, platform, niche_id, scheduled_at, status, drive_file_id, caption_variant) "
        "VALUES (?, ?, ?, ?, 'pending', ?, ?)",
        (video_id, platform, niche_id, scheduled_at_str, drive_file_id, caption_variant),
    )
    conn.commit()
    schedule_id = cur.lastrowid

    # Write the initial manifest JSON to Drive pending/ so run_scheduled_upload.py can find it.
    # _write_retry_manifest() handles subsequent retries; this covers the first attempt.
    try:
        import json
        import shutil
        import tempfile
        from pathlib import Path
        from pipeline.drive_storage import upload_to_drive as _upload_manifest
        _title = "Untitled"
        _row = conn.execute("SELECT file_path FROM videos WHERE id=?", (video_id,)).fetchone()
        if _row and _row[0]:
            _slug = Path(_row[0]).stem
            _script_path = Path("output/scripts") / f"{_slug}.json"
            if _script_path.exists():
                _script_data = json.loads(_script_path.read_text(encoding="utf-8"))
                _title = _script_data.get("script", {}).get("story_title", _slug)
            else:
                _title = _slug
        _manifest = {
            "schedule_id": schedule_id,
            "platform": platform,
            "drive_file_id": drive_file_id,
            "scheduled_at": scheduled_at_str,
            "niche_id": niche_id,
            "title": _title,
            "caption": "",
            "hashtags": [],
            "retry_count": 0,
        }
        _tmp_dir = tempfile.mkdtemp()
        _tmp = Path(_tmp_dir) / f"{schedule_id}_schedule.json"
        _tmp.write_text(json.dumps(_manifest))
        _upload_manifest(_tmp, folder_name="pending")
        shutil.rmtree(_tmp_dir, ignore_errors=True)
        logger.info("Manifest uploaded: %s (title=%s)", _tmp.name, _title)
    except Exception as _e:
        logger.error("Failed to upload schedule manifest: %s", _e)

    # Format IST display time
    ist_dt = scheduled_at + _IST_OFFSET
    scheduled_at_ist = ist_dt.strftime("%Y-%m-%d %H:%M IST")

    return {
        "schedule_id": schedule_id,
        "platform": platform,
        "scheduled_at": scheduled_at,
        "scheduled_at_ist": scheduled_at_ist,
        "caption_variant": caption_variant,
    }
