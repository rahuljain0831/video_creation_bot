"""Schedule approved videos to all 3 social media platforms.

Usage:
    python scripts/schedule_all_platforms.py 79 80 81
    python scripts/schedule_all_platforms.py --all-pending

    # batch layout: N videos per day, video 1 of each day pinned to an IST clock time
    python scripts/schedule_all_platforms.py 90 91 92 93 \
        --start "2026-08-20 21:00" --per-day 3 --anchor-first
    python scripts/schedule_all_platforms.py 90 91 --dry-run   # print slots, touch nothing

Without --start the behaviour is unchanged: every platform slot comes from
pick_optimal_time() and lands today or tomorrow.
"""

import argparse
import json
import logging
import random
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import cfg
from pipeline.scheduler import pick_optimal_time, create_upload_job, _get_scheduler_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
log = logging.getLogger(__name__)

_PLATFORMS = ["youtube", "instagram", "facebook"]
_IST_OFFSET = timedelta(hours=5, minutes=30)
_IST = timezone(_IST_OFFSET)


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


def _ist(dt_utc):
    """Format an aware UTC datetime as an IST string."""
    return (dt_utc + _IST_OFFSET).strftime("%Y-%m-%d %H:%M IST")


def resolve_slot(niche_id, platform, conn, slot):
    """
    Return the aware-UTC datetime this (video, platform) should publish at.

    slot is None            -> legacy behaviour, pick_optimal_time picks the day.
    slot["anchor"]          -> pinned datetime, used verbatim on every platform.
    otherwise               -> pick_optimal_time constrained to slot["on_date"]
                               (and slot["earliest"], when set), falling back to
                               slot["fallback"] if nothing on that date qualifies.
    """
    if slot is None:
        return pick_optimal_time(niche_id, platform, conn)

    if slot.get("anchor") is not None:
        return slot["anchor"]

    chosen = pick_optimal_time(
        niche_id, platform, conn,
        on_date=slot["on_date"],
        earliest=slot.get("earliest"),
    )
    if chosen is None:
        log.info(
            "No slot on %s for platform=%s — using fallback %s",
            slot["on_date"], platform, _ist(slot["fallback"]),
        )
        return slot["fallback"]
    return chosen


def schedule_on_platform(video_id, niche_id, drive_file_id, platform, conn,
                         title="", slot=None, dry_run=False):
    """Schedule a single video on a specific platform. Upload manifest to Drive."""
    scheduled_at = resolve_slot(niche_id, platform, conn, slot)
    caption_variant = random.choice(["A", "B"])
    scheduled_at_str = scheduled_at.strftime("%Y-%m-%d %H:%M:%S")

    if dry_run:
        # Insert anyway — pick_optimal_time reads pending rows to space slots
        # apart, so a dry run that writes nothing reports collisions the real
        # run would never produce. main() deletes these rows before exiting.
        cur = conn.execute(
            "INSERT INTO upload_schedule "
            "(video_id, platform, niche_id, scheduled_at, status, caption_variant) "
            "VALUES (?, ?, ?, ?, 'pending', ?)",
            (video_id, platform, niche_id, scheduled_at_str, caption_variant),
        )
        log.info(
            "[dry-run] video_id=%s platform=%-9s utc=%s  %s",
            video_id, platform, scheduled_at_str, _ist(scheduled_at),
        )
        return {
            "schedule_id": cur.lastrowid,
            "video_id": video_id,
            "platform": platform,
            "scheduled_at_utc": scheduled_at_str,
            "scheduled_at_ist": _ist(scheduled_at),
        }

    from pipeline.drive_storage import upload_to_drive

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

    # Register cron-job.org trigger for GitHub Actions dispatch
    scheduler_cfg = _get_scheduler_config()
    github_repo = scheduler_cfg.get("github_repo", "")
    if github_repo:
        repo_owner, repo_name = github_repo.split("/", 1)
        try:
            cronjob_id = create_upload_job(schedule_id, scheduled_at, repo_owner, repo_name)
            conn.execute(
                "UPDATE upload_schedule SET cronjob_id=? WHERE id=?",
                (cronjob_id, schedule_id),
            )
            conn.commit()
            log.info("Cron trigger created: schedule_id=%d cronjob_id=%s", schedule_id, cronjob_id)
        except Exception as exc:
            log.error("Failed to create cron trigger for schedule_id=%d: %s", schedule_id, exc)
    else:
        log.warning("No github_repo in scheduler config — skipping cron trigger")

    scheduled_ist = _ist(scheduled_at)

    log.info(
        "Scheduled: video_id=%d platform=%s at %s",
        video_id, platform, scheduled_ist,
    )
    return {
        "schedule_id": schedule_id,
        "video_id": video_id,
        "platform": platform,
        "scheduled_at_utc": scheduled_at_str,
        "scheduled_at_ist": scheduled_ist,
    }


