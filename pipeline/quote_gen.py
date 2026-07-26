"""
Phase 1 — Quote generation + embedding dedup.

Flow:
  1. Prompt Ollama to generate N quotes
  2. Embed each with nomic-embed-text
  3. Reject if cosine similarity > threshold against existing corpus
  4. Store accepted quotes in DB
"""
import json
import sqlite3
import logging
import math
from pathlib import Path

import requests

log = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"
DEDUP_THRESHOLD = 0.92

GENERATE_PROMPT = """Generate {n} original motivational quotes. Rules:
- Each quote must be unique and distinct in meaning
- No quotes from famous people — original only
- Short and punchy: 8–20 words each
- No numbering, no bullet points, no explanations
- One quote per line, nothing else

Output {n} quotes now:"""


def _ollama_generate(model: str, prompt: str) -> str:
    r = requests.post(
        f"{OLLAMA_URL}/api/generate",
        json={"model": model, "prompt": prompt, "stream": False, "options": {"num_gpu": 0}},
        timeout=300,
    )
    r.raise_for_status()
    return r.json()["response"].strip()


def _embed(text: str) -> list[float]:
    r = requests.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=120,  # model swap from llama → nomic can take ~60s
    )
    r.raise_for_status()
    return r.json()["embedding"]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def _load_existing_embeddings(conn: sqlite3.Connection) -> list[tuple[int, list[float]]]:
    rows = conn.execute(
        "SELECT id, embedding FROM quotes WHERE embedding IS NOT NULL"
    ).fetchall()
    result = []
    for row_id, emb_json in rows:
        if emb_json:
            result.append((row_id, json.loads(emb_json)))
    return result


def _is_duplicate(
    embedding: list[float],
    corpus: list[tuple[int, list[float]]],
    threshold: float,
) -> bool:
    for _, existing_emb in corpus:
        if _cosine(embedding, existing_emb) > threshold:
            return True
    return False


def _parse_quotes(raw: str) -> list[str]:
    lines = [line.strip().strip('"').strip("'") for line in raw.splitlines()]
    return [l for l in lines if l and len(l.split()) >= 4]


def generate_batch(
    conn: sqlite3.Connection,
    model: str,
    count: int = 10,
) -> dict:
    """Generate `count` quotes with `model`, dedup, store. Returns stats."""
    log.info("Generating %d quotes with %s", count, model)

    raw = _ollama_generate(model, GENERATE_PROMPT.format(n=count))
    candidates = _parse_quotes(raw)
    log.info("Parsed %d candidates from model output", len(candidates))

    corpus = _load_existing_embeddings(conn)

    accepted = rejected_dup = rejected_db = 0

    for quote in candidates:
        # Check DB for exact text match first (cheap)
        exists = conn.execute(
            "SELECT 1 FROM quotes WHERE quote_text = ?", (quote,)
        ).fetchone()
        if exists:
            rejected_db += 1
            log.debug("Exact duplicate skipped: %s", quote[:60])
            continue

        embedding = _embed(quote)

        if _is_duplicate(embedding, corpus, DEDUP_THRESHOLD):
            rejected_dup += 1
            log.debug("Semantic duplicate skipped: %s", quote[:60])
            continue

        conn.execute(
            """INSERT INTO quotes (quote_text, source, model_used, embedding)
               VALUES (?, 'ai', ?, ?)""",
            (quote, model, json.dumps(embedding)),
        )
        conn.commit()

        # Add to in-memory corpus so batch deduplicates against itself
        new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        corpus.append((new_id, embedding))
        accepted += 1
        log.info("Stored: %s", quote[:80])

    stats = {
        "model": model,
        "candidates": len(candidates),
        "accepted": accepted,
        "rejected_semantic": rejected_dup,
        "rejected_exact": rejected_db,
    }
    log.info("Batch done: %s", stats)
    return stats


def run_phase1(db_path: str, target: int = 50) -> None:
    """Generate target quotes split across both models."""
    conn = sqlite3.connect(db_path)

    current = conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
    needed = max(0, target - current)
    if needed == 0:
        log.info("Already have %d quotes — target met.", current)
        return

    log.info("Need %d more quotes (have %d, target %d)", needed, current, target)

    model = "llama3.1:8b"
    batch_size = 5  # small batches — CPU inference, 5 quotes ~75s

    print(f"\n=== Phase 1: Quote Generation ===")
    print(f"Target: {target} | Have: {current} | Generating: {needed} | Model: {model}\n")

    total_accepted = 0
    batch_num = 0
    while total_accepted < needed:
        batch_num += 1
        stats = generate_batch(conn, model, count=batch_size)
        total_accepted += stats["accepted"]
        print(f"Batch {batch_num} — accepted {stats['accepted']}, "
              f"dup {stats['rejected_semantic']} | total {current + total_accepted}/{target}")

    total = conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
    print(f"\nDone. Total quotes in DB: {total}/{target}")
    conn.close()


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    db = sys.argv[1] if len(sys.argv) > 1 else "data/db/agent.db"
    target = int(sys.argv[2]) if len(sys.argv) > 2 else 50
    run_phase1(db, target)
