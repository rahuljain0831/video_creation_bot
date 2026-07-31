"""
CLI tool to inspect the image library and deity coverage.

Usage:
    python list_library.py                    # all indexed deities + image count
    python list_library.py --missing          # deities in deity_prompts.json with no images
    python list_library.py --deity Shiva      # all images for a specific deity
    python list_library.py --report           # full coverage report
"""

import argparse
import sqlite3
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect deity image library coverage.")
    parser.add_argument("--missing", action="store_true",
                        help="Show deities from deity_prompts.json that have no library images")
    parser.add_argument("--deity", default="",
                        help="Show all images for a specific deity name")
    parser.add_argument("--report", action="store_true",
                        help="Full coverage report: all deities, image counts, prompt variants")
    args = parser.parse_args()

    from config import cfg
    from db.init_db import init_db

    init_db(cfg.paths["db"])
    conn = sqlite3.connect(cfg.paths["db"])
    conn.execute("PRAGMA foreign_keys=ON")

    try:
        if args.report:
            from pipeline.deity_map import deity_coverage_report
            print(deity_coverage_report(conn, cfg))

        elif args.missing:
            _show_missing(conn, cfg)

        elif args.deity:
            _show_deity_images(conn, args.deity)

        else:
            _show_all_deities(conn, cfg)

    finally:
        conn.close()


def _show_all_deities(conn: sqlite3.Connection, cfg) -> None:
    """List all indexed deity names with image counts."""
    from pipeline.image_library import get_all_indexed_deities

    deities = get_all_indexed_deities(conn)

    if not deities:
        print("Library is empty. Run: python ingest_library.py --folder <path>")
        return

    print(f"\nImage Library — {len(deities)} deity/figure(s) indexed\n")
    print(f"  {'Deity':<35} {'Images':>6}")
    print("  " + "-" * 43)

    for deity in deities:
        count = conn.execute(
            "SELECT COUNT(*) FROM image_library WHERE lower(deity_name) = lower(?)",
            (deity,),
        ).fetchone()[0]
        print(f"  {deity:<35} {count:>6}")

    total = conn.execute("SELECT COUNT(*) FROM image_library").fetchone()[0]
    print(f"\n  Total: {total} image(s)")


def _show_deity_images(conn: sqlite3.Connection, deity_name: str) -> None:
    """Show all images for a specific deity."""
    from pipeline.deity_prompts import find_all_variants, load_all_data

    rows = conn.execute(
        """SELECT id, file_path, deity_name, tradition, full_description, tags, ingested_at
           FROM image_library
           WHERE lower(deity_name) LIKE lower(?)
           ORDER BY ingested_at""",
        (f"%{deity_name}%",),
    ).fetchall()

    if not rows:
        print(f"No images found for deity: {deity_name!r}")
        print("Run: python list_library.py  (to see all indexed deities)")
        return

    print(f"\nImages for '{deity_name}' — {len(rows)} found\n")
    for r in rows:
        img_id, file_path, dname, tradition, desc, tags, ingested_at = r
        exists = Path(file_path).exists()
        status = "OK" if exists else "MISSING FILE"
        print(f"  [{img_id}] {dname} ({tradition}) [{status}]")
        print(f"       Path: {file_path}")
        if tags:
            try:
                import json
                tag_list = json.loads(tags)
                print(f"       Tags: {', '.join(tag_list[:8])}")
            except Exception:
                print(f"       Tags: {tags[:80]}")
        if desc:
            print(f"       Desc: {desc[:100]}")
        print()


def _show_missing(conn: sqlite3.Connection, cfg) -> None:
    """Show deities in deity_prompts.json that have no library images."""
    from pipeline.deity_prompts import load_all_data, _norm_base
    from pipeline.image_library import get_all_indexed_deities

    all_data = load_all_data(cfg)
    if not all_data:
        print("deity_prompts.json not found or empty.")
        return

    # Get all base names from deity_prompts.json
    base_groups: dict[str, list[dict]] = {}
    for entry in all_data:
        base = _norm_base(entry.get("deity_name", ""))
        if base:
            base_groups.setdefault(base, []).append(entry)

    # Get indexed deities
    indexed = {d.lower() for d in get_all_indexed_deities(conn)}
    indexed_bases = {_norm_base(d) for d in indexed}

    missing = []
    for base, entries in sorted(base_groups.items()):
        if base not in indexed_bases and base not in indexed:
            aliases = entries[0].get("aliases", [])
            alias_str = f" (also: {', '.join(str(a) for a in aliases[:3])})" if aliases else ""
            missing.append(f"  {base.title()}{alias_str}  [{len(entries)} prompt variant(s)]")

    if not missing:
        print("All deities in deity_prompts.json have library images.")
        return

    print(f"\nDeities in deity_prompts.json with NO library images ({len(missing)} of {len(base_groups)}):\n")
    for line in missing:
        print(line)
    print(f"\nTotal prompt entries without images: {len(missing)}")
    print("Add images with: python ingest_library.py --folder <path>")


if __name__ == "__main__":
    main()
