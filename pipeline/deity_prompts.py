"""
Deity prompt lookup — enriches AI image prompts with canonical visual identity
from the Indian deity prompts file.

On first import, lazy-loads the JSON file and builds a name+alias lookup.
Call get_lookup(cfg) to get the dict, find_deity(text, lookup) to detect a deity,
build_visual_suffix(deity) to get the enrichment string.
"""

import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

_LOOKUP: dict[str, dict] = {}
_LOADED: bool = False


def load_deity_prompts(path: str) -> list[dict]:
    """
    Load and sanitize the deity JSON file.
    Handles invalid control characters (including literal newlines inside strings).
    """
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    # Replace ALL control chars (0x00-0x1f) with space. Safe for JSON:
    # structural whitespace (newlines/tabs between tokens) → space (valid).
    # Illegal unescaped control chars inside string values → space (fixes bugs).
    text = re.sub(r"[\x00-\x1f]", " ", text)
    return json.loads(text)


_HONORIFICS = re.compile(
    r"^(lord|goddess|devi|sri|shri|mata|maa|swami|bhagwan|bhagwan|shree)\s+",
    re.IGNORECASE,
)


def _normalize_key(text: str) -> list[str]:
    """
    Return all useful lookup keys for a name string.
    Strips parentheticals, honorifics, and produces both full and base forms.
    """
    # Strip parenthetical suffixes e.g. "(Kodandapani Avatar)" "(warrior aspect)"
    base = re.sub(r"\s*\(.*?\)", "", text).strip().lower()
    keys = [base]
    # Also strip leading honorific: "lord rama" → "rama"
    stripped = _HONORIFICS.sub("", base).strip()
    if stripped and stripped != base:
        keys.append(stripped)
    return [k for k in keys if k]


def _build_lookup(data: list[dict]) -> dict[str, dict]:
    """
    Build lowercased name+alias → deity dict.
    Handles prefixes like "Lord", "Goddess", "Sri" and parenthetical suffixes.
    Also indexes tags as lookup keys.
    First entry wins for any given key (preserves canonical deities over variants).
    """
    lookup: dict[str, dict] = {}

    def _add(text: str, entry: dict) -> None:
        for key in _normalize_key(text):
            if key and key not in lookup:
                lookup[key] = entry

    for entry in data:
        _add(entry.get("deity_name", ""), entry)
        for alias in entry.get("aliases", []):
            _add(alias, entry)
        for tag in entry.get("tags", []):
            _add(str(tag), entry)

    return lookup


def get_lookup(cfg=None) -> dict[str, dict]:
    """Lazy-load and return the deity lookup dict (singleton)."""
    global _LOOKUP, _LOADED
    if _LOADED:
        return _LOOKUP

    path: str | None = None

    # Try cfg.image_gen.deity_prompts_path first
    if cfg:
        ig = getattr(cfg, "image_gen", {})
        rel_path = ig.get("deity_prompts_path", "")
        if rel_path:
            db_path = Path(cfg.paths.get("db", "output/db/agent.db"))
            project_root = db_path.parent.parent
            candidate = project_root / rel_path
            if candidate.exists():
                path = str(candidate)

    # Fallback: look next to the pipeline package (project root)
    if path is None:
        root = Path(__file__).parent.parent
        # Prefer clean .json version if it exists
        for fname in ("deity_prompts.json", "indian deities image generation prompts.txt"):
            candidate = root / fname
            if candidate.exists():
                path = str(candidate)
                break

    if path is None:
        log.warning("deity_prompts: file not found — prompt enrichment disabled")
        _LOADED = True
        return _LOOKUP

    try:
        data = load_deity_prompts(path)
        _LOOKUP = _build_lookup(data)
        log.info(
            "deity_prompts: loaded %d entries → %d lookup keys from %s",
            len(data), len(_LOOKUP), Path(path).name,
        )
    except Exception as exc:
        log.warning("deity_prompts: load failed (%s) — enrichment disabled", exc)

    _LOADED = True
    return _LOOKUP


