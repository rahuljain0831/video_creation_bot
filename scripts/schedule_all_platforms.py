"""Schedule approved videos to all 3 social media platforms.

Usage:
    python scripts/schedule_all_platforms.py 79 80 81
    python scripts/schedule_all_platforms.py --all-pending
"""

import json
import logging
import random
import sqlite3
import sys
import tempfile
from datetime import timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import cfg
from pipeline.scheduler import pick_optimal_time, _get_scheduler_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
log = logging.getLogger(__name__)

_PLATFORMS = ["youtube", "instagram", "facebook"]
_IST_OFFSET = timedelta(hours=5, minutes=30)


def _get_video_title(video_id, conn):
    """Get story title from script JSON or DB."""
    row = conn.execute("SELECT file_path FROM videos WHERE id=?", (video_id,)).fetchone()
    if row and row[0]:
        slug = Path(row[0]).stem
        script_path = Path(cfg.paths.get("scripts", "output/scripts")) / f"{slug}.json"
        if script_path.exists():
            try:
                data = json.loads(script_path.read_text())
                return data.get("story_title", slug)
            except Exception:
                pass
        return slug
    return f"Video {video_id}"


def schedule_on_platform(video_id, niche_id, drive_file_id, platform, conn, title=""):
    """Schedule a single video on a specific platform. Upload manifest to Drive."""
    from pipeline.drive_storage import upload_to_drive

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

    # Upload schedule manifest to Drive so GitHub Actions can find it
    manifest = {
        "schedule_id": schedule_id,
        "video_id": video_id,
        "platform": platform,
        "niche_id": niche_id,
        "drive_file_id": drive_file_id,
        "scheduled_at": scheduled_at_str,
        "title": title,
        "caption_variant": caption_variant,
    }

    tmp = Path(tempfile.mkdtemp()) / f"{schedule_id}_schedule.json"
    tmp.write_text(json.dumps(manifest, indent=2))
    manifest_drive_id = upload_to_drive(tmp, folder_name="pending")
    log.info("Manifest uploaded: schedule_id=%d drive_id=%s", schedule_id, manifest_drive_id)

    ist_dt = scheduled_at + _IST_OFFSET
    scheduled_ist = ist_dt.strftime("%Y-%m-%d %H:%M IST")

    log.info(
        "Scheduled: video_id=%d platform=%s at %s",
        video_id, platform, scheduled_ist,
    )
    return {
        "schedule_id": schedule_id,
        "platform": platform,
        "scheduled_at_ist": scheduled_ist,
    }


def process_video(video_id, conn):
    """Approve, upload to Drive, and schedule on all 3 platforms."""
    row = conn.execute(
        "SELECT niche_id, file_path, status FROM videos WHERE id=?", (video_id,)
    ).fetchone()

    if not row:
        log.error("video_id=%d not found in DB", video_id)
        return []

    niche_id, file_path, status = row

    if not file_path:
        log.error("video_id=%d has no file_path", video_id)
        return []

    video_path = Path(file_path)
    if not video_path.exists():
        log.error("video_id=%d file not found: %s", video_id, file_path)
        return []

    # Mark as approved
    if status != "approved":
        conn.execute("UPDATE videos SET status='approved' WHERE id=?", (video_id,))
        conn.execute(
            "INSERT INTO feedback (video_id, rating, source) VALUES (?, 'good', 'manual')",
            (video_id,),
        )
        conn.commit()
        log.info("video_id=%d marked approved", video_id)

    # Upload video to Drive
    from pipeline.drive_storage import upload_to_drive
    log.info("Uploading video_id=%d to Drive...", video_id)
    drive_file_id = upload_to_drive(video_path, folder_name="pending")
    log.info("Drive upload done: file_id=%s", drive_file_id)

    title = _get_video_title(video_id, conn)

    # Schedule on all 3 platforms
    results = []
    for platform in _PLATFORMS:
        info = schedule_on_platform(
            video_id, niche_id, drive_file_id, platform, conn, title=title,
        )
        results.append(info)

    return results


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/schedule_all_platforms.py <video_id> [video_id ...]")
        print("       python scripts/schedule_all_platforms.py --all-pending")
        sys.exit(1)

    conn = sqlite3.connect(cfg.paths["db"])
    conn.execute("PRAGMA journal_mode=WAL")

    if sys.argv[1] == "--all-pending":
        rows = conn.execute(
            "SELECT id FROM videos WHERE status IN ('assembled', 'sent') ORDER BY id"
        ).fetchall()
        video_ids = [r[0] for r in rows]
        if not video_ids:
            log.info("No pending videos found")
            sys.exit(0)
    else:
        video_ids = [int(x) for x in sys.argv[1:]]

    log.info("Scheduling %d videos on all 3 platforms", len(video_ids))

    all_results = []
    for vid in video_ids:
        results = process_video(vid, conn)
        all_results.extend(results)

    conn.close()

    print("\n" + "=" * 60)
    print("SCHEDULE SUMMARY")
    print("=" * 60)
    for r in all_results:
        print(f"  {r['platform']:12s} | {r['scheduled_at_ist']}")
    print("=" * 60)
    print(f"Total: {len(all_results)} uploads scheduled")


if __name__ == "__main__":
    main()
