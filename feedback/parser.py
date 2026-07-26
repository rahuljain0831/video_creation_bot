"""
Phase 5 — Feedback capture + Ollama tag parsing.

Two entry points:
  1. record_manual(video_id, rating, text)  — called from Telegram bot reply text
  2. parse_tags(feedback_text)              — uses Ollama to extract structured tags
  3. backfill_tags()                        — batch-parse existing feedback with no tags

Tags extracted: tone, mood, pacing, visual, quote_quality, voice_quality
Each tag is "aspect:value" e.g. "tone:too_serious", "visual:too_dark"
"""
import json
import logging
import sqlite3

import requests

log = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434"
TAG_MODEL  = "llama3.1:8b"

_TAG_PROMPT = """You are analyzing feedback on a motivational quote video.

Feedback text: "{text}"
Video rating: {rating}

Extract structured tags from this feedback. Output ONLY a JSON array of strings.
Each tag must follow the format "aspect:value".

Valid aspects and example values:
- tone: too_serious, too_casual, perfect, inspirational, flat
- mood: uplifting, dark, neutral, energetic, calm
- pacing: too_fast, too_slow, perfect
- visual: too_dark, too_bright, cluttered, clean, beautiful
- quote_quality: generic, original, powerful, weak, too_long, too_short
- voice_quality: clear, robotic, too_fast, too_slow, good

Rules:
- Only include aspects that are clearly implied by the feedback text
- If feedback is vague (e.g. just "good" or "bad"), return []
- Maximum 5 tags
- Output valid JSON only, nothing else

Output:"""


def parse_tags(feedback_text: str, rating: str = "good") -> list[str]:
    """
    Use Ollama to extract structured tags from free-text feedback.
    Returns list of "aspect:value" strings.
    Falls back to [] on any error.
    """
    if not feedback_text or not feedback_text.strip():
        return []

    prompt = _TAG_PROMPT.format(text=feedback_text.strip(), rating=rating)
    try:
        r = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": TAG_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"num_gpu": 0, "temperature": 0},
            },
            timeout=120,
        )
        r.raise_for_status()
        raw = r.json()["response"].strip()

        # Extract JSON array even if model adds prose around it
        start = raw.find("[")
        end   = raw.rfind("]") + 1
        if start == -1 or end == 0:
            log.warning("No JSON array in model output: %s", raw[:100])
            return []

        tags = json.loads(raw[start:end])
        # Validate format
        valid = [t for t in tags if isinstance(t, str) and ":" in t]
        log.info("Parsed tags: %s", valid)
        return valid

    except Exception as e:
        log.warning("Tag parsing failed: %s", e)
        return []


def record(
    conn: sqlite3.Connection,
    video_id: int,
    rating: str,
    feedback_text: str = "",
    source: str = "manual",
    parse: bool = True,
) -> int:
    """
    Insert a feedback row. Optionally parse tags via Ollama.
    Returns the new feedback row id.
    """
    tags = parse_tags(feedback_text, rating) if parse and feedback_text else []

    conn.execute(
        """INSERT INTO feedback (video_id, rating, feedback_text, parsed_tags, source)
           VALUES (?, ?, ?, ?, ?)""",
        (video_id, rating, feedback_text or None, json.dumps(tags) if tags else None, source),
    )
    conn.commit()
    row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    log.info("feedback id=%d  video=%d  rating=%s  tags=%s", row_id, video_id, rating, tags)
    return row_id


def backfill_tags(conn: sqlite3.Connection) -> int:
    """
    Parse tags for existing feedback rows that have text but no tags.
    Returns count of rows updated.
    """
    rows = conn.execute(
        """SELECT id, video_id, rating, feedback_text
           FROM feedback
           WHERE feedback_text IS NOT NULL
             AND (parsed_tags IS NULL OR parsed_tags = '[]')"""
    ).fetchall()

    updated = 0
    for row_id, video_id, rating, text in rows:
        tags = parse_tags(text, rating)
        if tags:
            conn.execute(
                "UPDATE feedback SET parsed_tags = ? WHERE id = ?",
                (json.dumps(tags), row_id),
            )
            conn.commit()
            updated += 1
            log.info("Backfilled feedback id=%d: %s", row_id, tags)

    log.info("Backfill complete — updated %d rows", updated)
    return updated


def tag_summary(conn: sqlite3.Connection) -> dict:
    """
    Aggregate tag counts across all feedback.
    Returns {aspect: {value: count}} sorted by count desc.
    """
    rows = conn.execute(
        "SELECT parsed_tags FROM feedback WHERE parsed_tags IS NOT NULL"
    ).fetchall()

    counts: dict[str, dict[str, int]] = {}
    for (tags_json,) in rows:
        try:
            tags = json.loads(tags_json)
        except Exception:
            continue
        for tag in tags:
            if ":" not in tag:
                continue
            aspect, value = tag.split(":", 1)
            counts.setdefault(aspect, {})
            counts[aspect][value] = counts[aspect].get(value, 0) + 1

    # Sort each aspect's values by count
    return {
        aspect: dict(sorted(values.items(), key=lambda x: -x[1]))
        for aspect, values in sorted(counts.items())
    }


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    db_path = sys.argv[1] if len(sys.argv) > 1 else "data/db/agent.db"
    conn = sqlite3.connect(db_path)

    # Quick test
    text = sys.argv[2] if len(sys.argv) > 2 else "The voice sounded a bit robotic and the background was too dark"
    print(f"Input: {text}")
    tags = parse_tags(text, rating="bad")
    print(f"Tags : {tags}")

    summary = tag_summary(conn)
    print(f"DB tag summary: {json.dumps(summary, indent=2)}")
    conn.close()
