"""Generate a batch of videos, each pinned to exactly one image provider.

For each (niche, seed, provider) entry: image_keys.json is temporarily
restricted to just that provider (no fallback chain), so a quota exhaustion
fails cleanly instead of silently completing on a different provider. The
failed video is left at whatever status run_niche.py left it at (not
'assembled') — documented in the report and never scheduled; nothing is
deleted, "discard" here means "don't use it," not "remove the file."

Usage: python scripts/run_provider_batch.py
"""
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
log = logging.getLogger("run_provider_batch")

_KEYS_FILE = _REPO / "image_keys.json"

# 12 cloudflare + 1 gemini + 1 fastrouter. Topics mirror batch_mixed.py's mix.
RUNS: list[tuple[str, str, str]] = [
    ("space_science",  "Why neutron stars spin faster than a kitchen blender", "cloudflare"),
    ("ai_tech_tools",  "The AI model that folded every known protein in a year", "cloudflare"),
    ("finance_facts",  "How compound interest quietly turns small savings into fortunes", "cloudflare"),
    ("heists",         "The Antwerp diamond heist that beat ten layers of security", "cloudflare"),
    ("space_science",  "What the James Webb telescope found at the edge of time", "cloudflare"),
    ("ai_tech_tools",  "How a voice can be cloned from three seconds of audio", "cloudflare"),
    ("finance_facts",  "Why the dollar lost 96 percent of its purchasing power", "cloudflare"),
    ("heists",         "The Lufthansa robbery that vanished into thin air", "cloudflare"),
    ("space_science",  "The Great Attractor pulling our galaxy across the void", "cloudflare"),
    ("ai_tech_tools",  "The day an AI wrote production code faster than its team", "cloudflare"),
    ("space_science",  "Why time itself slows down near a black hole's edge", "cloudflare"),
    ("space_science",  "The rogue planet wandering the galaxy with no star to orbit", "cloudflare"),
    ("ai_tech_tools",  "How a language model taught itself to play chess from scratch", "gemini"),
    ("ai_tech_tools",  "The robot hand that learned to tie a shoelace by trial and error", "fastrouter"),
]


def _query(conn, sql, params=(), attempts=5):
    for attempt in range(1, attempts + 1):
        try:
            return conn.execute(sql, params).fetchone()
        except sqlite3.Error as e:
            if attempt == attempts:
                raise
            log.warning("DB read failed (attempt %d/%d): %s", attempt, attempts, e)
            time.sleep(2 * attempt)


def _latest_video_row(conn):
    row = _query(conn, "SELECT id, niche_id, status FROM videos ORDER BY id DESC LIMIT 1")
    return row if row else None


def _pin_provider(provider: str, full_config: dict) -> None:
    """Restrict image_keys.json to exactly one provider — no fallback chain."""
    pinned = {"_comment": full_config["_comment"], provider: full_config[provider]}
    _KEYS_FILE.write_text(json.dumps(pinned, indent=2), encoding="utf-8")


def run_one(niche: str, seed: str, timeout: int = 1800) -> tuple[bool, str]:
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
    full_config = json.loads(_KEYS_FILE.read_text(encoding="utf-8"))
    conn = sqlite3.connect(cfg.paths["db"])

    made: list[dict] = []
    failures: list[str] = []
    started = time.time()

    log.info("Batch start: %d videos", len(RUNS))

    try:
        for i, (niche, seed, provider) in enumerate(RUNS, start=1):
            _pin_provider(provider, full_config)

            before = _latest_video_row(conn)
            before_id = before[0] if before else 0

            log.info("[%d/%d] provider=%s %s — %s", i, len(RUNS), provider, niche, seed)
            t0 = time.time()
            proc_ok, msg = run_one(niche, seed)
            elapsed = time.time() - t0

            after = _latest_video_row(conn)
            if after is None or after[0] == before_id:
                reason = msg if not proc_ok else "no new videos row"
                failures.append(f"provider={provider} {niche}: {seed} — {reason}")
                log.error("[%d/%d] FAILED in %.0fs — %s", i, len(RUNS), elapsed, reason)
                continue

            video_id, video_niche, status = after
            if status != "assembled":
                reason = f"video_id={video_id} ended at status={status!r}"
                if not proc_ok:
                    reason += f" ({msg})"
                failures.append(f"provider={provider} {niche}: {seed} — {reason}")
                log.error("[%d/%d] FAILED in %.0fs — %s", i, len(RUNS), elapsed, reason)
                continue

            path_row = _query(conn, "SELECT file_path FROM videos WHERE id=?", (video_id,))
            file_path = path_row[0] if path_row else ""
            made.append({"video_id": video_id, "niche": video_niche, "provider": provider,
                        "seed": seed, "file_path": file_path})
            log.info("[%d/%d] OK in %.0fs — video_id=%d %s",
                     i, len(RUNS), elapsed, video_id, Path(file_path).name)
    finally:
        # Always restore the full provider chain, even on crash/interrupt.
        _KEYS_FILE.write_text(json.dumps(full_config, indent=2), encoding="utf-8")
        conn.close()

    mins = (time.time() - started) / 60
    log.info("=" * 64)
    log.info("Batch done: %d made, %d failed, %.0f min", len(made), len(failures), mins)
    for m in made:
        log.info("  %-6s %-10s %-14s %s", m["video_id"], m["provider"], m["niche"], Path(m["file_path"]).name)
    for f in failures:
        log.info("  FAILED %s", f)

    report = _REPO / "output" / "provider_batch_report.json"
    report.write_text(
        json.dumps({
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "minutes": round(mins, 1),
            "made": made,
            "failed": failures,
        }, indent=2),
        encoding="utf-8",
    )
    log.info("Report: %s", report)


if __name__ == "__main__":
    main()