def find_deity(text: str, lookup: dict) -> dict | None:
    """
    Scan text for any deity name/alias using word-boundary regex.
    Longer keys checked first to avoid "Rama" matching inside "Brahma".
    Returns first (canonical) match or None.
    """
    lower_text = text.lower()
    for name in sorted(lookup, key=len, reverse=True):
        if re.search(r"\b" + re.escape(name) + r"\b", lower_text):
            return lookup[name]
    return None


def _norm_base(name: str) -> str:
    """Normalize deity name to base form for variant grouping."""
    name = re.sub(r"\s*\(.*?\)", "", name).strip().lower()
    name = re.sub(
        r"^(lord|goddess|devi|sri|shri|mata|maa|swami|bhagwan|shree)\s+",
        "", name, flags=re.IGNORECASE,
    ).strip()
    return name


def find_all_variants(deity_name: str, all_data: list[dict]) -> list[dict]:
    """
    Return all entries from all_data whose deity_name, aliases, or tags match
    deity_name using word-boundary regex (avoids "rama" matching "reincarnation").
    """
    target = _norm_base(deity_name).lower()
    pattern = re.compile(r"\b" + re.escape(target) + r"\b", re.IGNORECASE)
    results = []
    for d in all_data:
        full_name = d.get("deity_name", "")
        aliases = [str(a) for a in d.get("aliases", [])]
        tags = [str(t) for t in d.get("tags", [])]
        candidates = [full_name, _norm_base(full_name)] + aliases + [_norm_base(a) for a in aliases] + tags
        if any(pattern.search(c) for c in candidates):
            results.append(d)
    return results


def load_all_data(cfg=None) -> list[dict]:
    """Return the full parsed deity list (for variant selection)."""
    path: str | None = None
    if cfg:
        ig = getattr(cfg, "image_gen", {})
        rel = ig.get("deity_prompts_path", "")
        if rel:
            db_path = Path(cfg.paths.get("db", "output/db/agent.db"))
            project_root = db_path.parent.parent
            candidate = project_root / rel
            if candidate.exists():
                path = str(candidate)
    if path is None:
        root = Path(__file__).parent.parent
        for fname in ("deity_prompts.json", "indian deities image generation prompts.txt"):
            candidate = root / fname
            if candidate.exists():
                path = str(candidate)
                break
    if path is None:
        return []
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"[\x00-\x1f]", " ", text)
    return json.loads(text)


def build_visual_suffix(deity: dict) -> str:
    """
    Compact visual identity string appended to the LLM image_prompt.
    Combines visual_details, iconography, color_palette, and attire.
    """
    parts: list[str] = []
    if deity.get("visual_details"):
        parts.append(deity["visual_details"])
    if deity.get("iconography_and_symbols"):
        parts.append(f"Iconography: {deity['iconography_and_symbols']}")
    if deity.get("color_palette"):
        parts.append(f"Colors: {deity['color_palette']}")
    if deity.get("clothing_and_jewelry"):
        parts.append(f"Attire: {deity['clothing_and_jewelry']}")
    return ". ".join(parts)


def get_negative_prompt(deity: dict) -> str:
    """Return deity's negative prompt string (handles both key variants)."""
    return deity.get("negative_prompt", "") or deity.get("negative_prompts", "") or ""


def get_avatar_list(deity_name: str, all_data: list[dict]) -> list[str]:
    """Return list of scene_title names for all variants of a deity."""
    variants = find_all_variants(deity_name, all_data)
    return [v.get("scene_title", v.get("deity_name", "")) for v in variants if v.get("scene_title") or v.get("deity_name")]


def find_by_tradition(tradition: str, all_data: list[dict]) -> list[dict]:
    """Return all deity entries whose tradition field matches (case-insensitive)."""
    t = tradition.lower()
    return [d for d in all_data if d.get("tradition", "").lower() == t]
