"""
Pexels stock library — replaces deity image_library for Bucket-B niches.

Retrieval only: search Pexels by scene image_prompt keywords, download best
match, save to output_dir/scene_XX.jpg. No generation, no VRAM, no distortion
risk, no rate-limit wall (Pexels free tier: 200 req/hour, 20,000/month).
"""

import logging
import os
from pathlib import Path

import requests

log = logging.getLogger(__name__)

_PEXELS_SEARCH_URL = "https://api.pexels.com/v1/search"


class PexelsError(RuntimeError):
    """Raised on Pexels API failure or no results."""


def _api_key() -> str:
    key = os.getenv("PEXELS_API_KEY", "").strip()
    if not key:
        raise PexelsError("PEXELS_API_KEY not set in .env")
    return key


def search_pexels(query: str, orientation: str = "portrait", per_page: int = 5) -> list[dict]:
    """
    Query Pexels photo search. orientation='portrait' biases results toward
    9:16-friendly compositions (still crop-centered downstream, but starts closer).
    Returns list of {id, url, photographer, src: {original, large2x, ...}}.
    """
    headers = {"Authorization": _api_key()}
    params = {"query": query, "orientation": orientation, "per_page": per_page}
    resp = requests.get(_PEXELS_SEARCH_URL, headers=headers, params=params, timeout=20)
    resp.raise_for_status()
    photos = resp.json().get("photos", [])
    if not photos:
        raise PexelsError(f"No Pexels results for query: {query!r}")
    return photos


def get_pexels_image(
    image_prompt: str,
    niche: dict,
    output_dir: str,
    scene_index: int,
    fallback_query: str | None = None,
    used_photo_ids: set[int] | None = None,
) -> str:
    """
    Drop-in replacement for image_library.get_library_image().
    Searches Pexels using image_prompt (+ niche art_style_prompt_suffix as
    a bias term), downloads the best unused match to output_dir/scene_XX.jpg.

    used_photo_ids: mutable set tracking photo IDs already used this video run.
    Raises PexelsError if no results even after fallback_query.
    """
    # Use only the first 80 chars of the image_prompt as the Pexels search term.
    # Art style suffixes are generation directives, not good search keywords,
    # and long URLs cause Pexels 403 errors.
    search_term = image_prompt[:80].rsplit(" ", 1)[0] if len(image_prompt) > 80 else image_prompt

    try:
        photos = search_pexels(search_term, per_page=15)
    except PexelsError:
        if not fallback_query:
            raise
        log.warning("Primary query failed, retrying with fallback: %s", fallback_query)
        photos = search_pexels(fallback_query, per_page=15)

    # Filter out already-used photos; fall back to any if all used
    if used_photo_ids:
        available = [p for p in photos if p["id"] not in used_photo_ids]
        if not available:
            log.warning("All Pexels results already used; repeating a photo for scene %d", scene_index)
            available = photos
    else:
        available = photos

    best = available[0]
    if used_photo_ids is not None:
        used_photo_ids.add(best["id"])
    img_url = best["src"]["large2x"]

    dest_dir = Path(output_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"scene_{scene_index:02d}.jpg"

    img_resp = requests.get(img_url, timeout=30)
    img_resp.raise_for_status()
    dest.write_bytes(img_resp.content)

    log.info("Pexels match: scene %d → %s (photographer: %s)",
              scene_index, best["url"], best.get("photographer", "unknown"))
    return str(dest)


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
    q = sys.argv[1] if len(sys.argv) > 1 else "mountain sunrise cinematic"
    path = get_pexels_image(q, niche={}, output_dir="/tmp/pexels_test", scene_index=1)
    print(f"Saved: {path}")
