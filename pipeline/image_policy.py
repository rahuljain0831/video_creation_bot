"""
Image generation policy — two hard rules, enforced in code, not in prompts.

1. Mythology-type niches never use locally generated images.
   Deity iconography (arm counts, avatars, mounts, serene faces) is exactly what
   a local SD checkpoint mangles, so those niches stay on the curated library.
   Controlled per-niche by `allow_local_generation` in settings.json
   (default true; mythology sets it to false).

2. Generated images never show a clear face or a clear hand.

   The original rule banned people outright, which was aimed at the wrong
   target. Distorted *faces and hands* are what betray a generated image; a
   figure seen from behind, a silhouette in a doorway, someone half-swallowed by
   shadow — those carry the shot and never trip the anatomy problem. Banning
   them cost every scary story its most useful subject.

   So the policy is graded per niche, via `human_policy`:

       "never"     no people at all (the old behaviour, still the default)
       "obscured"  figures welcome, faces and hands never legible
       "none"      no restriction

   `no_humans: true` still means "never", so existing niches are unaffected.
"""

import logging
import re

log = logging.getLogger(__name__)

# Sources that produce a machine-generated image (as opposed to retrieval).
GENERATED_SOURCES = ("generate", "comfyui")

# Sources that run the model on this machine.
LOCAL_SOURCES = ("comfyui",)

# Sources where there is no image file at all — the renderer draws every frame.
PROCEDURAL_SOURCES = ("procedural",)

HUMAN_POLICIES = ("never", "obscured", "none")

NO_HUMAN_POSITIVE_SUFFIX = "no people, no humans, unpopulated empty scene"

NO_HUMAN_NEGATIVE_TERMS = (
    "person, people, human, man, woman, child, boy, girl, face, portrait, "
    "hands, fingers, arms, legs, body, crowd, human figure, silhouette of a person, "
    "nudity, nude, nsfw, sexual, naked, exposed skin, cleavage, erotic, topless, "
    "bare body, underwear, lingerie, swimsuit, bikini, suggestive, seductive, "
    "sensual, intimate, undressed, shirtless, skimpy, see-through, lewd, explicit"
)

# "obscured": the figure stays, the anatomy that breaks does not. Every phrase
# here describes a way of showing a person that a diffusion model renders
# reliably — turned away, backlit, cropped, distant.
OBSCURED_POSITIVE_SUFFIX = (
    "seen from behind, face not visible, hands out of frame, half in shadow"
)

# The same rule for providers with no negative channel, where every word is
# taken out of the subject's budget. Long-form constraints are how a 55-word
# prompt becomes a 111-word one and stops rendering the subject at all.
FLUX_CONSTRAINTS = {
    "never":    "no people, no nudity",
    "obscured": "no face, no hands, no nudity, fully clothed",
    "none":     "no nudity, fully clothed",
}

OBSCURED_NEGATIVE_TERMS = (
    "face, facial features, eyes, mouth, teeth, portrait, close-up of a face, "
    "looking at camera, hands, fingers, visible fingers, deformed hands, "
    "extra fingers, mangled hands, distorted face, disfigured face, "
    "nudity, nude, nsfw, sexual, naked, exposed skin, cleavage, erotic, topless, "
    "bare body, underwear, lingerie, swimsuit, bikini, suggestive, seductive, "
    "sensual, intimate, undressed, shirtless, skimpy, see-through, lewd, explicit"
)

# Chunks naming a face or a hand are dropped even under "obscured" — asking for
# the thing and forbidding it in the same breath just produces the distortion.
_ANATOMY_WORDS = (
    "face", "faces", "facial", "portrait", "eyes", "eye", "mouth", "teeth",
    "smile", "smiling", "expression", "hand", "hands", "finger", "fingers",
    "palm", "palms", "fist", "knuckle", "knuckles",
)

_ANATOMY_RE = re.compile(r"\b(" + "|".join(_ANATOMY_WORDS) + r")\b", re.IGNORECASE)

# Words that mean a human is (or may be) in frame.
_HUMAN_WORDS = (
    "human", "humans", "person", "persons", "people", "man", "men", "woman",
    "women", "child", "children", "kid", "kids", "boy", "boys", "girl", "girls",
    "male", "female", "guy", "lady", "crowd", "crowds", "figure", "figures",
    "silhouette", "silhouettes", "face", "faces", "portrait", "hand", "hands",
    "finger", "fingers", "arm", "arms", "body", "bodies", "worker", "workers",
    "scientist", "scientists", "trader", "traders", "businessman", "businesswoman",
    "warrior", "warriors", "soldier", "soldiers", "priest", "priests", "monk",
    "monks", "king", "queen", "god", "goddess", "deity", "hero", "heroine",
    "villager", "villagers", "astronaut", "astronauts", "pedestrian", "pedestrians",
)

