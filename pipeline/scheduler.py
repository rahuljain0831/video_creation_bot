"""
pipeline/scheduler.py — Platform rotation, time selection, and cron-job.org scheduling.

Provides:
    get_next_platform(niche_id, conn) -> str
    pick_optimal_time(niche_id, platform, conn) -> datetime
    create_upload_job(schedule_id, scheduled_at, repo_owner, repo_name) -> str
    delete_upload_job(cronjob_id) -> None
    schedule_video(video_id, niche_id, drive_file_id, drive_manifest_id, conn) -> dict
"""

import logging
import os
import random
from datetime import datetime, timedelta, timezone

import requests

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


def _slot_available(candidate: datetime, min_gap_minutes: int, conn) -> bool:
    """Return True if no pending upload is within ±min_gap_minutes of candidate."""
    delta = timedelta(minutes=min_gap_minutes)
    low = (candidate - delta).strftime("%Y-%m-%d %H:%M:%S")
    high = (candidate + delta).strftime("%Y-%m-%d %H:%M:%S")
    row = conn.execute(
        "SELECT COUNT(*) FROM upload_schedule WHERE status='pending' "
        "AND scheduled_at BETWEEN ? AND ?",
        (low, high),
    ).fetchone()
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


def pick_optimal_time(niche_id: str, platform: str, conn) -> datetime:
    """
    Choose the best UTC datetime for the next upload.

    Strategy (in order):
    1. Exploration — with probability exploration_rate, pick a random hour 6-22 UTC.
    2. Adaptive  — query time_performance for best hours with enough samples.
    3. Defaults  — fall back to default IST times from config.
    4. For the winning hour, find the next available slot (today or tomorrow).
       Respects min_gap_minutes via _slot_available().
    5. Hard fallback: default hour tomorrow at :00.
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

    # --- Exploration ---
    if random.random() < exploration_rate:
        hour = random.randint(6, 22)
        candidate = now_utc.replace(hour=hour, minute=0, second=0, microsecond=0)
        if candidate <= now_utc:
            candidate += timedelta(days=1)
        if _slot_available(candidate, min_gap, conn):
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
        candidate = now_utc.replace(hour=hour, minute=0, second=0, microsecond=0)
        if candidate <= now_utc:
            candidate += timedelta(days=1)
        if _slot_available(candidate, min_gap, conn):
            return candidate

    # Hard fallback: first default hour, tomorrow
    fallback_hour = default_utc_hours[0] if default_utc_hours else 12
    fallback = now_utc.replace(hour=fallback_hour, minute=0, second=0, microsecond=0) + timedelta(
        days=1
    )
    return fallback


def create_upload_job(
    schedule_id: int,
    scheduled_at: datetime,
    repo_owner: str,
    repo_name: str,
) -> str:
    """
    Register a one-time cron job with cron-job.org to trigger a GitHub
    repository_dispatch event at scheduled_at.

    Returns the cron-job.org job ID string.
    Raises RuntimeError if the API call fails.
    """
    api_key = os.environ.get("CRONJOB_API_KEY", "")
    github_token = os.environ.get("GITHUB_DISPATCH_TOKEN", "")

    schedule = {
        "timezone": "UTC",
        "hours": [scheduled_at.hour],
        "mdays": [scheduled_at.day],
        "months": [scheduled_at.month],
        "wdays": [-1],
        "minutes": [scheduled_at.minute],
    }

    body = {
        "job": {
            "url": f"https://api.github.com/repos/{repo_owner}/{repo_name}/dispatches",
            "enabled": True,
            "saveResponses": False,
            "schedule": schedule,
            "requestMethod": 1,  # POST
            "extendedData": {
                "headers": [
                    {"name": "Authorization", "value": f"Bearer {github_token}"},
                    {"name": "Accept", "value": "application/vnd.github.v3+json"},
                    {"name": "Content-Type", "value": "application/json"},
                ],
                "body": (
                    '{"event_type":"scheduled-upload",'
                    f'"client_payload":{{"schedule_id":{schedule_id}}}}}'
                ),
            },
        }
    }

    resp = requests.put(
        "https://api.cron-job.org/jobs",
        json=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=15,
    )

    if not resp.ok:
        raise RuntimeError(
            f"cron-job.org API error {resp.status_code}: {resp.text[:200]}"
        )

    data = resp.json()
    job_id = str(data.get("jobId", data.get("job", {}).get("jobId", "")))
    return job_id


def delete_upload_job(cronjob_id: str) -> None:
    """
    Delete a cron-job.org job by ID.
    Logs a warning on failure but does not raise.
    """
    api_key = os.environ.get("CRONJOB_API_KEY", "")
    try:
        resp = requests.delete(
            f"https://api.cron-job.org/jobs/{cronjob_id}",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15,
        )
        if not resp.ok:
            logger.warning(
                "Failed to delete cron-job.org job %s: %s %s",
                cronjob_id,
                resp.status_code,
                resp.text[:200],
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Exception deleting cron-job.org job %s: %s", cronjob_id, exc)


def schedule_video(
    video_id: int,
    niche_id: str,
    drive_file_id: str,
    drive_manifest_id: str,
    conn,
) -> dict:
    """
    Full scheduling flow for a video:
    1. Rotate platform.
    2. Pick optimal upload time.
    3. Insert into upload_schedule.
    4. Register cron-job.org job.
    5. Update upload_schedule with cronjob_id.
    6. Return summary dict.
    """
    cfg = _get_scheduler_config()
    github_repo: str = cfg.get("github_repo", "your-username/video-creation-agent")
    repo_owner, repo_name = github_repo.split("/", 1)

    platform = get_next_platform(niche_id, conn)
    scheduled_at = pick_optimal_time(niche_id, platform, conn)
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

    cronjob_id = create_upload_job(schedule_id, scheduled_at, repo_owner, repo_name)

    conn.execute(
        "UPDATE upload_schedule SET cronjob_id=? WHERE id=?",
        (cronjob_id, schedule_id),
    )
    conn.commit()

    # Format IST display time
    ist_dt = scheduled_at + _IST_OFFSET
    scheduled_at_ist = ist_dt.strftime("%Y-%m-%d %H:%M IST")

    return {
        "schedule_id": schedule_id,
        "platform": platform,
        "scheduled_at": scheduled_at,
        "scheduled_at_ist": scheduled_at_ist,
        "cronjob_id": cronjob_id,
        "caption_variant": caption_variant,
    }
