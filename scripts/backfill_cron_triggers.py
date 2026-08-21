"""Create cron-job.org triggers for scheduled uploads that have none.

    python scripts/backfill_cron_triggers.py            # every pending row missing a trigger
    python scripts/backfill_cron_triggers.py --dry-run  # list them, create nothing

A row with `cronjob_id IS NULL` still has its Drive manifest, so the GitHub
Actions poll would eventually collect it — but only inside the poll window
(05:30-16:00 UTC). Anything scheduled outside that window needs the trigger.

Safe to re-run: rows that already have a cronjob_id are skipped, and a row is
only updated after the API returns a job id.
"""

import argparse
import logging
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import cfg
from pipeline.scheduler import create_upload_job, _get_scheduler_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("backfill_cron")

_IST_OFFSET = timedelta(hours=5, minutes=30)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="list the rows, create nothing")
    ap.add_argument("--pause", type=float, default=2.0,
                    help="seconds between API calls (default 2.0) — the API throttles a burst")
    args = ap.parse_args()

    github_repo = _get_scheduler_config().get("github_repo", "")
    if not github_repo:
        log.error("No scheduler.github_repo in settings.json — nothing to point a trigger at")
        sys.exit(1)
    repo_owner, repo_name = github_repo.split("/", 1)

    conn = sqlite3.connect(cfg.paths["db"])
    rows = conn.execute(
        "SELECT id, video_id, platform, scheduled_at FROM upload_schedule "
        "WHERE status='pending' AND (cronjob_id IS NULL OR cronjob_id='') "
        "ORDER BY scheduled_at"
    ).fetchall()

    if not rows:
        log.info("Nothing to backfill — every pending row already has a trigger")
        return

    now = datetime.now(timezone.utc)
    log.info("%d pending row(s) without a cron trigger", len(rows))

    created, skipped, failed = 0, 0, []
    for schedule_id, video_id, platform, scheduled_at_str in rows:
        scheduled_at = datetime.strptime(
            scheduled_at_str, "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=timezone.utc)
        ist = (scheduled_at + _IST_OFFSET).strftime("%Y-%m-%d %H:%M IST")

        if scheduled_at <= now:
            log.warning("schedule_id=%d already past (%s) — skipping", schedule_id, ist)
            skipped += 1
            continue

        if args.dry_run:
            log.info("[dry-run] schedule_id=%-4d video=%-5d %-10s %s",
                     schedule_id, video_id, platform, ist)
            continue

        try:
            cronjob_id = create_upload_job(schedule_id, scheduled_at, repo_owner, repo_name)
            conn.execute(
                "UPDATE upload_schedule SET cronjob_id=? WHERE id=?", (cronjob_id, schedule_id)
            )
            conn.commit()
            created += 1
            log.info("schedule_id=%-4d video=%-5d %-10s %s -> cronjob_id=%s",
                     schedule_id, video_id, platform, ist, cronjob_id)
        except Exception as exc:
            failed.append((schedule_id, str(exc)[:120]))
            log.error("schedule_id=%d failed: %s", schedule_id, exc)

        time.sleep(args.pause)

    conn.close()

    log.info("=" * 60)
    log.info("Created %d, skipped %d (already past), failed %d", created, skipped, len(failed))
    for schedule_id, err in failed:
        log.info("  FAILED schedule_id=%d — %s", schedule_id, err)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
