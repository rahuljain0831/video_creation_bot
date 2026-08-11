"""Clean up old files from Google Drive.

Usage: python scripts/run_drive_cleanup.py
"""
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("drive_cleanup")


def main():
    from pipeline.drive_storage import delete_old_files

    uploaded = delete_old_files("uploaded", older_than_days=7)
    failed = delete_old_files("failed", older_than_days=14)
    log.info("Cleaned up: %d uploaded, %d failed files", uploaded, failed)


if __name__ == "__main__":
    main()
