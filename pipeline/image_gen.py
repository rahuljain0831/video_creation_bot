"""
Image generation router — cloud image APIs first, local ComfyUI as last resort.

Mirrors llm_router.py: providers are read from image_keys.json, top-to-bottom
is priority, providers with an empty api_key are skipped (except keyless ones
like Pollinations). If every cloud provider fails, the local ComfyUI chain runs —
unless the niche forbids local generation (see pipeline/image_policy.py).

Same input/output contract as get_library_image / get_pexels_image:
    generate_image(image_prompt, niche, output_dir, scene_index, cfg) -> str (path)
"""

import base64
import json
import logging
import os
import time
from pathlib import Path
from urllib.parse import quote

import requests

from pipeline.image_policy import (
    FLUX_CONSTRAINTS,
    LocalGenerationBlocked,
    apply_human_policy,
    local_generation_allowed,
    resolve_human_policy,
)
from pipeline.prompt_notes import lookup_notes

log = logging.getLogger(__name__)

_ROOT = Path(__file__).parent.parent
_KEYS_FILE = _ROOT / "image_keys.json"

# Fallback env vars per provider, used when image_keys.json has no key for it.
# Several names per provider on purpose: .env in the wild uses HF_API_TOKEN while
# the older code only looked for HUGGINGFACE_API_KEY, which silently skipped a
# provider that was in fact configured. First non-empty wins.
_PROVIDER_ENV_MAP = {
    "gemini":       ("GOOGLE_AI_STUDIO_API_KEY", "GEMINI_API_KEY"),
    "together_ai":  ("TOGETHERAI_API_KEY", "TOGETHER_API_KEY"),
    "huggingface":  ("HF_API_TOKEN", "HUGGINGFACE_API_KEY"),
    "pollinations": (),
}

# Camera language per shot type, prefixed to the prompt.
#
# Without this every scene comes back as the same symmetric wide establishing
# shot, because the prompt only ever describes *what* is in the frame and never
# *how far away the camera is*. Twelve of those in a row is the single biggest
# reason a generated short looks flat.
_SHOT_CAMERA = {
    "wide": "wide establishing shot, deep focus, the whole location visible",
    "detail": "extreme close-up macro shot, 85mm, shallow depth of field, "
              "one small object filling the frame",
    "pov": "first-person point of view, handheld, slightly low angle, "
           "as if the viewer is standing there",
    "object": "medium close shot of a single object, off-centre composition, "
              "background falling out of focus",
    "threshold": "shot looking through a doorway or window, dark foreground "
                 "frame in silhouette, the subject beyond it",
}

# Same framings, compressed. The FLUX family pays for every word out of the
# subject's share of the attention, so it gets the short forms; Gemini reads the
# whole prompt and gets the descriptive ones.
_SHOT_CAMERA_SHORT = {
    "wide": "wide establishing shot, deep focus",
    "detail": "extreme close-up macro, 85mm, shallow focus",
    "pov": "first-person view, handheld, low angle",
    "object": "medium close shot, single object, background out of focus",
    "threshold": "framed through a doorway, dark foreground silhouette",
}

SHOT_TYPES = tuple(_SHOT_CAMERA)

# One look per video, chosen by seed and prefixed to every scene's prompt.
#
# Twelve independently-prompted images drift: different film stocks, different
# colour temperatures, different times of night. Pinning one look per run is
# what makes a set of stills read as one piece of footage. Rotating between
# runs is what stops the channel looking like one repeated video.
#
# **Kept deliberately short.** Tested at one seed across six prompt structures:
# a ~30-word look block wins the frame and destroys the subject — asked for two
# rain-streaked bedroom windows, got a corridor and an alley. An ~11-word block
# holds the look *and* the subject, and moving it before or after the subject
# barely changes the result. Length is the variable, not position. Adding
# adjectives here will quietly cost you the thing the scene is about.
_LOOKS = (
    "35mm film grain, low-key, deep shadow, cold blue-grey night, one light source",
    "handheld camcorder, crushed blacks, sodium-orange streetlight, high contrast",
    "near-monochrome, silver-grey, dense fog, one weak warm light far away",
    "low-light photograph, desaturated, moonlight only, long shadows, fine grain",
    "anamorphic still, teal shadows, dim amber highlight, soft halation",
)

