"""Generate one video end-to-end on a GitHub Actions runner.

Thin wrapper around run_niche.py: pulls the persisted DB from Drive first (so
topic/title dedup and quota history carry over between ephemeral runs), runs
the pipeline, pushes the DB back — win or lose, a run that burns quota and
then fails is exactly when you want that usage recorded.

Usage: python scripts/run_scheduled_generation.py <niche_id>
"""
import logging
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("scheduled_generation")


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/run_scheduled_generation.py <niche_id>")
        sys.exit(1)
    niche_id = sys.argv[1]

    from pipeline.drive_storage import _build_service, _get_subfolder, download_from_drive
    from googleapiclient.http import MediaFileUpload

    service = _build_service()
    state_folder_id = _get_subfolder("state")

    def _find(name: str) -> str | None:
        results = service.files().list(
            q=f"'{state_folder_id}' in parents and name='{name}' and trashed=false",
            spaces="drive", fields="files(id)",
        ).execute()
        files = results.get("files", [])
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

    result = subprocess.run(
        [sys.executable, str(ROOT / "run_niche.py"), niche_id],
        cwd=str(ROOT),
    )

    # Sync the DB back regardless of success/failure.
    if db_path.exists():
        media = MediaFileUpload(str(db_path))
        if db_file_id:
            service.files().update(fileId=db_file_id, media_body=media).execute()
        else:
            service.files().create(
                body={"name": "schedule_db.sqlite", "parents": [state_folder_id]},
                media_body=media,
                fields="id",
            ).execute()
        log.info("DB synced back to Drive state")

    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
