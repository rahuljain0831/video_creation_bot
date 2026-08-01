"""
Image Library — user-provided deity images with Gemini Vision analysis.

Ingestion:
    ingest_image_folder(folder, conn, cfg) → analyzes each image, stores in DB.

Retrieval:
    get_library_image(image_prompt, niche, conn, ...) → best-matching image path.
"""

import base64
import hashlib
import json
import logging
import re
import shutil
import sqlite3
from pathlib import Path

import requests

log = logging.getLogger(__name__)

_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "are", "was", "were", "be",
    "been", "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "shall", "can", "this",
    "that", "these", "those", "it", "its", "he", "she", "they", "them",
    "his", "her", "their", "our", "your", "my", "who", "which", "what",
    "his", "into", "through", "over", "under", "above", "below", "around",
}

_ANALYSIS_PROMPT = """Analyze this image of a deity or mythological figure.
Respond with ONLY a valid JSON object — no markdown, no extra text:
{
  "deity_name": "<primary deity or figure name, e.g. Shiva, Odin, Anubis, or 'unknown'>",
  "tradition": "<one of: hindu, greek, norse, egyptian, buddhist, other>",
  "full_description": "<2-3 sentences: figure, pose, setting, mood, visual atmosphere>",
  "tags": ["<tag1>", "<tag2>", ...]
}

Tags must include (as applicable):
- Pose: standing, seated, dancing, reclining, warrior-stance, meditating
- Dominant colors: blue-skin, gold, red, white, black, multi-colored
- Symbols/attributes: trident, lotus, hammer, ankh, thunderbolt, bow, conch, discus, skull, serpent, crescent-moon
- Expression: serene, fierce, compassionate, wrathful, joyful, meditative
- Background: forest, temple, cosmic, battlefield, ocean, mountain, flames, darkness, celestial
- Number of arms if non-standard: four-armed, six-armed, eight-armed, ten-armed
- Other key visual elements unique to this image

Aim for 8-15 tags."""


class LibraryEmptyError(RuntimeError):
    """Raised when no matching image found in library."""