# Applied to every generated scene regardless of look. Pollinations has no
# negative channel, so this has to read as a positive instruction.
_NO_DAYLIGHT = "no daylight"


def build_style_token(seed: int) -> str:
    """The per-run look phrase. Deterministic, so a rerun looks the same."""
    return _LOOKS[seed % len(_LOOKS)]


_DEFAULTS = {
    "width": 720,
    "height": 1280,
    "timeout_seconds": 120,
    "negative_prompt": (
        "deformed, blurry, bad anatomy, disfigured, mutation, extra limb, "
        "ugly, missing limb, long neck, out of frame, watermark, text"
    ),
    "no_humans": True,
    "local_fallback": True,
    # Passes over the whole provider chain before a scene is given up on, and the
    # first backoff in seconds (doubles each pass).
    "max_attempts": 4,
    "retry_base_sleep": 3.0,
}


class ImageGenError(RuntimeError):
    """Raised when every image generation provider fails."""


# Failures worth waiting out (throttling, provider hiccups) vs. failures that
# will still be there in ten seconds (no credits left, key rejected).
_TERMINAL_MARKERS = (
    "http 401", "http 402", "http 403",
    "depleted", "insufficient", "billing", "payment required",
    "invalid api key", "unauthorized", "limit: 0",
)


def _is_terminal(exc: Exception) -> bool:
    """True when retrying this provider for this scene cannot help."""
    msg = str(exc).lower()
    return any(marker in msg for marker in _TERMINAL_MARKERS)


def _sniff_extension(data: bytes) -> str:
    """Return the real file extension for image bytes (providers vary: PNG/JPEG/WebP)."""
    if data.startswith(b"\x89PNG"):
        return ".png"
    if data.startswith(b"\xff\xd8"):
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".png"


def _get_config(cfg) -> dict:
    """Read image_gen config from settings.json, with defaults."""
    config = dict(_DEFAULTS)
    if cfg is not None and getattr(cfg, "image_gen", None):
        config.update(cfg.image_gen)
    return config


def _load_providers() -> list[dict]:
    """
    Read image_keys.json, return provider dicts in priority order.
    Providers with no usable key are skipped unless they are keyless.
    """
    if not _KEYS_FILE.exists():
        log.warning("image_keys.json not found — cloud image providers unavailable")
        return []

    try:
        with open(_KEYS_FILE) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.warning("image_keys.json parse error: %s", e)
        return []

    providers = []
    for name, entry in data.items():
        if name.startswith("_") or not isinstance(entry, dict):
            continue
        if entry.get("enabled", True) is False:
            continue

        api_key = (entry.get("api_key") or "").strip()
        if not api_key:
            for env_var in _PROVIDER_ENV_MAP.get(entry.get("type", name), ()):
                api_key = os.getenv(env_var, "").strip()
                if api_key:
                    break

        if not api_key and not entry.get("keyless", False):
            continue

        providers.append({
            **entry,
            "name": name,
            "type": entry.get("type", name),
            "api_key": api_key,
        })

    return providers


# ── Provider handlers: (provider, prompt, negative, width, height, timeout, seed) -> bytes ──
#
# `seed` is per-scene but derived from one per-run base, so a rerun of the same
# video reproduces the same frames and the twelve scenes of a single video stay
# in the same visual neighbourhood instead of drifting apart.

def _gen_pollinations(provider, prompt, negative, width, height, timeout, seed) -> bytes:
    model = provider.get("model", "flux")
    url = f"https://image.pollinations.ai/prompt/{quote(prompt, safe='')}"
    params = {
        "width": width, "height": height, "model": model,
        "nologo": "true", "seed": seed,
    }
    headers = {}
    if provider["api_key"]:
        headers["Authorization"] = f"Bearer {provider['api_key']}"
    resp = requests.get(url, params=params, headers=headers, timeout=timeout)
    resp.raise_for_status()
    if not resp.content:
        raise ImageGenError("pollinations returned empty body")
    return resp.content


