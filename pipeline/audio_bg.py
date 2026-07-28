"""
Background audio fetcher.

fetch_bg_audio(query, duration_secs, cfg) -> str | None

Supports two providers (configured in settings.json background_audio.provider):

  "pixabay"    — Pixabay Music API (requires PIXABAY_API_KEY in .env)
  "direct_url" — Download from a list of URLs (no API key needed).
                 background_audio.urls is a list; one is picked randomly per
                 video so tracks vary. Each URL is cached by its index slug.

Returns local path on success, None on any failure (caller degrades gracefully).
"""

import hashlib
import logging
import random
import re
import urllib.request
from pathlib import Path

import requests

log = logging.getLogger(__name__)

_PIXABAY_MUSIC_URL = "https://pixabay.com/api/music/"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _url_slug(url: str) -> str:
    """Stable 8-char hash slug for a URL — used as cache filename."""
    return hashlib.md5(url.encode()).hexdigest()[:8]


def _cache_dir(cfg) -> Path:
    audio_base = "output/audio"
    if cfg:
        paths_dict = getattr(cfg, "paths", {}) or {}
        audio_base = paths_dict.get("audio", audio_base) if isinstance(paths_dict, dict) else audio_base
    return Path(audio_base) / "bg"


def _download(url: str, dest: Path) -> bool:
    """Download url to dest. Returns True on success."""
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        log.info("audio_bg: downloading %s → %s", url, dest)
        urllib.request.urlretrieve(url, dest)
        log.info("audio_bg: saved %s (%d bytes)", dest, dest.stat().st_size)
        return True
    except Exception as e:
        log.warning("audio_bg: download failed: %s", e)
        if dest.exists():
            dest.unlink(missing_ok=True)
        return False


def _fetch_direct_url(cfg) -> str | None:
    """Pick a random URL from background_audio.urls, cache and return path."""
    bg_cfg = getattr(cfg, "background_audio", {}) if cfg else {}
    urls = bg_cfg.get("urls", [])
    if not urls:
        log.warning("audio_bg: direct_url provider but background_audio.urls is empty")
        return None

    cache = _cache_dir(cfg)
    # Shuffle so each call tries a different track; fall through on failure
    candidates = random.sample(urls, len(urls))
    for url in candidates:
        slug = _url_slug(url)
        dest = cache / f"bg_{slug}.mp3"
        if dest.exists():
            log.info("audio_bg: cache hit %s", dest)
            return str(dest)
        if _download(url, dest):
            return str(dest)

    log.warning("audio_bg: all direct_url downloads failed")
    return None


def _fetch_pixabay(query: str, cfg) -> str | None:
    """Fetch from Pixabay Music API."""
    api_key = getattr(cfg, "PIXABAY_API_KEY", "") if cfg else ""
    if not api_key:
        log.warning("audio_bg: PIXABAY_API_KEY not set — skipping background audio")
        return None

    cache = _cache_dir(cfg)
    dest = cache / f"{_slug(query)}.mp3"
    if dest.exists():
        log.info("audio_bg: cache hit %s", dest)
        return str(dest)

    try:
        resp = requests.get(
            _PIXABAY_MUSIC_URL,
            params={"key": api_key, "q": query, "category": "meditation"},
            timeout=15,
        )
        resp.raise_for_status()
        hits = resp.json().get("hits", [])
        if not hits:
            log.warning("audio_bg: no Pixabay results for query=%r", query)
            return None
        audio_url = hits[0].get("audio", "")
        if not audio_url:
            log.warning("audio_bg: Pixabay result has no audio URL")
            return None
        return str(dest) if _download(audio_url, dest) else None
    except Exception as e:
        log.warning("audio_bg: Pixabay fetch failed: %s", e)
        return None


def fetch_bg_audio(query: str, duration_secs: float = 0.0, cfg=None) -> str | None:
    """
    Fetch a background audio track.

    Args:
        query:         Search term (used by pixabay provider).
        duration_secs: Video duration in seconds (informational only).
        cfg:           Config singleton.

    Returns:
        Local path to cached MP3, or None on any failure.
    """
    bg_cfg = getattr(cfg, "background_audio", {}) if cfg else {}
    provider = bg_cfg.get("provider", "pixabay")

    if provider == "direct_url":
        return _fetch_direct_url(cfg)
    else:
        return _fetch_pixabay(query, cfg)
