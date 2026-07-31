"""
Batch-generate 10 test videos across pexels-backed niches and write
output/social_captions.json with YouTube + Instagram captions and hashtags.

Usage:
    python scripts/batch_generate.py
    python scripts/batch_generate.py --dry-run      # script gen only, no video
    python scripts/batch_generate.py --no-telegram  # assemble but skip Telegram
    python scripts/batch_generate.py --start 3      # resume from topic index 3

The script is safe to re-run — it appends new entries to social_captions.json
(keyed by video_id) rather than overwriting existing ones.
"""

import argparse
import json
import logging
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("batch_generate")

# ── 10 topics across Pexels-backed niches (no local image library needed) ────
TOPICS = [
    {"niche": "space_science",  "seed": "The mystery of neutron stars and pulsars"},
    {"niche": "space_science",  "seed": "How black holes tear apart entire star systems"},
    {"niche": "space_science",  "seed": "The James Webb telescope's greatest cosmic discoveries"},
    {"niche": "space_science",  "seed": "What happens at the very edge of the observable universe"},
    {"niche": "ai_tech_tools",  "seed": "How AI now writes code faster than any human programmer"},
    {"niche": "ai_tech_tools",  "seed": "The AI model that solved protein folding in minutes"},
    {"niche": "ai_tech_tools",  "seed": "How AI can clone your voice from just 3 seconds of audio"},
    {"niche": "finance_facts",  "seed": "How compound interest silently builds enormous wealth"},
    {"niche": "finance_facts",  "seed": "Why 96 percent of the dollar's purchasing power has vanished"},
    {"niche": "finance_facts",  "seed": "The one money rule Warren Buffett has never broken"},
]

# ── Platform hashtags (research-informed, at platform limits) ─────────────────
# YouTube: 15 max — more than 15 causes ALL hashtags to be ignored.
# Instagram: 30 max — full limit for maximum reach.

HASHTAGS: dict[str, dict[str, list[str]]] = {
    "space_science": {
        "youtube": [
            "#spacefacts", "#universe", "#cosmos", "#science", "#blackhole",
            "#spacescience", "#sciencefacts", "#astronomy", "#nasa", "#space",
            "#shorts", "#didyouknow", "#mindblowing", "#spaceshorts", "#learneveryday",
        ],
        "instagram": [
            "#spacefacts", "#universe", "#cosmos", "#science", "#blackhole",
            "#spacescience", "#sciencefacts", "#astronomy", "#nasa", "#space",
            "#astrophysics", "#galaxy", "#stars", "#solarsystem", "#milkyway",
            "#cosmology", "#spaceexploration", "#sciencelovers", "#sciencenerd",
            "#cosmicwonders", "#universefacts", "#deepspace", "#mindblown",
            "#spacephotography", "#nebula", "#learneveryday", "#sciencereels",
            "#astronomylovers", "#scienceisawesome", "#spaceuniverse",
        ],
    },
    "ai_tech_tools": {
        "youtube": [
            "#ai", "#artificialintelligence", "#aitools", "#chatgpt", "#tech",
            "#technology", "#futuretech", "#aivshumans", "#aishorts", "#shorts",
            "#aitrends", "#machinelearning", "#aifacts", "#bestaitools", "#airevolution",
        ],
        "instagram": [
            "#ai", "#artificialintelligence", "#aitools", "#chatgpt", "#tech",
            "#technology", "#futuretech", "#machinelearning", "#deeplearning",
            "#aifacts", "#airevolution", "#generativeai", "#techtools",
            "#aiproductivity", "#automation", "#aiart", "#coding", "#programming",
            "#techstartup", "#innovation", "#digitaltransformation", "#airesearch",
            "#techlovers", "#futurism", "#ainews", "#aicontent", "#techreels",
            "#techworld", "#aiworld", "#futureofwork",
        ],
    },
    "finance_facts": {
        "youtube": [
            "#finance", "#money", "#personalfinance", "#investing", "#financefacts",
            "#wealth", "#stockmarket", "#financetips", "#wealthbuilding", "#shorts",
            "#moneyfacts", "#financeshorts", "#millionairemindset",
            "#financialindependence", "#richvspoor",
        ],
        "instagram": [
            "#finance", "#money", "#personalfinance", "#investing", "#financefacts",
            "#wealth", "#stockmarket", "#financetips", "#wealthbuilding",
            "#millionairemindset", "#financialfreedom", "#moneymanagement",
            "#moneymoves", "#buildwealth", "#financialliteracy", "#passiveincome",
            "#richlife", "#wealthmindset", "#budgeting", "#savemoney",
            "#moneygoals", "#investorsofinstagram", "#moneycoach", "#financemotivation",
            "#richvspoor", "#moneymindset", "#financereels", "#investingtips",
            "#compoundinterest", "#moneyhabits",
        ],
    },
}

CAPTIONS_PATH = ROOT / "output" / "social_captions.json"