def process_video(video_id, conn, slot=None, dry_run=False):
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

    if dry_run:
        return [
            schedule_on_platform(video_id, niche_id, None, p, conn, slot=slot, dry_run=True)
            for p in _PLATFORMS
        ]

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
            video_id, niche_id, drive_file_id, platform, conn, title=title, slot=slot,
        )
        results.append(info)

    return results


def build_slot(index, anchor_ist, per_day, anchor_first):
    """
    Slot spec for the index-th video of the batch.

    Videos are laid out per_day at a time. Slot 0 of each day is pinned to the
    anchor clock time (same wall time every day) when --anchor-first is set; the
    rest are adaptive within that day, with a fallback of anchor+1h, anchor+2h…
    so a day that has no qualifying slot still lands after the anchor.

    Day 0 additionally carries earliest=anchor — "starting tonight 9 PM" means
    nothing publishes before it, even though the platform defaults are earlier.
    """
    day, slot_no = divmod(index, per_day)
    day_anchor_ist = anchor_ist + timedelta(days=day)
    day_anchor_utc = day_anchor_ist.astimezone(timezone.utc)

    if slot_no == 0 and anchor_first:
        return {"anchor": day_anchor_utc}

    return {
        "anchor": None,
        "on_date": day_anchor_utc.date(),
        "earliest": day_anchor_utc if day == 0 else None,
        "fallback": day_anchor_utc + timedelta(hours=slot_no),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Approve, upload to Drive, and schedule videos on all 3 platforms.",
    )
    parser.add_argument("video_ids", nargs="*", type=int, help="Video IDs to schedule")
    parser.add_argument("--all-pending", action="store_true",
                        help="Schedule every video with status assembled/sent (careful: that is "
                             "the whole back catalogue)")
    parser.add_argument("--start", metavar="'YYYY-MM-DD HH:MM'",
                        help="IST anchor for the batch layout. Omit for legacy behaviour.")
    parser.add_argument("--per-day", type=int, default=3,
                        help="Videos per day when --start is given (default 3)")
    parser.add_argument("--anchor-first", action="store_true",
                        help="Pin video 1 of each day to the anchor time on all 3 platforms")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the computed slots. No DB writes, no Drive, no cron jobs.")
    args = parser.parse_args()

    if not args.video_ids and not args.all_pending:
        parser.error("give one or more video_ids, or --all-pending")

    anchor_ist = None
    if args.start:
        try:
            anchor_ist = datetime.strptime(args.start, "%Y-%m-%d %H:%M").replace(tzinfo=_IST)
        except ValueError:
            parser.error("--start must look like '2026-08-20 21:00' (IST)")
        if args.per_day < 1:
            parser.error("--per-day must be >= 1")

    conn = sqlite3.connect(cfg.paths["db"])
    conn.execute("PRAGMA journal_mode=WAL")

    if args.all_pending:
        rows = conn.execute(
            "SELECT id FROM videos WHERE status IN ('assembled', 'sent') ORDER BY id"
        ).fetchall()
        video_ids = [r[0] for r in rows]
        if not video_ids:
            log.info("No pending videos found")
            sys.exit(0)
    else:
        video_ids = args.video_ids

    log.info("Scheduling %d videos on all 3 platforms%s",
             len(video_ids), " [dry-run]" if args.dry_run else "")
    if anchor_ist:
        log.info("Anchor %s, %d per day, anchor_first=%s",
                 anchor_ist.strftime("%Y-%m-%d %H:%M IST"), args.per_day, args.anchor_first)

    all_results = []
    try:
        for idx, vid in enumerate(video_ids):
            slot = (build_slot(idx, anchor_ist, args.per_day, args.anchor_first)
                    if anchor_ist else None)
            results = process_video(vid, conn, slot=slot, dry_run=args.dry_run)
            all_results.extend(results)
    finally:
        if args.dry_run:
            # Undo the probe rows. Nothing was committed, so a rollback is enough;
            # the explicit delete covers a stray autocommit.
            ids = [r["schedule_id"] for r in all_results if r.get("schedule_id")]
            if ids:
                conn.executemany(
                    "DELETE FROM upload_schedule WHERE id=?", [(i,) for i in ids]
                )
            conn.rollback()
            log.info("[dry-run] removed %d probe rows", len(ids))

    conn.close()

    print("\n" + "=" * 72)
    print("SCHEDULE SUMMARY" + (" (dry-run — nothing was written)" if args.dry_run else ""))
    print("=" * 72)
    for r in sorted(all_results, key=lambda x: x["scheduled_at_utc"]):
        print(f"  video {r['video_id']:<5} {r['platform']:12s} | "
              f"{r['scheduled_at_utc']} UTC | {r['scheduled_at_ist']}")
    print("=" * 72)
    print(f"Total: {len(all_results)} uploads scheduled")


if __name__ == "__main__":
    main()