def _extract_json(text: str) -> dict:
    """Extract first JSON object from Gemini response."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    match = re.search(r"(\{.*\})", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    raise ValueError(f"No valid JSON in Gemini response: {text[:300]}")


def analyze_image_with_gemini(
    image_path: Path,
    api_key: str,
    tradition_hint: str = "",
    model: str = "gemini-2.0-flash",
) -> dict:
    """
    Call Gemini Vision to analyze one image.
    Returns dict: {deity_name, tradition, full_description, tags: list[str]}
    """
    import time

    img_bytes = image_path.read_bytes()
    suffix = image_path.suffix.lower()
    mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".png": "image/png", ".webp": "image/webp"}
    mime = mime_map.get(suffix, "image/jpeg")
    img_b64 = base64.b64encode(img_bytes).decode()

    hint_line = (
        f"The user believes this image belongs to the {tradition_hint} tradition.\n"
        if tradition_hint else ""
    )
    prompt = hint_line + _ANALYSIS_PROMPT

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    body = {
        "contents": [{"parts": [
            {"inline_data": {"mime_type": mime, "data": img_b64}},
            {"text": prompt},
        ]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 512},
    }

    for attempt in range(3):
        response = requests.post(url, json=body, timeout=60)
        if response.status_code == 429:
            wait = 10 * (attempt + 1)
            log.warning("Rate limit hit, waiting %ds before retry %d/3...", wait, attempt + 1)
            time.sleep(wait)
            continue
        response.raise_for_status()
        raw_text = response.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        return _extract_json(raw_text)

    response.raise_for_status()  # raise final 429


def _copy_to_library_store(src: Path, store_dir: Path) -> Path:
    """
    Copy/convert image to store_dir/<sha256[:12]>.png.
    Uses Pillow for conversion so any input format works.
    Returns absolute destination Path.
    """
    store_dir.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256(src.read_bytes()).hexdigest()[:12]
    dest = store_dir / f"{sha}.png"
    if dest.exists():
        return dest.resolve()

    try:
        from PIL import Image
        with Image.open(src) as img:
            rgb = img.convert("RGB")
            rgb.save(dest, "PNG")
    except ImportError:
        # Pillow not available — just copy if already PNG, else raise
        if src.suffix.lower() == ".png":
            shutil.copy2(src, dest)
        else:
            raise RuntimeError(
                "Pillow is required for non-PNG image conversion. "
                "Install with: pip install Pillow"
            )
    return dest.resolve()


def ingest_image_folder(
    folder: str,
    conn: sqlite3.Connection,
    cfg,
    tradition_hint: str = "",
    skip_existing: bool = True,
) -> dict:
    """
    Scan folder recursively for .jpg/.jpeg/.png/.webp images.
    Per image: copy to library store → Gemini Vision analysis → INSERT image_library.
    FTS sync handled automatically by DB triggers.

    Returns: {processed, skipped, failed, failed_paths}
    """
    api_key = cfg.GOOGLE_AI_STUDIO_API_KEY
    if not api_key:
        raise RuntimeError("GOOGLE_AI_STUDIO_API_KEY not set — required for vision analysis")

    lib_cfg = getattr(cfg, "image_library", {})
    vision_model = lib_cfg.get("vision_model", "gemini-1.5-flash")
    store_dir_str = lib_cfg.get("store_dir", "output/images/library")

    # Resolve store_dir relative to project root (where cfg lives)
    store_dir = Path(store_dir_str)
    if not store_dir.is_absolute():
        # Attempt to find project root via cfg.paths
        db_path = Path(cfg.paths.get("db", "output/db/agent.db"))
        project_root = db_path.parent.parent  # output/ → project root
        store_dir = project_root / store_dir_str

    source_dir = Path(folder)
    if not source_dir.exists():
        raise FileNotFoundError(f"Image folder not found: {folder}")

    extensions = {".jpg", ".jpeg", ".png", ".webp"}
    image_files = [
        p for p in source_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in extensions
    ]

    log.info("Found %d images in %s", len(image_files), folder)

    processed = skipped = failed = 0
    failed_paths: list[str] = []

    for src in image_files:
        try:
            # Copy/convert to library store
            dest = _copy_to_library_store(src, store_dir)
            dest_str = str(dest)

            # Skip if already in DB
            if skip_existing:
                existing = conn.execute(
                    "SELECT id FROM image_library WHERE file_path=?", (dest_str,)
                ).fetchone()
                if existing:
                    log.debug("Skip existing: %s", dest_str)
                    skipped += 1
                    continue

            # Gemini Vision analysis (small delay to respect free-tier RPM)
            import time
            time.sleep(4)
            log.info("Analyzing: %s", src.name)
            analysis = analyze_image_with_gemini(
                dest, api_key, tradition_hint, vision_model
            )

            deity_name = analysis.get("deity_name", "unknown")
            tradition = analysis.get("tradition", "other")
            full_description = analysis.get("full_description", "")
            tags = analysis.get("tags", [])
            tags_json = json.dumps(tags)

            conn.execute(
                """INSERT OR REPLACE INTO image_library
                   (file_path, original_path, deity_name, tradition, full_description, tags)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (dest_str, str(src.resolve()), deity_name, tradition, full_description, tags_json),
            )
            conn.commit()

            log.info("  → %s | %s | tags: %s", deity_name, tradition, ", ".join(tags[:5]))
            processed += 1

        except Exception as exc:
            log.warning("Failed to process %s: %s", src, exc)
            failed += 1
            failed_paths.append(str(src))

    return {
        "processed": processed,
        "skipped": skipped,
        "failed": failed,
        "failed_paths": failed_paths,
    }


def _extract_fts_keywords(image_prompt: str, min_len: int = 4) -> str:
    """
    Extract search keywords from a scene image_prompt for FTS5 MATCH.
    Strips stopwords and short words, joins remaining with OR.
    """
    words = re.findall(r"[a-zA-Z]+", image_prompt.lower())
    keywords = [
        w for w in words
        if w not in _STOPWORDS and len(w) >= min_len
    ]
    # Deduplicate preserving order
    seen: set[str] = set()
    unique = []
    for w in keywords:
        if w not in seen:
            seen.add(w)
            unique.append(w)
    return " OR ".join(unique) if unique else ""


