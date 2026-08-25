"""
Batch-generate scary shorts unattended and collect the finished mp4s in one folder.

Each video runs as its own `run_niche.py` subprocess so a single bad render,
LLM hiccup, or ffmpeg failure cannot take down the batch — the loop logs it and
moves to the next premise.

    python scripts/batch_scary.py --count 30
    python scripts/batch_scary.py --count 30 --out output/final_videos

Finished videos are COPIED (not moved) into --out, so the normal pipeline
artifacts stay where the rest of the tooling expects them.
"""

import argparse
import json
import logging
import random
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("batch_scary")

_REPO = Path(__file__).resolve().parent.parent

# Varied premises so a night's output doesn't read as ten takes on one idea.
# The model still writes the story; this only sets the territory.
PREMISES = [
    "the neighbour who never ages",
    "the last train home has one extra passenger",
    "a baby monitor picking up a room that isn't the nursery",
    "the hotel corridor that has one more door every night",
    "a lighthouse keeper logging a ship that sank forty years ago",
    "the family photo where everyone is smiling except one person",
    "a night shift security guard watching himself on the monitors",
    "the woods behind the house where the birds stopped singing",
    "a voicemail from a number that was disconnected in 1998",
    "the apartment above yours where nobody has lived for years",
    "a diving crew finding a car at the bottom of the reservoir",
    "the scarecrow that is closer to the house every morning",
    "a nurse doing rounds on a ward that was demolished",
    "the mirror in the rented flat that reflects the room a second late",
    "a taxi driver whose passenger gives an address that doesn't exist",
    "the tunnel where phone signal dies and something answers anyway",
    "a house sitter told never to open the third bedroom",
    "the sound of someone brushing their teeth in an empty house",
    "a hiker finding a tent zipped from the outside",
    "the elevator that stops at a floor the building doesn't have",
    "a radio station broadcasting names of people who die that week",
    "the swimming pool that is deeper at night",
    "a locksmith called to a door with no keyhole",
    "the sleep study patient who stops breathing when observed",
    "a farmhouse where the dog refuses to enter the kitchen",
    "the school photograph with a child no teacher remembers",
    "a delivery driver with a parcel addressed to himself",
    "the caravan park closed since the fire, with a light on",
    "a subway busker nobody else can hear",
    "the attic where the previous owner left the ladder pulled up",
    "a fisherman hauling the same net back three times",
    "the answering machine that records the house while you're out",
    "a museum night guard and the exhibit that changes pose",
    "the road that loops back no matter which turn you take",
    "a hospice patient describing the visitor in the corner",
    "the basement freezer that hums a tune",
    "a wedding video with someone standing at the back",
    "the campsite where every tent is pitched facing inward",
    "a train station waiting room that never empties",
    "the cellar door that was bricked up from the inside",
]


def _newest_video(video_dir: Path, before: set[Path]) -> Path | None:
    """The scary mp4 that appeared since `before` was captured."""
    now = {p for p in video_dir.glob("scary_stories_*.mp4")}
    fresh = now - before
    if not fresh:
        return None
    return max(fresh, key=lambda p: p.stat().st_mtime)


def run_one(premise: str, timeout: int) -> tuple[bool, str]:
    """Run the pipeline once. Returns (ok, message)."""
    cmd = [sys.executable, "run_niche.py", "scary_stories", premise, "--no-telegram"]
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
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--count", type=int, default=20, help="how many videos to attempt")
    ap.add_argument("--out", default="output/final_videos",
                    help="folder collecting the finished mp4s (videos only)")
    ap.add_argument("--timeout", type=int, default=2400,
                    help="per-video wall clock limit in seconds")
    ap.add_argument("--stop-after-failures", type=int, default=6,
                    help="give up once this many consecutive runs fail")
    args = ap.parse_args()

    out_dir = _REPO / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    video_dir = _REPO / "output" / "video"
    video_dir.mkdir(parents=True, exist_ok=True)

    premises = PREMISES[:]
    random.shuffle(premises)

    made: list[str] = []
    failures: list[str] = []
    consecutive = 0
    started = time.time()

    log.info("Batch start: target=%d out=%s", args.count, out_dir)

    for i in range(args.count):
        premise = premises[i % len(premises)]
        before = {p for p in video_dir.glob("scary_stories_*.mp4")}

        log.info("[%d/%d] %s", i + 1, args.count, premise)
        t0 = time.time()
        ok, msg = run_one(premise, args.timeout)
        elapsed = time.time() - t0

        if not ok:
            consecutive += 1
            failures.append(f"{premise}: {msg}")
            log.error("[%d/%d] FAILED in %.0fs — %s", i + 1, args.count, elapsed, msg)
            if consecutive >= args.stop_after_failures:
                log.error("Stopping: %d consecutive failures", consecutive)
                break
            continue

        video = _newest_video(video_dir, before)
        if video is None:
            consecutive += 1
            failures.append(f"{premise}: run reported success but produced no mp4")
            log.error("[%d/%d] no output file found", i + 1, args.count)
            continue

        consecutive = 0
        dest = out_dir / video.name
        shutil.copy2(video, dest)
        made.append(dest.name)
        log.info("[%d/%d] OK in %.0fs → %s (%.1f MB)",
                 i + 1, args.count, elapsed, dest.name,
                 dest.stat().st_size / 1e6)

    mins = (time.time() - started) / 60
    log.info("=" * 64)
    log.info("Batch done: %d made, %d failed, %.0f min", len(made), len(failures), mins)
    for name in made:
        log.info("  %s", name)
    for f in failures:
        log.info("  FAILED %s", f)

    # A manifest beside the folder, not inside it — --out holds videos only.
    (out_dir.parent / "batch_report.json").write_text(
        json.dumps(
            {
                "finished_at": datetime.now().isoformat(timespec="seconds"),
                "minutes": round(mins, 1),
                "made": made,
                "failed": failures,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
