"""Delete local output files (video/audio/images) for old or rejected videos.

Deletes files for:
  - Videos with status rejected / permanently_rejected (always safe)
  - Videos with status assembled older than --keep-days (default 30)
    that have no pending upload_schedule row

Leaves everything approved, sent, queued, or recently assembled.

Usage:
    python scripts/run_local_cleanup.py
    python scripts/run_local_cleanup.py --keep-days 14
    python scripts/run_local_cleanup.py --dry-run
"""
import argparse
import logging
import shutil
import sqlite3
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("local_cleanup")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _slug_from_path(file_path: str) -> str | None:
    if not file_path:
        return None
    return Path(file_path).stem


def _delete_output_dirs(slug: str, dry_run: bool) -> int:
    """Remove output/video/{slug}.mp4, output/audio/{slug}/, output/images/{slug}/."""
    removed = 0
    candidates = [
        ROOT / "output" / "video" / f"{slug}.mp4",
        ROOT / "output" / "audio" / slug,
        ROOT / "output" / "images" / slug,
        ROOT / "output" / "scripts" / f"{slug}.json",
    ]
    for path in candidates:
        if path.exists():
            action = "would delete" if dry_run else "deleting"
            log.info("%s: %s", action, path)
            if not dry_run:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            removed += 1
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description="Remove local output files for old/rejected videos")
    parser.add_argument("--keep-days", type=int, default=30,
                        help="Keep assembled videos newer than this many days (default 30)")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be deleted")
    args = parser.parse_args()

    from config import cfg
    conn = sqlite3.connect(cfg.paths["db"])

    # Always-delete: rejected / permanently_rejected
    rejected_rows = conn.execute(
        "SELECT id, file_path FROM videos WHERE status IN ('rejected', 'permanently_rejected')"
    ).fetchall()

    # Delete old assembled with no pending schedule
    old_assembled_rows = conn.execute(
        f"""
        SELECT id, file_path FROM videos
        WHERE status = 'assembled'
          AND created_at < datetime('now', '-{args.keep_days} days')
          AND id NOT IN (SELECT DISTINCT video_id FROM upload_schedule WHERE status='pending')
        """
    ).fetchall()

    all_rows = rejected_rows + old_assembled_rows
    if not all_rows:
        log.info("Nothing to clean up.")
        conn.close()
        return

    log.info(
        "Candidates: %d rejected + %d old-assembled = %d total%s",
        len(rejected_rows), len(old_assembled_rows), len(all_rows),
        " [dry-run]" if args.dry_run else "",
    )

    total_removed = 0
    for video_id, file_path in all_rows:
        slug = _slug_from_path(file_path)
        if not slug:
            log.warning("video_id=%d has no file_path — skipping", video_id)
            continue
        removed = _delete_output_dirs(slug, dry_run=args.dry_run)
        total_removed += removed

    log.info("Done. %s %d file/dir entries.", "Would remove" if args.dry_run else "Removed", total_removed)
    conn.close()


if __name__ == "__main__":
    main()
