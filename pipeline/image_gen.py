"""
Per-scene AI image generator — design-v3 pivot.

Fallback chain: HuggingFace (FLUX.1-schnell, fixed seed)
              → Google AI Studio (Gemini image generation, reference-image)
              → Pollinations (fixed seed, zero-friction)

Usage:
    from pipeline.image_gen import generate_scene_image
    path = generate_scene_image(
        image_prompt="A hero stands at the gates of Troy",
        art_style_suffix="epic mythological digital painting, dramatic lighting",
        seed=42,
        output_dir="data/images",
        scene_index=0,
        video_id=1,
        cfg=cfg,
    )
"""

import base64
import logging
import time
from pathlib import Path
from urllib.parse import quote as url_quote

import requests

log = logging.getLogger(__name__)

_HF_API_URL = "https://api-inference.huggingface.co/models/black-forest-labs/FLUX.1-schnell"
# Target size closest to 9:16 that FLUX supports (multiples of 64)
_HF_WIDTH  = 768
_HF_HEIGHT = 1344


def _build_prompt(image_prompt: str, art_style_suffix: str) -> str:
    return f"{image_prompt}, {art_style_suffix}" if art_style_suffix else image_prompt


def _hf_generate(prompt: str, seed: int, hf_token: str) -> bytes | None:
    try:
        r = requests.post(
            _HF_API_URL,
            headers={"Authorization": f"Bearer {hf_token}"},
            json={
                "inputs": prompt,
                "parameters": {
                    "seed": seed,
                    "width": _HF_WIDTH,
                    "height": _HF_HEIGHT,
                    "num_inference_steps": 4,   # schnell is optimised for 1-4 steps
                },
            },
            timeout=90,
        )
        ct = r.headers.get("content-type", "")
        if r.status_code == 200 and ct.startswith("image"):
            return r.content
        log.warning("HF: status=%d body=%s", r.status_code, r.text[:200])
    except Exception as e:
        log.warning("HF generate failed: %s", e)
    return None


def _gemini_generate(
    prompt: str,
    api_key: str,
    model: str,
    reference_image_b64: str | None = None,
) -> bytes | None:
    """
    Google AI Studio image generation via REST.
    Uses reference-image conditioning when reference_image_b64 is provided.
    """
    try:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={api_key}"
        )

        if reference_image_b64:
            parts = [
                {"text": "Generate a new image for this scene in the exact same art style as the reference:"},
                {"inline_data": {"mime_type": "image/png", "data": reference_image_b64}},
                {"text": prompt},
            ]
        else:
            parts = [{"text": f"Generate an image: {prompt}"}]

        body = {
            "contents": [{"parts": parts}],
            "generationConfig": {"responseModalities": ["IMAGE"]},
        }

        r = requests.post(url, json=body, timeout=120)
        if r.status_code != 200:
            log.warning("Gemini image: status=%d body=%s", r.status_code, r.text[:200])
            return None

        data = r.json()
        for candidate in data.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                if "inlineData" in part:
                    return base64.b64decode(part["inlineData"]["data"])

        log.warning("Gemini image: no inlineData in response")
    except Exception as e:
        log.warning("Gemini generate failed: %s", e)
    return None


def _pollinations_generate(prompt: str, seed: int, width: int = 1080, height: int = 1920) -> bytes | None:
    try:
        url = (
            f"https://image.pollinations.ai/prompt/{url_quote(prompt)}"
            f"?seed={seed}&width={width}&height={height}&nologo=true&model=flux"
        )
        r = requests.get(url, timeout=120)
        ct = r.headers.get("content-type", "")
        if r.status_code == 200 and ct.startswith("image"):
            return r.content
        log.warning("Pollinations: status=%d", r.status_code)
    except Exception as e:
        log.warning("Pollinations failed: %s", e)
    return None


def _save_image(data: bytes, dest: Path) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return str(dest)


def generate_scene_image(
    image_prompt: str,
    art_style_suffix: str,
    seed: int,
    output_dir: str,
    scene_index: int,
    video_id: int,
    cfg=None,
    reference_image_path: str | None = None,
) -> str:
    """
    Generate one scene image. Returns path to saved PNG.
    Tries providers in priority order from settings.json (image_provider.priority).

    Args:
        image_prompt:         Scene visual description from script_gen.
        art_style_suffix:     Niche art style string appended to every prompt.
        seed:                 Fixed seed for this video (ensures style consistency
                              across scenes for HF and Pollinations).
        output_dir:           Directory to save images.
        scene_index:          0-based scene number (used in filename).
        video_id:             DB video id (used in filename).
        cfg:                  Config singleton.
        reference_image_path: Path to first scene's image for Gemini conditioning.
                              Only used when provider is google_ai_studio.

    Raises:
        RuntimeError if all providers fail.
    """
    full_prompt = _build_prompt(image_prompt, art_style_suffix)
    dest = Path(output_dir) / f"scene_{video_id}_{scene_index}_{int(time.time())}.png"

    # Defaults — overridden by cfg
    hf_token        = ""
    gemini_key      = ""
    gemini_model    = "gemini-2.0-flash-preview-image-generation"
    provider_priority = ["huggingface", "google_ai_studio", "pollinations"]

    if cfg:
        hf_token   = getattr(cfg, "HF_API_TOKEN", "")
        gemini_key = getattr(cfg, "GOOGLE_AI_STUDIO_API_KEY", "")
        ip = getattr(cfg, "image_provider", {})
        provider_priority = ip.get("priority", provider_priority)
        gemini_model      = ip.get("google_ai_studio", {}).get("model", gemini_model)

    # Encode reference image for Gemini conditioning
    ref_b64: str | None = None
    if reference_image_path and Path(reference_image_path).exists():
        ref_b64 = base64.b64encode(Path(reference_image_path).read_bytes()).decode()

    for provider in provider_priority:
        image_data: bytes | None = None

        if provider == "huggingface" and hf_token:
            log.info("image_gen: HuggingFace scene=%d seed=%d", scene_index, seed)
            image_data = _hf_generate(full_prompt, seed, hf_token)

        elif provider == "google_ai_studio" and gemini_key:
            log.info("image_gen: Google AI Studio scene=%d", scene_index)
            image_data = _gemini_generate(full_prompt, gemini_key, gemini_model, ref_b64)

        elif provider == "pollinations":
            log.info("image_gen: Pollinations scene=%d seed=%d", scene_index, seed)
            image_data = _pollinations_generate(full_prompt, seed)

        if image_data:
            path = _save_image(image_data, dest)
            log.info("Image saved: %s (%d bytes)", path, len(image_data))
            return path

        log.warning("image_gen: provider=%s failed for scene=%d", provider, scene_index)

    raise RuntimeError(
        f"All image providers failed for scene {scene_index}. "
        f"prompt={full_prompt[:80]!r}"
    )
