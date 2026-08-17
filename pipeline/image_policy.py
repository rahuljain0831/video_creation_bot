"""
Image generation policy — two hard rules, enforced in code, not in prompts.

1. Mythology-type niches never use locally generated images.
   Deity iconography (arm counts, avatars, mounts, serene faces) is exactly what
   a local SD checkpoint mangles, so those niches stay on the curated library.
   Controlled per-niche by `allow_local_generation` in settings.json
   (default true; mythology sets it to false).

2. Generated images never contain human figures.
   Distorted hands and faces are the biggest quality problem in local output, so
   people are kept out of frame entirely: human-referring chunks are stripped
   from the positive prompt and pushed into the negative prompt.
"""

import logging
import re

log = logging.getLogger(__name__)

# Sources that produce a machine-generated image (as opposed to retrieval).
GENERATED_SOURCES = ("generate", "comfyui")

# Sources that run the model on this machine.
LOCAL_SOURCES = ("comfyui",)

NO_HUMAN_POSITIVE_SUFFIX = "no people, no humans, unpopulated empty scene"

NO_HUMAN_NEGATIVE_TERMS = (
    "person, people, human, man, woman, child, boy, girl, face, portrait, "
    "hands, fingers, arms, legs, body, crowd, human figure, silhouette of a person"
)

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


def apply_no_human_policy(positive: str, negative: str) -> tuple[str, str]:
    """Return (positive, negative) with the no-human rule applied to both."""
    positive = sanitize_no_humans(positive)
    negative = f"{negative}, {NO_HUMAN_NEGATIVE_TERMS}" if negative else NO_HUMAN_NEGATIVE_TERMS
    return positive, negative