def search_library(
    image_prompt: str,
    niche: dict,
    conn: sqlite3.Connection,
    limit: int = 5,
    exclude_ids: set[int] | None = None,
) -> list[dict]:
    """
    Find best-matching images for a scene's image_prompt.

    Strategy:
    1. FTS5 MATCH on extracted keywords (ranked by relevance)
    2. Fallback: filter by niche tradition, random order
    3. Fallback: any image in library, random

    exclude_ids: set of image_library.id values to skip (already used this video).

    Returns list of dicts: {id, file_path, deity_name, tradition, full_description}
    """
    fts_query = _extract_fts_keywords(image_prompt)
    ex_ids = list(exclude_ids) if exclude_ids else []
    ex_clause = f"AND il.id NOT IN ({','.join('?' * len(ex_ids))})" if ex_ids else ""

    if fts_query:
        try:
            params: list = [fts_query] + ex_ids + [limit]
            rows = conn.execute(
                f"""SELECT il.id, il.file_path, il.deity_name, il.tradition, il.full_description
                   FROM image_library_fts fts
                   JOIN image_library il ON il.id = fts.rowid
                   WHERE image_library_fts MATCH ?
                   {ex_clause}
                   ORDER BY rank
                   LIMIT ?""",
                params,
            ).fetchall()
            if rows:
                return [
                    {"id": r[0], "file_path": r[1], "deity_name": r[2],
                     "tradition": r[3], "full_description": r[4]}
                    for r in rows
                ]
        except sqlite3.OperationalError as exc:
            log.warning("FTS query failed (%s), falling back to tradition filter", exc)

    # Fallback 1: tradition filter
    tradition_map = {
        "hindu": "hindu", "norse": "norse", "egypt": "egyptian",
        "greek": "greek", "buddhist": "buddhist",
    }
    niche_label = niche.get("label", "").lower()
    matched_tradition = next(
        (v for k, v in tradition_map.items() if k in niche_label), None
    )

    if matched_tradition:
        rows = conn.execute(
            f"""SELECT id, file_path, deity_name, tradition, full_description
               FROM image_library WHERE tradition=? {ex_clause.replace('il.id', 'id')}
               ORDER BY RANDOM() LIMIT ?""",
            [matched_tradition] + ex_ids + [limit],
        ).fetchall()
        if rows:
            return [
                {"id": r[0], "file_path": r[1], "deity_name": r[2],
                 "tradition": r[3], "full_description": r[4]}
                for r in rows
            ]

    # Fallback 2: any image (excluding used)
    ex_where = ex_clause.replace("AND il.id", "WHERE id") if ex_clause else ""
    rows = conn.execute(
        f"""SELECT id, file_path, deity_name, tradition, full_description
           FROM image_library {ex_where} ORDER BY RANDOM() LIMIT ?""",
        ex_ids + [limit],
    ).fetchall()
    return [
        {"id": r[0], "file_path": r[1], "deity_name": r[2],
         "tradition": r[3], "full_description": r[4]}
        for r in rows
    ]


def search_by_deity_name(
    deity_name: str,
    conn: sqlite3.Connection,
    limit: int = 10,
) -> list[dict]:
    """
    Query image_library directly by deity_name column (exact + case-insensitive).
    Faster than FTS for known deity names.
    Returns list of image_library row dicts.
    """
    rows = conn.execute(
        """SELECT id, file_path, deity_name, tradition, full_description, tags
           FROM image_library
           WHERE lower(deity_name) = lower(?)
           ORDER BY RANDOM()
           LIMIT ?""",
        (deity_name, limit),
    ).fetchall()
    return [
        {"id": r[0], "file_path": r[1], "deity_name": r[2],
         "tradition": r[3], "full_description": r[4], "tags": r[5]}
        for r in rows
    ]


def get_all_indexed_deities(conn: sqlite3.Connection) -> list[str]:
    """Return sorted list of unique deity names currently in image_library."""
    rows = conn.execute(
        "SELECT DISTINCT deity_name FROM image_library WHERE deity_name IS NOT NULL ORDER BY deity_name"
    ).fetchall()
    return [r[0] for r in rows if r[0] and r[0].lower() != "unknown"]


def get_library_image(
    image_prompt: str,
    niche: dict,
    conn: sqlite3.Connection,
    output_dir: str,
    scene_index: int,
    video_id: int,
    cfg=None,
    preselected_row: dict | None = None,
    used_image_ids: set[int] | None = None,
) -> str:
    """
    Finds best matching image from library, copies it to output_dir/scene_XX.png.
    Returns that destination path.
    Raises LibraryEmptyError if library has no images at all.

    used_image_ids: mutable set of already-used image_library.id values.
    Images in this set are excluded; chosen image's ID is added to the set.

    If preselected_row is provided (from deity_map.find_best_image_for_scene),
    uses it directly (falling back to search if it's already used).
    """
    # If preselected row is already used this video, ignore it and search fresh
    if preselected_row and used_image_ids and preselected_row.get("id") in used_image_ids:
        preselected_row = None

    if preselected_row:
        candidates = [preselected_row]
    else:
        candidates = search_library(image_prompt, niche, conn, exclude_ids=used_image_ids)

    if not candidates:
        raise LibraryEmptyError(
            "Image library is empty. Run: python ingest_library.py --folder <path>"
        )

    best = candidates[0]
    src = Path(best["file_path"])

    if not src.exists():
        for candidate in candidates[1:]:
            src = Path(candidate["file_path"])
            if src.exists():
                best = candidate
                break
        else:
            raise LibraryEmptyError(
                f"Library has rows but image files are missing. "
                f"Check that {best['file_path']} exists."
            )

    dest_dir = Path(output_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"scene_{scene_index:02d}.png"

    shutil.copy2(src, dest)

    if used_image_ids is not None:
        used_image_ids.add(best["id"])

    log.info(
        "Library match: scene %d → %s (%s, %s)",
        scene_index, best["deity_name"], best["tradition"], src.name,
    )
    return str(dest)