_HUMAN_RE = re.compile(r"\b(" + "|".join(_HUMAN_WORDS) + r")\b", re.IGNORECASE)

_FALLBACK_SUBJECT = "empty scene, environment only"


class LocalGenerationBlocked(RuntimeError):
    """Raised when a niche is not permitted to use locally generated images."""


def local_generation_allowed(niche: dict) -> bool:
    """True unless the niche opts out via allow_local_generation: false."""
    return bool(niche.get("allow_local_generation", True))


def assert_local_generation_allowed(niche: dict) -> None:
    """Raise if this niche must never use locally generated images."""
    if not local_generation_allowed(niche):
        raise LocalGenerationBlocked(
            f"Niche {niche.get('id', '?')!r} has allow_local_generation=false — "
            "local image generation is not permitted for it (use library/pexels)."
        )


def is_procedural(niche: dict) -> bool:
    """
    True when the niche has no image stage at all — the video renderer draws
    every frame itself (Remotion typography/atmosphere).
    """
    return niche.get("image_source", "library") in PROCEDURAL_SOURCES


def resolve_image_source(niche: dict) -> str:
    """
    Return the effective image_source for a niche, after policy.

    A niche that disallows local generation but is configured for a local-only
    source falls back to 'library' rather than failing the run.
    """
    source = niche.get("image_source", "library")
    if source in LOCAL_SOURCES and not local_generation_allowed(niche):
        log.warning(
            "Niche %r has image_source=%r but allow_local_generation=false — "
            "falling back to 'library'.",
            niche.get("id", "?"), source,
        )
        return "library"
    return source


def sanitize_no_humans(positive_prompt: str) -> str:
    """
    Drop every comma-separated chunk of the prompt that refers to a human,
    then append the no-people directive.

    Image prompts are comma-delimited tag lists, so dropping a whole chunk
    removes the subject cleanly instead of leaving a broken sentence.
    """
    chunks = [c.strip() for c in positive_prompt.split(",")]
    kept = [c for c in chunks if c and not _HUMAN_RE.search(c)]
    dropped = len(chunks) - len(kept)

    if dropped:
        log.info("no-human policy: dropped %d/%d prompt chunk(s)", dropped, len(chunks))

    if not kept:
        kept = [_FALLBACK_SUBJECT]

    kept.append(NO_HUMAN_POSITIVE_SUFFIX)
    return ", ".join(kept)


def sanitize_obscured(positive_prompt: str) -> str:
    """
    Keep the people, drop the anatomy.

    Only chunks naming a face or a hand are removed; "a figure standing at the
    end of the corridor" survives untouched, which is the whole point.
    """
    chunks = [c.strip() for c in positive_prompt.split(",")]
    kept = [c for c in chunks if c and not _ANATOMY_RE.search(c)]
    dropped = len(chunks) - len(kept)

    if dropped:
        log.info("obscured-human policy: dropped %d/%d face/hand chunk(s)",
                 dropped, len(chunks))

    if not kept:
        kept = [_FALLBACK_SUBJECT]

    kept.append(OBSCURED_POSITIVE_SUFFIX)
    return ", ".join(kept)


def resolve_human_policy(niche: dict, config: dict | None = None) -> str:
    """
    Which human policy applies to this niche.

    `human_policy` wins when set. Otherwise the legacy `no_humans` boolean is
    honoured — niche first, then the global image_gen config — so nothing that
    predates the graded policy changes behaviour.
    """
    policy = str(niche.get("human_policy", "")).strip().lower()
    if policy in HUMAN_POLICIES:
        return policy
    if policy:
        log.warning("Unknown human_policy %r — falling back to 'never'", policy)

    no_humans = niche.get("no_humans")
    if no_humans is None:
        no_humans = (config or {}).get("no_humans", True)
    return "never" if no_humans else "none"


def apply_human_policy(positive: str, negative: str, policy: str) -> tuple[str, str]:
    """Return (positive, negative) with the niche's human policy applied."""
    if policy == "none":
        return positive, negative

    if policy == "obscured":
        positive = sanitize_obscured(positive)
        terms = OBSCURED_NEGATIVE_TERMS
    else:
        positive = sanitize_no_humans(positive)
        terms = NO_HUMAN_NEGATIVE_TERMS

    negative = f"{negative}, {terms}" if negative else terms
    return positive, negative


def apply_no_human_policy(positive: str, negative: str) -> tuple[str, str]:
    """Deprecated: the 'never' policy. Kept so older callers keep working."""
    return apply_human_policy(positive, negative, "never")
