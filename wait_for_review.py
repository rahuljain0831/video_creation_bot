"""
wait_for_review.py — Poll DB for video review decision.

Usage:
    python wait_for_review.py <video_id>

Polls every 5 seconds. Exits when status reaches 'approved' or 'rejected',
after 30-minute timeout, or on Ctrl+C.
"""
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

POLL_INTERVAL = 5       # seconds between DB checks
TIMEOUT_SECONDS = 1800  # 30 minutes

TERMINAL_STATUSES = {"approved", "rejected"}


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def watch(video_id: int, db_path: str) -> None:
    print(f"Watching video_id={video_id} — press Ctrl+C to stop")
    deadline = time.monotonic() + TIMEOUT_SECONDS
    last_status = None

    while time.monotonic() < deadline:
        row = None
        try:
            conn = sqlite3.connect(db_path)
            try:
                row = conn.execute(
                    "SELECT status FROM videos WHERE id=?", (video_id,)
                ).fetchone()
            finally:
                conn.close()
        except Exception as e:
            print(f"[{_now()}]  DB error: {e}")
            time.sleep(POLL_INTERVAL)
            continue

        if row is None:
            print(f"[{_now()}]  video_id={video_id} not found in DB")
            time.sleep(POLL_INTERVAL)
            continue

        status = row[0]
        print(f"[{_now()}]  status = {status}")

        if status in TERMINAL_STATUSES:
            icon = "[OK]" if status == "approved" else "[REJECTED]"
            print(f"\nDecision reached: {status.upper()} {icon}")
            return

        last_status = status
        time.sleep(POLL_INTERVAL)

    print(f"\nTimeout after {TIMEOUT_SECONDS // 60} minutes. Last status: {last_status}")


def main() -> None:
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        print("Usage: python wait_for_review.py <video_id>")
        sys.exit(1)

    video_id = int(sys.argv[1])

    from config import cfg
    db_path = str(Path(__file__).parent / cfg.paths["db"])
    if not Path(db_path).exists():
        print(f"DB not found: {db_path}")
        sys.exit(1)

    try:
        watch(video_id, db_path)
    except KeyboardInterrupt:
        print("\nStopped by user.")


if __name__ == "__main__":
    main()
