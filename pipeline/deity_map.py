"""
Deity Map — central registry bridging deity_prompts.json and image_library DB.

The key entry point for the pipeline is find_best_image_for_scene():
    1. Detect deity name in scene prompt via deity_prompts.find_deity()
    2. If found: exact DB match on deity_name column
    3. Rank multiple matches by tag overlap with scene prompt
    4. Fallback: FTS5 full-text search on scene prompt
    5. Fallback: tradition filter → random image
    6. Fallback: any image in library

Usage:
    from pipeline.deity_map import find_best_image_for_scene, deity_coverage_report

    row = find_best_image_for_scene(scene_prompt, niche, conn, cfg)
    # row is a dict {id, file_path, deity_name, tradition, full_description, tags}
    # or None if library is empty
"""

import json
import logging
import re
import sqlite3

log = logging.getLogger(__name__)


def build_deity_map(conn: sqlite3.Connection, cfg) -> dict:
    """
    Build mapping: deity_base_name → {
        "prompts": [list of deity_prompts.json entries],
        "library_images": [list of image_library rows],
        "tradition": str,
        "aliases": list[str],
        "avatar_count": int,
    }

    Joins deity_prompts.json data with what is actually in the DB.
    Useful for coverage analysis and gap detection.
    """
    from pipeline.deity_prompts import load_all_data, _norm_base, find_all_variants

    all_data = load_all_data(cfg)

    # Group prompt entries by base deity name
    base_groups: dict[str, list[dict]] = {}
    for entry in all_data:
        base = _norm_base(entry.get("deity_name", ""))
        if base:
            base_groups.setdefault(base, []).append(entry)

    # Fetch all library images
    rows = conn.execute(
        "SELECT id, file_path, deity_name, tradition, full_description, tags FROM image_library"
    ).fetchall()
    lib_rows = [
        {"id": r[0], "file_path": r[1], "deity_name": r[2],
         "tradition": r[3], "full_description": r[4], "tags": r[5]}
        for r in rows
    ]

    result: dict[str, dict] = {}

    for base, entries in base_groups.items():
        first = entries[0]
        aliases = []
        for e in entries:
            aliases.extend(e.get("aliases", []))
        aliases = list(dict.fromkeys(a.lower() for a in aliases))  # dedup

        # Find matching library images (check base name and aliases)
        search_names = {base} | {_norm_base(a) for a in aliases} | {base}
        matching_images = []
        for lib_row in lib_rows:
            lib_base = _norm_base(lib_row.get("deity_name", ""))
            if lib_base in search_names or any(
                re.search(r"\b" + re.escape(name) + r"\b", lib_base, re.IGNORECASE)
                for name in search_names if name
            ):
                matching_images.append(lib_row)

        result[base] = {
            "prompts": entries,
            "library_images": matching_images,
            "tradition": first.get("tradition", "hindu"),
            "aliases": aliases,
            "avatar_count": len(entries),
        }

    return result


def get_available_deities(conn: sqlite3.Connection, cfg) -> list[str]:
    """Return sorted list of deity base names that have BOTH a prompt entry AND library images."""
    deity_map = build_deity_map(conn, cfg)
    return sorted(
        name for name, data in deity_map.items()
        if data["prompts"] and data["library_images"]
    )


def _score_row_by_tags(row: dict, scene_prompt: str) -> int:
    """Score a library image row by how many of its tags appear in the scene prompt."""
    tags_raw = row.get("tags", "")
    if isinstance(tags_raw, str):
        try:
            tags = json.loads(tags_raw)
        except (json.JSONDecodeError, ValueError):
            tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
    else:
        tags = tags_raw or []

    prompt_lower = scene_prompt.lower()
    return sum(1 for tag in tags if tag.lower() in prompt_lower)


