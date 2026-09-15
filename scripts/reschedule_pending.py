"""
One-shot: reschedule all pending uploads from 9 PM IST tonight.
- Re-uploads video files to Drive (old IDs are dead)
- Uploads fresh manifests to Drive pending/ folder
- Updates upload_schedule table

Run: python scripts/reschedule_pending.py
"""
import json, sqlite3, sys, tempfile, logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv; load_dotenv()
import os

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("reschedule")

IST = timedelta(hours=5, minutes=30)
GAP = timedelta(minutes=90)   # gap between video slots

def main():
    from pipeline.drive_storage import upload_to_drive

    conn = sqlite3.connect(ROOT / "output/db/agent.db")

    # Get failed (or pending) rows joined with local file path
    rows = conn.execute("""
        SELECT u.id, u.video_id, u.platform, u.niche_id, u.caption_variant,
               v.file_path
        FROM upload_schedule u JOIN videos v ON u.video_id = v.id
        WHERE u.status IN ('pending', 'failed')
        ORDER BY u.video_id, u.id
    """).fetchall()

    if not rows:
        log.info("No pending/failed rows.")
        return
    log.info("Found %d rows across %d videos", len(rows), len({r[1] for r in rows}))

    # First slot: now + 10 min. Subsequent video slots: +90 min each.
    now_utc = datetime.now(timezone.utc)
    start = now_utc + timedelta(minutes=10)

    # One Drive upload per unique video_id; cache new drive_file_id
    video_drive_id: dict[int, str] = {}
    slot_map: dict[int, datetime] = {}
    slot = start
    for row in rows:
        _, video_id, _, _, _, file_path = row
        if video_id in video_drive_id:
            continue
        local = ROOT / file_path
        if not local.exists():
            log.error("Missing local file for video %d: %s — skipping", video_id, file_path)
            continue
        log.info("Uploading video %d to Drive...", video_id)
        new_drive_id = upload_to_drive(local, folder_name="pending")
        video_drive_id[video_id] = new_drive_id
        slot_map[video_id] = slot
        slot += GAP
        log.info("video %d uploaded: %s", video_id, new_drive_id)

    # Now create manifest per row
    for row in rows:
        schedule_id, video_id, platform, niche_id, caption_variant, _ = row
        if video_id not in video_drive_id:
            log.warning("schedule_id=%d skipped (no Drive file)", schedule_id)
            continue

        scheduled_at = slot_map[video_id]
        scheduled_at_str = scheduled_at.strftime("%Y-%m-%d %H:%M:%S")
        new_drive_file_id = video_drive_id[video_id]

        # Manifest JSON in Drive pending/
        manifest = {
            "schedule_id": schedule_id,
            "video_id": video_id,
            "platform": platform,
            "niche_id": niche_id,
            "drive_file_id": new_drive_file_id,
            "scheduled_at": scheduled_at_str,
            "caption_variant": caption_variant or "A",
        }
        tmp = Path(tempfile.mkdtemp()) / f"{schedule_id}_schedule.json"
        tmp.write_text(json.dumps(manifest, indent=2))
        upload_to_drive(tmp, folder_name="pending")

        conn.execute(
            "UPDATE upload_schedule SET scheduled_at=?, drive_file_id=?, status='pending' WHERE id=?",
            (scheduled_at_str, new_drive_file_id, schedule_id)
        )
        conn.commit()

        ist_str = (scheduled_at + IST).strftime("%Y-%m-%d %H:%M IST")
        log.info("schedule_id=%d %s/%s -> %s", schedule_id, platform, niche_id, ist_str)

    log.info("Done.")
    conn.close()

if __name__ == "__main__":
    main()
