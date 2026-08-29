#!/usr/bin/env python3
"""
Drop a finished video into the upload schedule.

Usage:
    python scripts/schedule_manual.py --video path/to/video.mp4
    python scripts/schedule_manual.py --video path/to/video.mp4 --platforms youtube instagram
    python scripts/schedule_manual.py --video path/to/video.mp4 --dry-run

Platforms: youtube, instagram, facebook  (default: all three)
"""

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger("schedule_manual")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_ALL_PLATFORMS = ["youtube", "instagram", "facebook"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Schedule a video for upload")
    parser.add_argument("--video", required=True, help="Path to the finished .mp4 file")
    parser.add_argument(
        "--platforms",
        nargs="+",
        choices=_ALL_PLATFORMS,
        default=_ALL_PLATFORMS,
        metavar="PLATFORM",
        help="Platforms to schedule (default: all three)",
    )
    parser.add_argument("--niche", default="mythology", help="Niche ID (default: mythology)")
    parser.add_argument("--dry-run", action="store_true", help="Show schedule without uploading")
    args = parser.parse_args()

    video_path = Path(args.video).resolve()
    if not video_path.exists():
        log.error("Video not found: %s", video_path)
        sys.exit(1)

    file_size_mb = video_path.stat().st_size / 1_048_576
    log.info("Video: %s (%.1f MB)", video_path.name, file_size_mb)

    from config import cfg
    from pipeline.scheduler import schedule_video

    db_path = Path(cfg.paths["db"])
    conn = sqlite3.connect(str(db_path))

    if args.dry_run:
        print(f"\n{'=' * 58}")
        print(f"  DRY RUN — nothing uploaded or scheduled")
        print(f"{'=' * 58}")
        print(f"  Video     : {video_path} ({file_size_mb:.1f} MB)")
        print(f"  Niche     : {args.niche}")
        print(f"  Platforms : {', '.join(args.platforms)}")
        print(f"\n  Would upload to Google Drive (folder=pending)")
        for p in args.platforms:
            print(f"  Would schedule for {p}")
        print(f"{'=' * 58}\n")
        conn.close()
        return

    # Insert DB row
    cur = conn.execute(
        "INSERT INTO videos (status, file_path, niche_id) VALUES ('assembled', ?, ?)",
        (str(video_path), args.niche),
    )
    conn.commit()
    video_id = cur.lastrowid
    log.info("DB row created: video_id=%d", video_id)

    # Upload to Drive
    from pipeline.drive_storage import upload_to_drive

    log.info("Uploading to Google Drive ...")
    try:
        drive_file_id = upload_to_drive(video_path, folder_name="pending")
        log.info("Drive file ID: %s", drive_file_id)
    except Exception as exc:
        log.error("Drive upload failed: %s", exc)
        conn.execute("DELETE FROM videos WHERE id=?", (video_id,))
        conn.commit()
        conn.close()
        sys.exit(1)

    # Schedule per platform
    results = []
    for platform in args.platforms:
        log.info("Scheduling for %s ...", platform)
        try:
            result = schedule_video(
                video_id=video_id,
                niche_id=args.niche,
                drive_file_id=drive_file_id,
                drive_manifest_id="",
                conn=conn,
                force_platform=platform,
            )
            results.append(result)
        except Exception as exc:
            log.error("Failed to schedule %s: %s", platform, exc)

    conn.execute("UPDATE videos SET status='sent' WHERE id=?", (video_id,))
    conn.commit()
    conn.close()

    print(f"\n{'=' * 58}")
    print(f"  SCHEDULED")
    print(f"{'=' * 58}")
    print(f"  Video ID  : {video_id}")
    print(f"  Drive ID  : {drive_file_id}")
    print()
    if results:
        print(f"  {'Platform':<12} {'Scheduled (IST)':<22} Cron Job ID")
        print(f"  {'-'*12} {'-'*22} {'-'*20}")
        for r in results:
            print(f"  {r['platform']:<12} {r['scheduled_at_ist']:<22} {r['cronjob_id']}")
    else:
        print("  No platforms scheduled.")
    print(f"{'=' * 58}\n")


if __name__ == "__main__":
    main()
