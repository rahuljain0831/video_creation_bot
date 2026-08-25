"""
Accumulated prompt corrections, keyed by (niche, shot type).

The loop this serves is deliberately non-blocking: a generated image is accepted
and the video keeps building. Separately, the critic looks at what came back,
and when it finds a flaw it writes down the correction that would have avoided
it. The next run's prompt for that same shot type starts with those corrections
already applied.

So the prompts get better across runs without anyone tuning them by hand, and
without a failed image ever stalling a video.

This is scaffolding for a young pipeline. Once the prompts stabilise the notes
stop changing, and the whole module can be switched off with
`image_critic.enabled: false` — nothing else depends on it.

Storage is a small JSON file rather than a table in agent.db: the notes are
global to the project rather than per-video, they are edited by hand as often as
by the critic, and being able to read the file is most of their value.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)

_ROOT = Path(__file__).parent.parent
_NOTES_FILE = _ROOT / "prompt_notes.json"

# A prompt can absorb only so much correction before the corrections start
# competing with the subject — the same failure mode as an over-long look block.
_MAX_NOTES_PER_KEY = 3

# How many times a flaw must be seen before its correction is applied. One bad
# image is noise; the same flaw three times is a prompt problem.
_MIN_HITS = 2


def _key(niche_id: str, shot: str) -> str:
    return f"{niche_id or 'unknown'}/{(shot or 'any').strip().lower()}"


def _load() -> dict:
    if not _NOTES_FILE.is_file():
        return {}
    try:
        return json.loads(_NOTES_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("prompt_notes: unreadable (%s) — starting empty", e)
        return {}


def _save(data: dict) -> None:
    try:
        _NOTES_FILE.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as e:
        log.warning("prompt_notes: could not write (%s) — corrections not persisted", e)


def lookup_notes(niche_id: str, shot: str) -> str:
    """
    The correction phrase to append for this (niche, shot), or "".

    Only corrections seen at least `_MIN_HITS` times are returned, most-hit
    first, capped so they never outweigh the subject.
    """
    entries = _load().get(_key(niche_id, shot), {})
    ranked = sorted(
        (e for e in entries.values() if e.get("hits", 0) >= _MIN_HITS),
        key=lambda e: -e.get("hits", 0),
    )
    return ", ".join(e["correction"] for e in ranked[:_MAX_NOTES_PER_KEY])


def record_flaw(niche_id: str, shot: str, flaw: str, correction: str) -> None:
    """
    Note that `flaw` was seen, and what would have avoided it.

    Repeated flaws increment a hit count rather than duplicating, so a
    correction earns its place in the prompt by recurring.
    """
    if not correction:
        return

    data = _load()
    key = _key(niche_id, shot)
    entries = data.setdefault(key, {})
    entry = entries.setdefault(flaw, {"correction": correction, "hits": 0})
    entry["correction"] = correction        # let a better wording win
    entry["hits"] = entry.get("hits", 0) + 1
    entry["last_seen"] = time.strftime("%Y-%m-%d")

    _save(data)
    log.info("prompt_notes: %s — %s (hit %d)", key, flaw, entry["hits"])


def summary() -> str:
    """Human-readable dump of what the critic has learned so far."""
    data = _load()
    if not data:
        return "prompt_notes: nothing recorded yet"

    lines = []
    for key in sorted(data):
        lines.append(key)
        for flaw, e in sorted(data[key].items(), key=lambda kv: -kv[1].get("hits", 0)):
            mark = "*" if e.get("hits", 0) >= _MIN_HITS else " "
            # ASCII only: this prints to a Windows console under cp1252, where a
            # unicode arrow raises UnicodeEncodeError and takes the run with it.
            lines.append(f"  {mark} {e.get('hits', 0):>2}x {flaw} -> {e['correction']}")
    lines.append(f"\n(* = active, applied to new prompts at {_MIN_HITS}+ hits)")
    return "\n".join(lines)