def _gen_huggingface(provider, prompt, negative, width, height, timeout, seed) -> bytes:
    """
    HuggingFace inference, via the router.

    `api-inference.huggingface.co` was retired and its hostname no longer
    resolves, so the old code failed with a DNS error that read like a network
    fault rather than a dead endpoint. Routed inference replaces it, and the
    routed providers expose an OpenAI-shaped images endpoint rather than the old
    `inputs`/`parameters` body.

    `hf-inference` itself no longer serves FLUX ("deprecated and no longer
    supported by provider"), so the routed provider is configurable — check
    https://huggingface.co/api/models/<model>?expand[]=inferenceProviderMapping
    for which ones are live for a given model.
    """
    model = provider.get("model", "black-forest-labs/FLUX.1-schnell")
    route = provider.get("route", "nscale")
    url = f"https://router.huggingface.co/{route}/v1/images/generations"
    payload = {
        "model": model,
        "prompt": prompt,
        "negative_prompt": negative,
        "width": width,
        "height": height,
        "seed": seed,
        "response_format": "b64_json",
    }
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {provider['api_key']}"},
        json=payload,
        timeout=timeout,
    )
    if resp.status_code == 401:
        raise ImageGenError(
            "huggingface HTTP 401 — the token is invalid or lacks inference "
            "permission. Regenerate it at https://huggingface.co/settings/tokens "
            "with 'Make calls to Inference Providers' enabled."
        )
    if resp.status_code != 200:
        raise ImageGenError(f"huggingface HTTP {resp.status_code}: {resp.text[:300]}")

    items = resp.json().get("data", [])
    if not items or not items[0].get("b64_json"):
        raise ImageGenError("huggingface response contained no b64_json image")
    return base64.b64decode(items[0]["b64_json"])


def _gen_together(provider, prompt, negative, width, height, timeout, seed) -> bytes:
    model = provider.get("model", "black-forest-labs/FLUX.1-schnell-Free")
    payload = {
        "model": model,
        "prompt": prompt,
        "negative_prompt": negative,
        "width": width,
        "height": height,
        "steps": provider.get("steps", 4),
        "seed": seed,
        "n": 1,
        "response_format": "b64_json",
    }
    resp = requests.post(
        "https://api.together.xyz/v1/images/generations",
        headers={"Authorization": f"Bearer {provider['api_key']}"},
        json=payload,
        timeout=timeout,
    )
    resp.raise_for_status()
    items = resp.json().get("data", [])
    if not items:
        raise ImageGenError("together_ai returned no image data")
    b64 = items[0].get("b64_json")
    if not b64:
        raise ImageGenError("together_ai response missing b64_json")
    return base64.b64decode(b64)


# Aspect ratios the Gemini image models accept, as (width/height, label).
_GEMINI_RATIOS = (
    (9 / 16, "9:16"), (3 / 4, "3:4"), (4 / 5, "4:5"), (1.0, "1:1"),
    (5 / 4, "5:4"), (4 / 3, "4:3"), (16 / 9, "16:9"),
)


def _gemini_aspect(width: int, height: int) -> str:
    """Closest supported aspect label for the requested pixel size."""
    target = width / height if height else 1.0
    return min(_GEMINI_RATIOS, key=lambda r: abs(r[0] - target))[1]