def _load_captions() -> list[dict]:
    if CAPTIONS_PATH.exists():
        try:
            return json.loads(CAPTIONS_PATH.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_captions(entries: list[dict]) -> None:
    CAPTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CAPTIONS_PATH.write_text(
        json.dumps(entries, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _latest_script_file(before: set[Path]) -> Path | None:
    scripts_dir = ROOT / "output" / "scripts"
    if not scripts_dir.exists():
        return None
    after = set(scripts_dir.glob("*.json"))
    new = after - before
    if not new:
        return None
    return max(new, key=lambda p: p.stat().st_mtime)


def _generate_captions_llm(title: str, niche_id: str, narration_summary: str) -> dict:
    """Use LLM to generate YouTube title/description and Instagram caption."""
    try:
        from llm_router import call_llm
        from config import cfg

        niche_labels = {
            "space_science": "Space & Science",
            "ai_tech_tools": "AI & Tech",
            "finance_facts": "Finance Facts",
        }
        niche_label = niche_labels.get(niche_id, niche_id)

        system = (
            "You are a social media content writer for short-form video. "
            "Always respond with valid JSON only — no markdown, no extra text."
        )
        prompt = f"""Write social media captions for a {niche_label} short video.

Video title: {title}
Content summary: {narration_summary[:400]}

Respond with exactly this JSON:
{{
  "youtube_title": "<punchy title under 60 chars, adds curiosity or shock>",
  "youtube_description": "<2-3 sentences, hooks viewer in first line, explains what they'll learn>",
  "instagram_caption": "<1-2 sentences with strong hook + 'Follow for more [topic] facts!' CTA>"
}}"""

        raw, _ = call_llm(prompt, system=system, cfg_router=cfg.llm_router, temperature=0.75)

        # Strip markdown fences if present
        import re
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        raw = re.sub(r"\s*```$", "", raw.strip())
        return json.loads(raw)

    except Exception as e:
        log.warning("LLM caption generation failed: %s — using defaults", e)
        return {
            "youtube_title": title,
            "youtube_description": f"Discover the story of {title}. New short every day.",
            "instagram_caption": f"{title} — follow for more facts!",
        }


def run_topic(topic: dict, flags: list[str]) -> dict | None:
    """Run one video and return its caption entry, or None on failure."""
    niche = topic["niche"]
    seed  = topic["seed"]

    scripts_dir = ROOT / "output" / "scripts"
    before = set(scripts_dir.glob("*.json")) if scripts_dir.exists() else set()

    cmd = [
        sys.executable,
        str(ROOT / "run_niche.py"),
        niche,
        seed,
    ] + flags

    log.info("Running: %s", " ".join(cmd[2:]))
    t0 = time.time()

    result = subprocess.run(
        cmd,
        capture_output=False,     # let stdout/stderr stream to console
        cwd=str(ROOT),
    )

    elapsed = time.time() - t0
    log.info("Finished in %.1fs — returncode=%d", elapsed, result.returncode)

    if result.returncode != 0:
        log.error("Pipeline failed for niche=%s seed=%r", niche, seed)
        return None

    # Find the newly created script JSON
    script_file = _latest_script_file(before)
    if not script_file:
        log.warning("No new script file found — skipping captions for this run")
        return None

    try:
        payload = json.loads(script_file.read_text(encoding="utf-8", errors="replace"))
    except Exception as e:
        log.warning("Could not parse script file %s: %s", script_file, e)
        return None

    video_id    = payload.get("video_id")
    script      = payload.get("script", {})
    story_title = script.get("story_title", seed)
    scenes      = script.get("scenes", [])
    narration_summary = " ".join(s.get("narration", "") for s in scenes)

    log.info("Generating captions for video_id=%s title=%r", video_id, story_title)
    llm_caps = _generate_captions_llm(story_title, niche, narration_summary)

    tags = HASHTAGS.get(niche, {"youtube": [], "instagram": []})

    return {
        "video_id":    video_id,
        "run_slug":    script_file.stem,
        "niche":       niche,
        "story_title": story_title,
        "seed":        seed,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "youtube": {
            "title":       llm_caps.get("youtube_title", story_title),
            "description": llm_caps.get("youtube_description", ""),
            "hashtags":    tags["youtube"],
            "hashtag_count": len(tags["youtube"]),
        },
        "instagram": {
            "caption":     llm_caps.get("instagram_caption", ""),
            "hashtags":    tags["instagram"],
            "hashtag_count": len(tags["instagram"]),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch-generate 10 test videos.")
    parser.add_argument("--dry-run",     action="store_true", help="Script only, no video")
    parser.add_argument("--no-telegram", action="store_true", help="Skip Telegram send")
    parser.add_argument("--start",       type=int, default=0, help="Start from topic index (0-based)")
    args = parser.parse_args()

    flags = []
    if args.dry_run:
        flags.append("--dry-run")
    if args.no_telegram:
        flags.append("--no-telegram")

    topics = TOPICS[args.start:]
    log.info("Starting batch: %d topics (start=%d)", len(topics), args.start)

    existing = _load_captions()
    existing_ids = {e.get("video_id") for e in existing}

    results = list(existing)
    failed  = []

    for i, topic in enumerate(topics, start=args.start + 1):
        log.info("=" * 60)
        log.info("Topic %d/10: [%s] %s", i, topic["niche"], topic["seed"])
        log.info("=" * 60)

        entry = run_topic(topic, flags)
        if entry:
            if entry["video_id"] not in existing_ids:
                results.append(entry)
                existing_ids.add(entry["video_id"])
            _save_captions(results)
            log.info("Captions saved → %s", CAPTIONS_PATH)
        else:
            failed.append({"index": i, "topic": topic})

        # Brief pause between runs to avoid LLM quota bursts
        if i < len(TOPICS):
            time.sleep(3)

    log.info("=" * 60)
    log.info("Batch complete: %d/%d succeeded", len(topics) - len(failed), len(topics))
    if failed:
        log.warning("Failed topics: %s", failed)
    log.info("Captions file: %s (%d entries total)", CAPTIONS_PATH, len(results))


if __name__ == "__main__":
    main()
