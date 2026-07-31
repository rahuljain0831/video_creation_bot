"""
Ingest user-provided deity images into the image library.

Usage:
    python ingest_library.py --folder /path/to/images
    python ingest_library.py --folder /path/to/images --tradition hindu
    python ingest_library.py --folder /path/to/images --re-ingest
"""

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze and index deity images into the video pipeline library."
    )
    parser.add_argument(
        "--folder", required=True,
        help="Path to folder containing deity images (.jpg/.jpeg/.png/.webp)",
    )
    parser.add_argument(
        "--tradition", default="",
        choices=["hindu", "greek", "norse", "egyptian", "buddhist", "other", ""],
        help="Optional tradition hint to help Gemini (Gemini still auto-detects)",
    )
    parser.add_argument(
        "--re-ingest", action="store_true",
        help="Re-analyze images already in DB (default: skip existing)",
    )
    args = parser.parse_args()

    from config import cfg
    from db.init_db import init_db
    from pipeline.image_library import ingest_image_folder

    if not cfg.GOOGLE_AI_STUDIO_API_KEY:
        log.error("GOOGLE_AI_STUDIO_API_KEY not set in .env — required for vision analysis")
        sys.exit(1)

    folder = Path(args.folder)
    if not folder.exists():
        log.error("Folder not found: %s", args.folder)
        sys.exit(1)

    log.info("Initializing database...")
    init_db(cfg.paths["db"])

    conn = sqlite3.connect(cfg.paths["db"])
    conn.execute("PRAGMA foreign_keys=ON")

    log.info("Starting ingestion: folder=%s tradition=%s", args.folder, args.tradition or "auto")

    result = ingest_image_folder(
        folder=args.folder,
        conn=conn,
        cfg=cfg,
        tradition_hint=args.tradition,
        skip_existing=not args.re_ingest,
    )
    conn.close()

    print()
    print("=" * 50)
    print(f"  Ingestion complete")
    print(f"  Processed : {result['processed']}")
    print(f"  Skipped   : {result['skipped']}  (already in DB)")
    print(f"  Failed    : {result['failed']}")
    if result["failed_paths"]:
        print(f"\n  Failed files:")
        for p in result["failed_paths"]:
            print(f"    {p}")
    print("=" * 50)

    total = conn.execute("SELECT COUNT(*) FROM image_library").fetchone() if False else None
    print(f"\nRun 'python ingest_library.py --folder <path>' to add more images.")
    print("Run 'python run_niche.py mythology --use-library' to generate video from library.")


if __name__ == "__main__":
    main()
