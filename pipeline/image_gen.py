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
from pathlib import Path
from urllib.parse import quote

import requests

from pipeline.image_policy import (
    LocalGenerationBlocked,
    apply_no_human_policy,
    local_generation_allowed,
)

log = logging.getLogger(__name__)

_ROOT = Path(__file__).parent.parent
_KEYS_FILE = _ROOT / "image_keys.json"

# Fallback env var per provider, used when image_keys.json has no key for it.
_PROVIDER_ENV_MAP = {
    "gemini":       "GOOGLE_AI_STUDIO_API_KEY",
    "together_ai":  "TOGETHERAI_API_KEY",
    "huggingface":  "HUGGINGFACE_API_KEY",
    "pollinations": None,
}

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
}


class ImageGenError(RuntimeError):
    """Raised when every image generation provider fails."""


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
            env_var = _PROVIDER_ENV_MAP.get(entry.get("type", name))
            if env_var:
                api_key = os.getenv(env_var, "").strip()

        if not api_key and not entry.get("keyless", False):
            continue

        providers.append({
            **entry,
            "name": name,
            "type": entry.get("type", name),
            "api_key": api_key,
        })

    return providers


# ── Provider handlers: (provider, prompt, negative, width, height, timeout) -> bytes ──

def _gen_pollinations(provider, prompt, negative, width, height, timeout) -> bytes:
    model = provider.get("model", "flux")
    url = f"https://image.pollinations.ai/prompt/{quote(prompt, safe='')}"
    params = {"width": width, "height": height, "model": model, "nologo": "true"}
    headers = {}
    if provider["api_key"]:
        headers["Authorization"] = f"Bearer {provider['api_key']}"
    resp = requests.get(url, params=params, headers=headers, timeout=timeout)
    resp.raise_for_status()
    if not resp.content:
        raise ImageGenError("pollinations returned empty body")
    return resp.content


def _gen_huggingface(provider, prompt, negative, width, height, timeout) -> bytes:
    model = provider.get("model", "black-forest-labs/FLUX.1-schnell")
    url = f"https://api-inference.huggingface.co/models/{model}"
    payload = {
        "inputs": prompt,
        "parameters": {"negative_prompt": negative, "width": width, "height": height},
    }
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {provider['api_key']}"},
        json=payload,
        timeout=timeout,
    )
    resp.raise_for_status()
    if resp.headers.get("content-type", "").startswith("application/json"):
        raise ImageGenError(f"huggingface returned JSON, not an image: {resp.text[:200]}")
    return resp.content


def _gen_together(provider, prompt, negative, width, height, timeout) -> bytes:
    model = provider.get("model", "black-forest-labs/FLUX.1-schnell-Free")
    payload = {
        "model": model,
        "prompt": prompt,
        "negative_prompt": negative,
        "width": width,
        "height": height,
        "steps": provider.get("steps", 4),
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


def _gen_gemini(provider, prompt, negative, width, height, timeout) -> bytes:
    model = provider.get("model", "gemini-2.5-flash-image")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    full_prompt = f"{prompt}. Avoid: {negative}." if negative else prompt
    payload = {"contents": [{"parts": [{"text": full_prompt}]}]}
    resp = requests.post(
        url,
        headers={"x-goog-api-key": provider["api_key"], "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    resp.raise_for_status()
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


def generate_image(
    image_prompt: str,
    niche: dict,
    output_dir: str,
    scene_index: int,
    cfg=None,
    local_only: bool = False,
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

    Returns:
        Path to saved image (output_dir/scene_XX.<png|jpg|webp>, extension follows
        the bytes the provider actually returned).

    Raises:
        ImageGenError if every provider fails.
        LocalGenerationBlocked if local generation is needed but the niche forbids it.
    """
    config = _get_config(cfg)
    width = config["width"]
    height = config["height"]
    timeout = config["timeout_seconds"]

    art_style = niche.get("art_style_prompt_suffix", "")
    positive = f"{image_prompt}, {art_style}" if art_style else image_prompt
    negative = config["negative_prompt"]

    if config.get("no_humans", True):
        positive, negative = apply_no_human_policy(positive, negative)

    output_dir_path = Path(output_dir)
    last_error: Exception | None = None

    # ── Cloud providers ──────────────────────────────────────────────────────
    if not local_only:
        providers = _load_providers()
        if not providers:
            log.warning("No cloud image providers configured (see image_keys.example.json)")

        for provider in providers:
            handler = _HANDLERS.get(provider["type"])
            if handler is None:
                log.warning("Unknown image provider type: %r", provider["type"])
                continue
            try:
                log.info("Image gen: provider=%s model=%s scene=%d",
                         provider["name"], provider.get("model", "-"), scene_index)
                data = handler(provider, positive, negative, width, height, timeout)
                output_path = output_dir_path / f"scene_{scene_index:02d}{_sniff_extension(data)}"
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(data)
                log.info("Image gen success: %s (%d bytes, provider=%s)",
                         output_path, len(data), provider["name"])
                return str(output_path)
            except Exception as e:
                log.warning("Image provider %s failed: %s", provider["name"], e)
                last_error = e

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
        return generate_comfyui_image(
            image_prompt=image_prompt,
            niche=niche,
            output_dir=output_dir,
            scene_index=scene_index,
            cfg=cfg,
        )
    except ComfyUIError as e:
        raise ImageGenError(
            f"All image providers failed (cloud + local ComfyUI). ComfyUI error: {e}. "
            f"Last cloud error: {last_error}"
        )
