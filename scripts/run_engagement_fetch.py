"""Fetch engagement stats and recalculate optimal time slots.

Usage: python scripts/run_engagement_fetch.py
"""
import logging
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("engagement_fetch")


def main():
    from pipeline.drive_storage import download_from_drive, _build_service, _get_subfolder
    from pipeline.engagement_tracker import fetch_engagement, recalculate_time_performance
    from googleapiclient.http import MediaFileUpload

    service = _build_service()
    state_folder_id = _get_subfolder("state")

    results = service.files().list(
        q=f"'{state_folder_id}' in parents and name='schedule_db.sqlite' and trashed=false",
        spaces="drive",
        fields="files(id)",
    ).execute()
    files = results.get("files", [])

    if not files:
        log.info("No schedule DB found on Drive. Nothing to do.")
        return

    db_path = Path(tempfile.mkdtemp()) / "schedule.db"
    download_from_drive(files[0]["id"], db_path)

    conn = sqlite3.connect(str(db_path))
    updated = fetch_engagement(conn, lookback_hours=48)
    recalculated = recalculate_time_performance(conn, lookback_days=30)
    conn.close()

    log.info("Engagement updated: %d uploads", updated)
    log.info("Time slots recalculated: %d", recalculated)

    media = MediaFileUpload(str(db_path))
    service.files().update(fileId=files[0]["id"], media_body=media).execute()
    log.info("Schedule DB synced back to Drive")


if __name__ == "__main__":
    main()
