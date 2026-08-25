"""Batch-generate videos across the ffmpeg niches (everything except scary_stories).

    python scripts/batch_mixed.py                 # 12 videos, default topic mix
    python scripts/batch_mixed.py --count 4       # first 4 topics only
    python scripts/batch_mixed.py --start 5       # resume from topic index 5
    python scripts/batch_mixed.py --list          # print the topic mix and exit

Success is decided from the DB, not the exit code. run_niche.py returns 0 when
image sourcing fails (it marks the video `rejected` and returns — see
run_niche.py `_fetch_scene_images` returning None), so a returncode check counts
failures as wins. Here every run is confirmed against `videos.status`.

Prints the video_id list of everything that succeeded — feed that straight to
scripts/schedule_all_platforms.py.
"""

import argparse
import json
import logging
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from config import cfg

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("batch_mixed")

# 12 topics across the 5 ffmpeg niches. scary_stories is deliberately absent —
# it renders through Remotion, which is still work in progress.
TOPICS: list[tuple[str, str]] = [
    ("space_science",  "Why neutron stars spin faster than a kitchen blender"),
    ("ai_tech_tools",  "The AI model that folded every known protein in a year"),
    ("finance_facts",  "How compound interest quietly turns small savings into fortunes"),
    ("heists",         "The Antwerp diamond heist that beat ten layers of security"),
    ("mythology",      "The churning of the ocean of milk and the poison Shiva drank"),
    ("space_science",  "What the James Webb telescope found at the edge of time"),
    ("ai_tech_tools",  "How a voice can be cloned from three seconds of audio"),
    ("finance_facts",  "Why the dollar lost 96 percent of its purchasing power"),
    ("heists",         "The Lufthansa robbery that vanished into thin air"),
    ("mythology",      "Hanuman leaping across the ocean to find Sita"),
    ("space_science",  "The Great Attractor pulling our galaxy across the void"),
    ("ai_tech_tools",  "The day an AI wrote production code faster than its team"),
]


def _query(conn, sql, params=(), attempts=5):
    """
    Run a read against the DB, riding out transient sqlite errors.

    A pipeline run and this batch script both touch agent.db, and a concurrent
    writer can surface as `database is locked` or even `disk I/O error` for a
    moment. Losing eight finished videos to a blip in a status check is not a
    trade worth making.
    """
    for attempt in range(1, attempts + 1):
        try:
            return conn.execute(sql, params).fetchone()
        except sqlite3.Error as e:
            if attempt == attempts:
                raise
            log.warning("DB read failed (attempt %d/%d): %s", attempt, attempts, e)
            time.sleep(2 * attempt)


def _latest_video_row(conn) -> tuple[int, str, str] | None:
    row = _query(conn, "SELECT id, niche_id, status FROM videos ORDER BY id DESC LIMIT 1")
    return row if row else None


def run_one(niche: str, seed: str, timeout: int) -> tuple[bool, str]:
    """Run the pipeline once. Returns (process_ok, message) — not final success."""
    cmd = [sys.executable, "run_niche.py", niche, seed, "--no-telegram"]
    try:
        proc = subprocess.run(
            cmd, cwd=str(_REPO), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout}s"

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return False, "; ".join(tail[-3:]) if tail else f"exit {proc.returncode}"
    return True, "ok"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--count", type=int, default=len(TOPICS),
                    help=f"how many topics to run (default {len(TOPICS)})")
    ap.add_argument("--start", type=int, default=0, help="resume from this topic index")
    ap.add_argument("--timeout", type=int, default=1800,
                    help="per-video wall clock limit in seconds (default 1800)")
    ap.add_argument("--stop-after-failures", type=int, default=4,
                    help="give up once this many consecutive runs fail (default 4)")
    ap.add_argument("--list", action="store_true", help="print the topic mix and exit")
    args = ap.parse_args()

    topics = TOPICS[args.start:args.start + args.count]

    if args.list:
        mix: dict[str, int] = {}
        for niche, seed in topics:
            mix[niche] = mix.get(niche, 0) + 1
        for i, (niche, seed) in enumerate(topics, start=args.start):
            print(f"  [{i:2d}] {niche:14s} {seed}")
        print("\n  mix: " + ", ".join(f"{k} {v}" for k, v in sorted(mix.items())))
        return

    conn = sqlite3.connect(cfg.paths["db"])

    made: list[dict] = []
    failures: list[str] = []
    consecutive = 0
    started = time.time()

    log.info("Batch start: %d videos", len(topics))

    for i, (niche, seed) in enumerate(topics, start=1):
        before = _latest_video_row(conn)
        before_id = before[0] if before else 0

        log.info("[%d/%d] %s — %s", i, len(topics), niche, seed)
        t0 = time.time()
        proc_ok, msg = run_one(niche, seed, args.timeout)
        elapsed = time.time() - t0

        # Confirm against the DB regardless of exit code.
        after = _latest_video_row(conn)
        if after is None or after[0] == before_id:
            consecutive += 1
            reason = msg if not proc_ok else "no new videos row"
            failures.append(f"{niche}: {seed} — {reason}")
            log.error("[%d/%d] FAILED in %.0fs — %s", i, len(topics), elapsed, reason)
            if consecutive >= args.stop_after_failures:
                log.error("Stopping: %d consecutive failures", consecutive)
                break
            continue

        video_id, video_niche, status = after
        if status != "assembled":
            consecutive += 1
            reason = f"video_id={video_id} ended at status={status!r}"
            if not proc_ok:
                reason += f" ({msg})"
            failures.append(f"{niche}: {seed} — {reason}")
            log.error("[%d/%d] FAILED in %.0fs — %s", i, len(topics), elapsed, reason)
            if consecutive >= args.stop_after_failures:
                log.error("Stopping: %d consecutive failures", consecutive)
                break
            continue

        consecutive = 0
        path_row = _query(conn, "SELECT file_path FROM videos WHERE id=?", (video_id,))
        file_path = path_row[0] if path_row else ""
        made.append({"video_id": video_id, "niche": video_niche,
                     "seed": seed, "file_path": file_path})
        log.info("[%d/%d] OK in %.0fs — video_id=%d %s",
                 i, len(topics), elapsed, video_id, Path(file_path).name)

    conn.close()

    mins = (time.time() - started) / 60
    ids = [m["video_id"] for m in made]

    log.info("=" * 64)
    log.info("Batch done: %d made, %d failed, %.0f min", len(made), len(failures), mins)
    for m in made:
        log.info("  %-6s %-14s %s", m["video_id"], m["niche"], Path(m["file_path"]).name)
    for f in failures:
        log.info("  FAILED %s", f)

    report = _REPO / "output" / "batch_mixed_report.json"
    report.write_text(
        json.dumps({
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "minutes": round(mins, 1),
            "video_ids": ids,
            "made": made,
            "failed": failures,
        }, indent=2),
        encoding="utf-8",
    )
    log.info("Report: %s", report)

    if ids:
        print("\nSchedule these with:")
        print(f"  python scripts/schedule_all_platforms.py {' '.join(str(i) for i in ids)} \\")
        print('      --start "<YYYY-MM-DD HH:MM>" --per-day 3 --anchor-first')

    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
