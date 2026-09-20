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
    from llm_router import call_llm

    recent_hooks = [r["hook"] for r in recent if r.get("hook")]
    recent_caps = [r["caption"] for r in recent if r.get("caption")]
    prompt = f"""Write short on-screen wording for a playful eye-exercise Instagram Reel.
Drill type: {family}
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