def find_best_image_for_scene(
    scene_prompt: str,
    niche: dict,
    conn: sqlite3.Connection,
    cfg=None,
    exclude_ids: set[int] | None = None,
) -> dict | None:
    """
    Given a scene's image_prompt, find the best library image.

    Strategy:
    1. Detect deity name in prompt via deity_prompts.find_deity()
    2. If found: query image_library by deity_name column (exact name + variants)
    3. Rank multiple matches by tag overlap with scene prompt (skip exclude_ids)
    4. Fallback: FTS search on full prompt
    5. Fallback: tradition filter → random
    6. Fallback: any image

    exclude_ids: set of image_library.id values already used this video — skipped.
    Returns image_library row dict or None if library is empty.
    """
    from pipeline.deity_prompts import get_lookup, find_deity, find_all_variants, load_all_data, _norm_base
    from pipeline.image_library import search_by_deity_name, search_library

    # Step 1: Detect deity
    lookup = get_lookup(cfg)
    deity_entry = find_deity(scene_prompt, lookup)

    if deity_entry:
        deity_name = deity_entry.get("deity_name", "")
        log.info("deity_map: detected deity=%r in prompt", deity_name)

        # Step 2: Find all name variants (including aliases and avatars)
        all_data = load_all_data(cfg)
        variants = find_all_variants(deity_name, all_data)
        search_names = {deity_name}
        for v in variants:
            search_names.add(v.get("deity_name", ""))
            for alias in v.get("aliases", []):
                search_names.add(str(alias))

        # Query by each name, collect unique rows (excluding already-used)
        seen_ids: set[int] = set(exclude_ids or [])
        candidates: list[dict] = []
        for name in search_names:
            if not name:
                continue
            rows = search_by_deity_name(name, conn, limit=20)
            for row in rows:
                if row["id"] not in seen_ids:
                    seen_ids.add(row["id"])
                    candidates.append(row)

        if candidates:
            # Step 3: Rank by tag overlap
            candidates.sort(key=lambda r: _score_row_by_tags(r, scene_prompt), reverse=True)
            best = candidates[0]
            log.info(
                "deity_map: %d candidate(s) for %r, best=%r (score=%d)",
                len(candidates), deity_name, best["deity_name"],
                _score_row_by_tags(best, scene_prompt),
            )
            return best
        else:
            log.info("deity_map: no library images for deity=%r, falling back to FTS", deity_name)

    # Step 4: FTS fallback
    fts_results = search_library(scene_prompt, niche, conn, limit=5, exclude_ids=exclude_ids)
    if fts_results:
        log.info("deity_map: FTS fallback matched %d image(s)", len(fts_results))
        return fts_results[0]

    log.info("deity_map: no match found, returning None (library may be empty)")
    return None


def deity_coverage_report(conn: sqlite3.Connection, cfg) -> str:
    """
    Generate a text report showing which deities have library images and which are missing.

    Shows: deity_name | images_in_library | prompt_variants_available
    """
    deity_map = build_deity_map(conn, cfg)

    if not deity_map:
        return "No deity data found. Check deity_prompts.json path in settings.json."

    lines = [
        f"{'Deity':<30} {'Library Images':>14} {'Prompt Variants':>16}",
        "-" * 64,
    ]

    covered = []
    missing = []

    for base_name, data in sorted(deity_map.items()):
        img_count = len(data["library_images"])
        prompt_count = data["avatar_count"]
        display = base_name.title()
        row = f"{display:<30} {img_count:>14} {prompt_count:>16}"
        if img_count > 0:
            covered.append(row)
        else:
            missing.append(row)

    lines.append(f"\n[COVERED: {len(covered)} deities have library images]")
    lines.extend(covered)
    lines.append(f"\n[MISSING: {len(missing)} deities have no library images]")
    lines.extend(missing)

    lines.append("\n" + "-" * 64)
    lines.append(
        f"Total: {len(covered)}/{len(deity_map)} deities covered  |  "
        f"{sum(len(d['library_images']) for d in deity_map.values())} total library images"
    )

    return "\n".join(lines)
