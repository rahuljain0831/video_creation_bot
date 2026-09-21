"""
LLM wording for a drill: a fresh hook, a closing question, and a caption.

The LLM never touches geometry or timing — only these three strings. Its output
is untrusted, so every field is length-checked, screened for medical claims, and
compared against recent wording; anything that fails falls back to the drill's
own default text. A failed or offline LLM therefore costs variety, never a video.

The disclaimer is appended by the caller in code, never by the LLM.
"""

import json
import logging
import re

log = logging.getLogger(__name__)



def call_llm(*args, **kwargs):
    """Late-bound so importing this module never loads .env (config) into os.environ."""
    from llm_router import call_llm as _call
    return _call(*args, **kwargs)


MAX_HOOK = 40
MAX_QUESTION = 40
MAX_CAPTION = 220

# Anything that reads as a health claim. Eye-exercise content is fine as a game;
# it must not promise results.
_CLAIMS = re.compile(
    r"\b(cure[sd]?|treat(?:s|ed|ment)?|heal(?:s|ing)?|therapy|eyesight|myopia|"
    r"glasses|prescription|diagnos\w*|20/20|improv\w*|strengthen\w*|fix|"
    r"better vision|sharper|restore\w*|medical|doctor)\b",
    re.IGNORECASE,
)

FALLBACK_CAPTIONS = [
    "Can you keep up? Try this one and tell me how you did.",
    "Give your eyes a little workout. Ready?",
    "One quick eye game. How did you do?",
    "Focus up for a few seconds. Can you nail it?",
]


# The answer is never revealed, so the caption must not leak where it is.
_POSITION = re.compile(
    r"\b(top|bottom|upper|lower|left|right|centre|center|middle|corner|edge|"
    r"behind|beneath|under(?:neath)?|beside|next to|near the|"
    r"first|second|third|fourth)\b",
    re.IGNORECASE,
)


def reveals_position(text: str) -> bool:
    """True if the wording hints at where the hidden target is."""
    return bool(_POSITION.search(text))


def has_claim(text: str) -> bool:
    return bool(_CLAIMS.search(text))


def _parse(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    return json.loads(m.group(0)) if m else {}


def _clean(value, max_len: int, recent: list[str]) -> str | None:
    if not isinstance(value, str):
        return None
    s = " ".join(value.split()).strip(" \"'")
    if not s or len(s) > max_len or has_claim(s):
        return None
    if s.lower() in {r.lower() for r in recent}:
        return None
    return s


def generate_wording(family: str, defaults: dict, recent: list[dict], cfg=None) -> dict:
    """
    Return any of {"hook", "question", "caption"} that passed validation.
    Missing keys mean "use the default". Never raises.
    """
    recent_hooks = [r["hook"] for r in recent if r.get("hook")]
    recent_caps = [r["caption"] for r in recent if r.get("caption")]
    prompt = f"""Write short on-screen wording for a playful eye-exercise Instagram Reel.
Drill: {defaults.get('name', family)} — {defaults.get('about', '')}
Difficulty shown on screen: {defaults.get('level_label', 'none')}
Default instruction: "{defaults['hook']}"
Default closing question: "{defaults['question']}"

Rules:
- "hook": rewrite the default instruction in a fresh way. SAME task, at most {MAX_HOOK} characters.
- "question": a closing question the viewer can answer, at most {MAX_QUESTION} characters.
- "caption": one or two friendly sentences, at most {MAX_CAPTION} characters, no hashtags.
- Treat it as a game. Never mention health, vision improvement, treatment or results.
- Avoid these recent hooks: {recent_hooks[-8:]}

Respond with ONLY JSON: {{"hook": "...", "question": "...", "caption": "..."}}"""

    try:
        raw, model = call_llm(prompt, cfg_router=(cfg.llm_router if cfg else {}), temperature=0.9)
        data = _parse(raw)
    except Exception as e:  # LLM offline, quota exhausted, bad JSON: all just "no wording"
        log.warning("wording: LLM failed (%s) — using defaults", e)
        return {}

    out = {}
    for key, limit, pool in (("hook", MAX_HOOK, recent_hooks),
                             ("question", MAX_QUESTION, []),
                             ("caption", MAX_CAPTION, recent_caps)):
        v = _clean(data.get(key), limit, pool)
        if v:
            out[key] = v
        else:
            log.info("wording: %s rejected, using default", key)
    log.info("wording: accepted %s via %s", sorted(out), model)
    return out


def hidden_object_caption(scene: dict, recent: list[dict], cfg=None) -> str:
    """
    A caption that poses the hunt without answering it.

    Screened three ways, because each failure ships: a medical claim, a position
    hint that gives the answer away, or a repeat of a recent caption. Any hit
    falls back to a plain default that names the target and nothing else.
    """
    default = f"Somewhere in this picture: {scene['target']}. Can you find it? Answers below."
    recent_caps = [r["caption"] for r in recent if r.get("caption")]
    prompt = f"""Write one Instagram caption for a "find the hidden object" photo.

Scene: {scene['environment']}
Hidden in it: {scene['target']}

Rules:
- Invite people to hunt and to comment their answer. At most {MAX_CAPTION} characters.
- NEVER say where it is. No directions, no corners, no "behind" or "under".
- Treat it as a game. Never mention health, vision, treatment or results.
- No hashtags.

Respond with ONLY JSON: {{"caption": "..."}}"""

    try:
        raw, model = call_llm(prompt, cfg_router=(cfg.llm_router if cfg else {}), temperature=0.9)
        candidate = _parse(raw).get("caption")
    except Exception as e:
        log.warning("hidden_object_caption: LLM failed (%s) — using the default", e)
        return default

    cleaned = _clean(candidate, MAX_CAPTION, recent_caps)
    if cleaned and not reveals_position(cleaned):
        log.info("hidden_object_caption: accepted via %s", model)
        return cleaned
    log.info("hidden_object_caption: rejected — using the default")
    return default


def pick_drill(seed: int, recent: list[dict], cfg=None) -> str:
    """
    Let the LLM choose the next drill from the catalog.

    Its answer is untrusted, so an id that is not in the catalog — or a dead LLM —
    falls back to the seeded rotation. Recent ids are shown so it spreads out.
    """
    from skillstotraineyes.drills import FAMILIES, load_catalog, pick_family

    last = [r.get("drill_id") or r.get("family") for r in recent[:1] if r.get("drill_id") or r.get("family")]
    fallback = pick_family(seed, last)
    recent_ids = [r.get("drill_id") or r.get("family") for r in recent[:10]
                  if r.get("drill_id") or r.get("family")]
    menu = "\n".join(f'- {e["id"]} [{e["mode"]}]: {e["name"]} — {e["about"]}' for e in load_catalog())
    prompt = (
        "Pick ONE eye-exercise drill for the next short video.\n\n"
        f"{menu}\n\n"
        f"Recently used, avoid these: {recent_ids}\n"
        'Respond with ONLY JSON: {"drill_id": "..."}'
    )
    try:
        raw, model = call_llm(prompt, cfg_router=(cfg.llm_router if cfg else {}), temperature=1.0)
        choice = _parse(raw).get("drill_id")
    except Exception as e:
        log.warning("pick_drill: LLM failed (%s) — rotating instead", e)
        return fallback
    if isinstance(choice, str) and choice in FAMILIES and choice not in last:
        log.info("pick_drill: %s via %s", choice, model)
        return choice
    log.info("pick_drill: %r rejected — rotating to %s", choice, fallback)
    return fallback
