"""
pipeline/publisher.py — approve a finished video, upload it to Drive, schedule it.

Shared by every entry point (run_niche.py, run_skillstotraineyes.py). There is no
manual review gate: content safety is enforced upstream by each pipeline, and
Telegram is a notification only — nothing waits on it.

    publish(video_id, output_path, niche, conn, cfg, ...) -> list[dict]
"""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)


def _forced_time(schedule_time: str) -> datetime:
    """'HH:MM' IST -> next occurrence as an aware UTC datetime."""
    h_ist, m_ist = map(int, schedule_time.split(":"))
    ist_offset = timedelta(hours=5, minutes=30)
    now_ist = datetime.now(timezone.utc) + ist_offset
    target_ist = now_ist.replace(hour=h_ist, minute=m_ist, second=0, microsecond=0)
    if target_ist <= now_ist:
        target_ist += timedelta(days=1)
    force_time = (target_ist - ist_offset).replace(tzinfo=timezone.utc)
    log.info("Forcing upload time: %s IST = %s UTC", schedule_time, force_time.strftime("%Y-%m-%d %H:%M"))
    return force_time


def publish(
    video_id: int,
    output_path: str,
    niche: dict,
    conn,
    cfg,
    *,
    platforms: list[str] | None = None,
    schedule_time: str | None = None,
    title: str | None = None,
    caption: str = "",
    hashtags: list[str] | dict[str, list[str]] | None = None,
    notify_text: str = "",
    media_type: str = "video",
) -> list[dict]:
    """
    Mark the video approved, upload it (and its script JSON, if any) to Drive
    `pending/`, schedule it on each platform, and send a Telegram FYI.

    platforms:     defaults to every platform the scheduler knows about.
    schedule_time: 'HH:MM' IST to force; otherwise each platform's slot is
                   appended after the whole pending queue (next_queue_slot).
    title/caption/hashtags: forwarded into the schedule manifest.
    media_type:    "video" (default) or "image" (Instagram feed photo).
    Returns one schedule_video() summary per platform.
    """
    from pipeline.drive_storage import upload_to_drive
    from pipeline.scheduler import _PLATFORMS, next_queue_slot, schedule_video

    platforms = platforms or _PLATFORMS
    log.info("[5/5] Auto-approving — uploading to Drive + scheduling %s...", platforms)
    conn.execute("UPDATE videos SET status='approved' WHERE id=?", (video_id,))
    conn.commit()

    drive_file_id = upload_to_drive(output_path, folder_name="pending")
    slug = Path(output_path).stem
    script_path = Path(cfg.paths.get("scripts", "output/scripts")) / f"{slug}.json"
    drive_manifest_id = upload_to_drive(script_path, folder_name="pending") if script_path.exists() else ""

    force_time = _forced_time(schedule_time) if schedule_time else None
    results = []
    for platform in platforms:
        # No explicit schedule_time: append after the whole pending queue
        # (next_queue_slot) rather than pick_optimal_time's today/tomorrow
        # default, so a video generated mid-queue lands after everything
        # already scheduled, not interleaved into it.
        platform_time = force_time or next_queue_slot(niche["id"], platform, conn)
        results.append(schedule_video(
            video_id, niche["id"], drive_file_id, drive_manifest_id, conn,
            force_platform=platform, force_time=platform_time,
            title=title, caption=caption, hashtags=hashtags,
            media_type=media_type,
        ))
    log.info("Scheduled on %s.", platforms)

    # FYI only — no buttons, nothing waits on this.
    if notify_text:
        try:
            from review.telegram_bot import send_for_review
            send_for_review(video_id=video_id, file_path=output_path,
                            quote_text=notify_text, conn=conn)
            log.info("Notification sent to Telegram.")
        except Exception as e:
            log.warning("Telegram notification failed (non-fatal): %s", e)
    return results
