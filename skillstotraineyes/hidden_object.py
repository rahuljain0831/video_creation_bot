"""
Hidden-object image posts: a dense scene with one thing buried in it.

Not a Reel and not a drill — a single still for the Instagram feed. The answer
is never revealed anywhere: not in the image, not in the caption, not in a
follow-up. Viewers argue it out in the comments, which is the whole point.

Three constraints are enforced here rather than hoped for, because each one
fails silently:

  * The niche must run `human_policy: "none"`. The default "never" strips
    human-referring chunks out of the positive prompt, so "a sniper in a
    ghillie suit" would be deleted from its own brief and pushed into the
    negative. A test pins the settings.json value.
  * The prompt is capped at 55 words with the target named first. The FLUX
    providers truncate near 77 CLIP tokens, and a long "bury it" prompt loses
    the very thing it is supposed to hide.
  * Any result whose short edge is under 1024px is rejected rather than
    upscaled. Pollinations returns 576x1024 for a 1080x1920 ask, and no amount
    of lanczos puts back the detail a camouflaged target needs.
"""

import json
import logging
import random
import re
from pathlib import Path

from PIL import Image

from skillstotraineyes.wording import has_claim, reveals_position

log = logging.getLogger(__name__)



def call_llm(*args, **kwargs):
    """Late-bound so importing this module never loads .env (config) into os.environ."""
    from llm_router import call_llm as _call
    return _call(*args, **kwargs)


def generate_image(*args, **kwargs):
    from pipeline.image_gen import generate_image as _gen
    return _gen(*args, **kwargs)


MAX_PROMPT_WORDS = 55
MIN_SHORT_EDGE = 1024
MAX_FIELD = 90
JPEG_QUALITY = 92

# Enough to keep going when the LLM is down or answers with junk.
FALLBACK_SCENES = [
    {"environment": "a dry rocky mountainside at noon",
     "target": "a sand-coloured snake",
     "difficulty_note": "coiled in shadow between two boulders"},
    {"environment": "a dense green jungle canopy",
     "target": "a sniper in a ghillie suit",
     "difficulty_note": "prone under low ferns"},
    {"environment": "a snowbound pine forest at dusk",
     "target": "a white arctic fox",
     "difficulty_note": "curled against a drift"},
    {"environment": "a cluttered autumn leaf floor",
     "target": "a brown moth",
     "difficulty_note": "flat against a dead leaf"},
]


class HiddenObjectError(RuntimeError):
    pass


def _clean(value, max_len: int = MAX_FIELD) -> str | None:
    if not isinstance(value, str):
        return None
    s = " ".join(value.split()).strip(" \"'")
    return s if s and len(s) <= max_len else None


def invent_scene(seed: int, recent: list[dict], cfg=None, use_llm: bool = True) -> dict:
    """
    Ask the LLM for one (environment, target) pair. Never raises.

    Its answer is untrusted: empty or over-long fields, and a dead LLM, both fall
    back to a built-in scene so a post is never lost to a quota.
    """
    fallback = FALLBACK_SCENES[seed % len(FALLBACK_SCENES)]
    if not use_llm:
        return dict(fallback)
    recent_targets = [r.get("target") for r in recent[:10] if r.get("target")]
    prompt = f"""Invent a "find the hidden object" photo brief.

A dense, busy natural or man-made scene with ONE thing camouflaged in it that a
viewer has to hunt for. Hard but genuinely findable.

Avoid these recent targets: {recent_targets}

Respond with ONLY JSON:
{{"environment": "the scene, under 12 words",
  "target": "the one hidden thing, under 8 words",
  "difficulty_note": "how it blends in, under 12 words"}}"""

    try:
        raw, model = call_llm(prompt, cfg_router=(cfg.llm_router if cfg else {}), temperature=1.0)
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(m.group(0)) if m else {}
    except Exception as e:
        log.warning("hidden_object: LLM failed (%s) — using a built-in scene", e)
        return dict(fallback)

    scene = {}
    for key in ("environment", "target", "difficulty_note"):
        val = _clean(data.get(key))
        # The target reaches the caption, the IG title and the DB, so it also may not
        # name a position. Position words are fine in a scene ("top of a mountain").
        if val and (has_claim(val) or (key == "target" and reveals_position(val))):
            log.info("hidden_object: %s %r rejected — using the built-in one", key, val)
            val = None
        scene[key] = val or fallback[key]
    if scene["target"] in recent_targets:
        log.info("hidden_object: %r repeats a recent target — using a built-in scene",
                 scene["target"])
        return dict(fallback)
    log.info("hidden_object: %s in %s (via %s)", scene["target"], scene["environment"], model)
    return scene


def build_prompt(scene: dict) -> str:
    """
    One image brief, target first and capped at MAX_PROMPT_WORDS.

    Target first is deliberate: FLUX truncates the tail, so whatever must
    survive goes at the front. The environment is what gets trimmed when a
    verbose LLM answer would breach the cap.
    """
    head = (f"{scene['target']} camouflaged and hidden, {scene['difficulty_note']}, "
            f"in {scene['environment']}")
    tail = "photorealistic, sharp focus, rich detail, natural light, wide establishing shot"
    words = f"{head}, {tail}".split()
    if len(words) <= MAX_PROMPT_WORDS:
        return " ".join(words)
    keep = MAX_PROMPT_WORDS - len(tail.split())
    return " ".join(head.split()[:keep] + tail.split())


def generate(scene: dict, out_dir, seed: int, niche: dict, cfg=None) -> str:
    """
    Generate the scene image and return a path to a JPEG at full resolution.

    Raises HiddenObjectError when the provider chain returns nothing usable —
    caller decides whether to retry with a new seed or give up on this post.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prompt = build_prompt(scene)
    log.info("hidden_object prompt (%d words): %s", len(prompt.split()), prompt)

    # Per-run subdirectory: generate_image always names its file scene_00.<ext>.
    raw = generate_image(
        image_prompt=prompt, niche=niche, output_dir=str(out_dir / f"raw_{seed}"),
        scene_index=0, cfg=cfg, seed=seed, use_notes=False,
        min_short_edge=MIN_SHORT_EDGE,
    )
    img = Image.open(raw)
    short = min(img.size)
    if short < MIN_SHORT_EDGE:
        raise HiddenObjectError(
            f"provider returned {img.size[0]}x{img.size[1]}; the short edge must be "
            f"at least {MIN_SHORT_EDGE}px — upscaling cannot restore the detail a "
            f"camouflaged target needs"
        )

    # Feed photos outside 4:5..1.91:1 are rejected; centre-crop only when too tall.
    w, h = img.size
    if h * 4 > w * 5:
        new_h = w * 5 // 4
        top = (h - new_h) // 2
        img = img.crop((0, top, w, top + new_h))

    out = out_dir / f"hidden_{seed}.jpg"
    img.convert("RGB").save(out, "JPEG", quality=JPEG_QUALITY, optimize=True)
    log.info("hidden_object image: %s (%dx%d)", out, *img.size)
    return str(out)
