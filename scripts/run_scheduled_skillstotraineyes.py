"""
Generate + publish one skillstotraineyes tracking Reel on a GitHub Actions runner,
gated to 2 days a week: one on Saturday or Sunday, one on Tuesday/Wednesday/Thursday.

Which day in each pair fires is picked once per ISO week (seeded on year+week), so
it varies week to week rather than pinning e.g. always-Saturday. The workflow's
cron runs daily; a day that isn't the pick for its half of the week is a no-op.

Difficulty (level) and arena shape are already randomised per run inside
skillstotraineyes/drills.py (pick_level / pick_shape) — this script only decides
*whether* today generates a video, not which knobs it rolls.

Same DB pull-from-Drive / push-back-to-Drive pattern as run_scheduled_generation.py,
so topic/uniqueness history and quota usage survive between ephemeral runs.

Usage: python scripts/run_scheduled_skillstotraineyes.py [--family FAMILY] [--force]

The Sat/Sun + Tue/Wed/Thu day gate only applies to the default `tracking`
family — a non-tracking family is assumed to have its own cron cadence
(one trigger per family) and always generates when invoked.
"""
import argparse
import logging
import random
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("scheduled_skillstotraineyes")

WEEKEND_POOL = ("Saturday", "Sunday")
MIDWEEK_POOL = ("Tuesday", "Wednesday", "Thursday")


def picked_days(seed: int) -> tuple[str, str]:
    """One weekend day + one midweek day, stable for the whole ISO week."""
    rng = random.Random(seed)
    return rng.choice(WEEKEND_POOL), rng.choice(MIDWEEK_POOL)


def is_posting_day(today: datetime | None = None) -> bool:
    now = today or datetime.now(timezone.utc)
    iso_year, iso_week, _ = now.isocalendar()
    weekend_day, midweek_day = picked_days(iso_year * 100 + iso_week)
    weekday = now.strftime("%A")
    if weekday in (weekend_day, midweek_day):
        log.info("Today (%s) is this week's pick (weekend=%s, midweek=%s) — generating",
                 weekday, weekend_day, midweek_day)
        return True
    log.info("Today (%s) is not this week's pick (weekend=%s, midweek=%s) — skipping",
             weekday, weekend_day, midweek_day)
    return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--family", default="tracking")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-llm", action="store_true")
    args = parser.parse_args()

    if args.family == "tracking" and not args.force and not is_posting_day():
        return

    from googleapiclient.http import MediaFileUpload

    from pipeline.drive_storage import _build_service, _get_subfolder, _retry_drive, download_from_drive

    service = _build_service()
    state_folder_id = _get_subfolder("state")

    def _find(name: str) -> str | None:
        req = service.files().list(
            q=f"'{state_folder_id}' in parents and name='{name}' and trashed=false",
            spaces="drive", fields="files(id)",
        )
        files = _retry_drive(req.execute).get("files", [])
        return files[0]["id"] if files else None

    from config import cfg
    db_path = Path(cfg.paths["db"])
    db_path.parent.mkdir(parents=True, exist_ok=True)

    db_file_id = _find("schedule_db.sqlite")
    if db_file_id:
        download_from_drive(db_file_id, db_path)
        log.info("Restored DB from Drive state (%s)", db_path)
    else:
        log.info("No DB on Drive state yet — %s starts fresh", db_path)

    cmd = [sys.executable, str(ROOT / "run_skillstotraineyes.py"), "--family", args.family]
    if args.no_llm:
        cmd.append("--no-llm")
    result = subprocess.run(cmd, cwd=str(ROOT))

    if db_path.exists():
        media = MediaFileUpload(str(db_path))
        if db_file_id:
            req = service.files().update(fileId=db_file_id, media_body=media)
        else:
            req = service.files().create(
                body={"name": "schedule_db.sqlite", "parents": [state_folder_id]},
                media_body=media, fields="id",
            )
        _retry_drive(req.execute)
        log.info("DB synced back to Drive state")

    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