def _gen_gemini(provider, prompt, negative, width, height, timeout, seed) -> bytes:
    model = provider.get("model", "gemini-2.5-flash-image")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    full_prompt = f"{prompt}. Avoid: {negative}." if negative else prompt
    # Without an explicit imageConfig the model returns a square image, which is
    # useless for a 9:16 short — it either letterboxes or crops the subject out.
    payload = {
        "contents": [{"parts": [{"text": full_prompt}]}],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": {
                "aspectRatio": _gemini_aspect(width, height),
                "imageSize": provider.get("image_size", "2K"),
            },
        },
    }
    resp = requests.post(
        url,
        headers={"x-goog-api-key": provider["api_key"], "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    if resp.status_code != 200:
        # The body says which of model name / quota / config is wrong. Losing it
        # to a bare status code is what let this provider fail unnoticed for
        # every run while a working API key sat in .env.
        raise ImageGenError(
            f"gemini HTTP {resp.status_code}: {resp.text[:400]}"
        )
    for candidate in resp.json().get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                return base64.b64decode(inline["data"])
    raise ImageGenError("gemini response contained no inline image data")


_HANDLERS = {
    "pollinations": _gen_pollinations,
    "huggingface":  _gen_huggingface,
    "together_ai":  _gen_together,
    "gemini":       _gen_gemini,
}


# Providers that run a FLUX-family diffusion model. They share an attention
# cliff (CLIP truncates around 77 tokens) that Gemini does not have, so they get
# a different serialization of the same intent.
_FLUX_PROVIDERS = ("pollinations", "huggingface", "together_ai")

# Providers with no negative-prompt channel at all. Every constraint has to be
# phrased positively inside the prompt or it is silently discarded — which is
# how "no daylight" was being dropped on the most-used fallback.
_NO_NEGATIVE_CHANNEL = ("pollinations",)


def build_positive_prompt(
    image_prompt: str,
    niche: dict,
    shot: str = "",
    style_token: str = "",
    provider_type: str = "",
    human_policy: str = "never",
    notes: str = "",
) -> str:
    """
    Assemble the prompt for one provider from one scene's intent.

    The same intent is serialized differently per provider, so that when a free
    tier runs out mid-video the next provider is asked for the same picture in
    the dialect it actually listens to, instead of the same string twice.

    FLUX family: look first (short), then camera, then the subject at full
    length. Ordering the other way round loses the look; lengthening the look
    block loses the subject. Both were measured, not assumed.

    Gemini: reads the whole prompt and takes instruction, so it gets the art
    style too — the extra words cost nothing there.

    `notes` are accumulated corrections from the critic for this shot type; they
    go last, where they refine rather than compete.
    """
    key = (shot or "").strip().lower()

    if provider_type in _FLUX_PROVIDERS:
        parts = [
            style_token,
            _NO_DAYLIGHT,
            _SHOT_CAMERA_SHORT.get(key, ""),
            image_prompt,
        ]
        # No negative channel — the constraint has to be phrased positively, and
        # kept to a few words. Passing the full SD-era negative list here turned
        # a 55-word prompt into 111 words and cost the subject entirely.
        if provider_type in _NO_NEGATIVE_CHANNEL:
            parts.append(FLUX_CONSTRAINTS.get(human_policy, ""))
    else:
        parts = [
            _SHOT_CAMERA.get(key, ""),
            image_prompt,
            style_token,
            niche.get("art_style_prompt_suffix", ""),
        ]

    if notes:
        parts.append(notes)

    return ", ".join(p for p in parts if p)


def generate_image(
    image_prompt: str,
    niche: dict,
    output_dir: str,
    scene_index: int,
    cfg=None,
    local_only: bool = False,
    *,
    shot: str = "",
    style_token: str = "",
    seed: int = 0,
    name_suffix: str = "",
    use_notes: bool = True,
) -> str:
    """
    Generate one scene image: cloud providers in priority order, then local ComfyUI.

    Args:
        image_prompt: Visual description for the scene.
        niche:        Niche config dict.
        output_dir:   Directory to save the image.
        scene_index:  Scene number (0-based).
        cfg:          Config singleton.
        local_only:   Skip cloud providers, go straight to ComfyUI.
        shot:         Shot type from SHOT_TYPES; selects the camera phrase.
        style_token:  Per-run look phrase shared by every scene of one video.
        seed:         Provider seed, for reproducible reruns.
        name_suffix:  Appended to the filename stem, e.g. "_b" for a beat's
                      second image.

    Returns:
        Path to saved image (output_dir/scene_XX<suffix>.<png|jpg|webp>, extension
        follows the bytes the provider actually returned).

    Raises:
        ImageGenError if every provider fails.
        LocalGenerationBlocked if local generation is needed but the niche forbids it.
    """
    config = _get_config(cfg)
    width = config["width"]
    height = config["height"]
    timeout = config["timeout_seconds"]

    negative = config["negative_prompt"]

    # The human policy is graded per niche: "never" (default), "obscured"
    # (figures yes, faces and hands no) or "none".
    policy = resolve_human_policy(niche, config)
    subject, negative = apply_human_policy(image_prompt, negative, policy)

    # Corrections the critic accumulated for this shot type on earlier runs.
    notes = lookup_notes(niche.get("id", ""), shot) if use_notes else ""
    if notes:
        log.info("Prompt notes for %s/%s: %s", niche.get("id", "?"), shot or "-", notes)

    def prompt_for(provider_type: str) -> str:
        """One scene's intent, in the dialect this provider listens to."""
        return build_positive_prompt(
            subject, niche, shot, style_token,
            provider_type=provider_type, human_policy=policy, notes=notes,
        )

    output_dir_path = Path(output_dir)
    last_error: Exception | None = None

    # ── Cloud providers ──────────────────────────────────────────────────────
    #
    # Each provider gets several attempts, not one. The keyless providers throttle
    # hard under a batch (Pollinations answers 429 to back-to-back requests and
    # then serves the same prompt fine seconds later), so a single pass through
    # the chain reports "all providers failed" for what is really a busy minute.
    # Providers that fail terminally — depleted credits, bad key — are struck off
    # for the rest of the scene instead of being retried into the same wall.
    if not local_only:
        providers = _load_providers()
        if not providers:
            log.warning("No cloud image providers configured (see image_keys.example.json)")

        max_attempts = int(config.get("max_attempts", 4))
        base_sleep = float(config.get("retry_base_sleep", 3.0))
        dead: set[str] = set()

        for attempt in range(1, max_attempts + 1):
            retryable_failure = False

            for provider in providers:
                if provider["name"] in dead:
                    continue
                handler = _HANDLERS.get(provider["type"])
                if handler is None:
                    log.warning("Unknown image provider type: %r", provider["type"])
                    dead.add(provider["name"])
                    continue
                try:
                    log.info("Image gen: provider=%s model=%s scene=%d shot=%s attempt=%d",
                             provider["name"], provider.get("model", "-"),
                             scene_index, shot or "-", attempt)
                    data = handler(provider, prompt_for(provider["type"]),
                                   negative, width, height, timeout, seed)
                    output_path = (
                        output_dir_path
                        / f"scene_{scene_index:02d}{name_suffix}{_sniff_extension(data)}"
                    )
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.write_bytes(data)
                    log.info("Image gen success: %s (%d bytes, provider=%s)",
                             output_path, len(data), provider["name"])
                    return str(output_path)
                except Exception as e:
                    last_error = e
                    if _is_terminal(e):
                        dead.add(provider["name"])
                        log.warning("Image provider %s is out for this scene: %s",
                                    provider["name"], e)
                    else:
                        retryable_failure = True
                        log.warning("Image provider %s failed (attempt %d): %s",
                                    provider["name"], attempt, e)

            if not retryable_failure:
                break          # everything left is terminal — waiting changes nothing
            if attempt < max_attempts:
                pause = base_sleep * (2 ** (attempt - 1))
                log.info("All providers busy for scene %d — retrying in %.0fs", scene_index, pause)
                time.sleep(pause)

    # ── Local ComfyUI fallback ───────────────────────────────────────────────
    if not local_generation_allowed(niche):
        if local_only:
            raise LocalGenerationBlocked(
                f"Niche {niche.get('id', '?')!r} forbids local image generation."
            )
        raise ImageGenError(
            f"All cloud image providers failed and local generation is not permitted "
            f"for niche {niche.get('id', '?')!r}. Last error: {last_error}"
        )

    if not config.get("local_fallback", True) and not local_only:
        raise ImageGenError(
            f"All cloud image providers failed and local_fallback is disabled. "
            f"Last error: {last_error}"
        )

    from pipeline.comfyui_gen import generate_comfyui_image, ComfyUIError

    try:
        log.info("Falling back to local ComfyUI for scene %d", scene_index)
        camera = _SHOT_CAMERA.get((shot or "").strip().lower())
        local_path = generate_comfyui_image(
            image_prompt=f"{camera}, {image_prompt}" if camera else image_prompt,
            niche=niche,
            output_dir=output_dir,
            scene_index=scene_index,
            cfg=cfg,
        )
        if name_suffix:
            # ComfyUI names by scene index alone; without this a beat's second
            # image would overwrite its first.
            src = Path(local_path)
            dest = src.with_name(f"{src.stem}{name_suffix}{src.suffix}")
            src.replace(dest)
            return str(dest)
        return local_path
    except ComfyUIError as e:
        raise ImageGenError(
            f"All image providers failed (cloud + local ComfyUI). ComfyUI error: {e}. "
            f"Last cloud error: {last_error}"
        )
