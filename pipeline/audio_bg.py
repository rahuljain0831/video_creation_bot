"""
Background audio fetcher — Pixabay Music API.

fetch_bg_audio(query, duration_secs, cfg) -> str | None

Downloads first matching track to data/audio/bg/{slug}.mp3 and caches it.
Returns local path on success, None on any failure (caller degrades gracefully).
"""

import logging
import re
import urllib.request
from pathlib import Path

import requests

log = logging.getLogger(__name__)

_PIXABAY_MUSIC_URL = "https://pixabay.com/api/music/"


def _slug(query: str) -> str:
    """Convert query string to a safe filename slug."""
    return re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")


def fetch_bg_audio(query: str, duration_secs: float = 0.0, cfg=None) -> str | None:
    """
    Fetch a background audio track for the given query.

    Args:
        query:         Search term for Pixabay Music API (e.g. "chanting meditation").
        duration_secs: Intended video duration in seconds (informational, not used in search).
        cfg:           Config singleton. Reads PIXABAY_API_KEY and paths["audio"].

    Returns:
        Absolute path to cached MP3 file, or None if fetch fails for any reason.
    """
    api_key = getattr(cfg, "PIXABAY_API_KEY", "") if cfg else ""
    if not api_key:
        log.warning("audio_bg: PIXABAY_API_KEY not set — skipping background audio")
        return None

    audio_base = "data/audio"
    if cfg:
        audio_base = cfg.paths.get("audio", audio_base)
    cache_dir = Path(audio_base) / "bg"
    cache_path = cache_dir / f"{_slug(query)}.mp3"

    if cache_path.exists():
        log.info("audio_bg: cache hit %s", cache_path)
        return str(cache_path)

    try:
        resp = requests.get(
            _PIXABAY_MUSIC_URL,
            params={"key": api_key, "q": query, "category": "meditation"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        hits = data.get("hits", [])
        if not hits:
            log.warning("audio_bg: no results for query=%r", query)
            return None

        audio_url = hits[0].get("audio", "")
        if not audio_url:
            log.warning("audio_bg: first result has no audio URL")
            return None

        cache_dir.mkdir(parents=True, exist_ok=True)
        log.info("audio_bg: downloading %s → %s", audio_url, cache_path)
        urllib.request.urlretrieve(audio_url, cache_path)
        log.info("audio_bg: saved %s", cache_path)
        return str(cache_path)

    except Exception as e:
        log.warning("audio_bg: fetch failed: %s", e)
        return None
