"""
Per-scene AI image generator — design-v3 pivot.

Fallback chain defined in quota.json (fallback_chains.image_generation):
    HuggingFace (FLUX.1-schnell, fixed seed)
  → Google AI Studio (Gemini image generation, reference-image)
  → Pollinations (fixed seed, zero-friction)

Quota is checked before each provider call (pre-call) and logged after (post-call).
If all providers are quota-capped (HTTP 429), raises QuotaExhaustedError.
Other failures (500, timeout) fall through to the next provider normally.

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
        conn=conn,
    )
"""

import base64
import logging
import sqlite3
import time
from pathlib import Path
from urllib.parse import quote as url_quote

import requests

from pipeline.quota_tracker import check_and_log_quota, load_quota_config

log = logging.getLogger(__name__)

_HF_API_URL = "https://api-inference.huggingface.co/models/black-forest-labs/FLUX.1-schnell"


class QuotaExhaustedError(RuntimeError):
    """All image providers hit their daily quota limit (HTTP 429)."""


def _build_prompt(image_prompt: str, art_style_suffix: str) -> str:
    return f"{image_prompt}, {art_style_suffix}" if art_style_suffix else image_prompt


def _hf_generate(
    prompt: str,
    seed: int,
    hf_token: str,
    width: int = 768,
    height: int = 1344,
    num_inference_steps: int = 4,
) -> tuple[bytes | None, int | None]:
    """Returns (image_bytes, error_code). error_code is None on success."""
    try:
        r = requests.post(
            _HF_API_URL,
            headers={"Authorization": f"Bearer {hf_token}"},
            json={
                "inputs": prompt,
                "parameters": {
                    "seed": seed,
                    "width": width,
                    "height": height,
                    "num_inference_steps": num_inference_steps,
                },
            },
            timeout=90,
        )
        ct = r.headers.get("content-type", "")
        if r.status_code == 200 and ct.startswith("image"):
            return r.content, None
        log.warning("HF: status=%d body=%s", r.status_code, r.text[:200])
        return None, r.status_code
    except requests.Timeout:
        log.warning("HF generate: timeout")
        return None, None  # timeout — don't count against quota
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else None
        log.warning("HF generate: HTTPError %s", code)
        return None, code
    except Exception as e:
        log.warning("HF generate failed: %s", e)
        return None, None


def _gemini_generate(
    prompt: str,
    api_key: str,
    model: str,
    reference_image_b64: str | None = None,
) -> tuple[bytes | None, int | None]:
    """
    Google AI Studio image generation via REST.
    Uses reference-image conditioning when reference_image_b64 is provided.
    Returns (image_bytes, error_code).
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
            return None, r.status_code

        data = r.json()
        for candidate in data.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                if "inlineData" in part:
                    return base64.b64decode(part["inlineData"]["data"]), None

        log.warning("Gemini image: no inlineData in response")
        return None, None
    except requests.Timeout:
        log.warning("Gemini generate: timeout")
        return None, None
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else None
        log.warning("Gemini generate: HTTPError %s", code)
        return None, code
    except Exception as e:
        log.warning("Gemini generate failed: %s", e)
        return None, None


def _pollinations_generate(prompt: str, seed: int, width: int = 1080, height: int = 1920) -> tuple[bytes | None, int | None]:
    try:
        url = (
            f"https://image.pollinations.ai/prompt/{url_quote(prompt)}"
            f"?seed={seed}&width={width}&height={height}&nologo=true&model=flux"
        )
        r = requests.get(url, timeout=120)
        ct = r.headers.get("content-type", "")
        if r.status_code == 200 and ct.startswith("image"):
            return r.content, None
        log.warning("Pollinations: status=%d", r.status_code)
        return None, r.status_code
    except requests.Timeout:
        log.warning("Pollinations: timeout")
        return None, None
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else None
        log.warning("Pollinations: HTTPError %s", code)
        return None, code
    except Exception as e:
        log.warning("Pollinations failed: %s", e)
        return None, None


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
    conn: sqlite3.Connection | None = None,
    reference_image_path: str | None = None,
) -> str:
    """
    Generate one scene image. Returns path to saved PNG.

    Reads provider fallback order from quota.json (fallback_chains.image_generation).
    Checks quota before each provider call; logs result after.

    Args:
        image_prompt:         Scene visual description from script_gen.
        art_style_suffix:     Niche art style string appended to every prompt.
        seed:                 Fixed seed for this video (ensures style consistency
                              across scenes for HF and Pollinations).
        output_dir:           Directory to save images.
        scene_index:          0-based scene number (used in filename).
        video_id:             DB video id (used in filename).
        cfg:                  Config singleton.
        conn:                 SQLite connection for quota tracking. If None, quota
                              checks are skipped (dry-run / test usage).
        reference_image_path: Path to first scene's image for Gemini conditioning.

    Raises:
        QuotaExhaustedError: All providers hit 429 (quota cap). Caller should set
                             video status to 'waiting_quota'.
        RuntimeError:        All providers failed for non-quota reasons.
    """
    full_prompt = _build_prompt(image_prompt, art_style_suffix)
    dest = Path(output_dir) / f"scene_{video_id}_{scene_index}_{int(time.time())}.png"

    # Defaults — overridden by cfg
    hf_token     = ""
    gemini_key   = ""
    gemini_model = "gemini-2.0-flash-preview-image-generation"

    if cfg:
        hf_token     = getattr(cfg, "HF_API_TOKEN", "")
        gemini_key   = getattr(cfg, "GOOGLE_AI_STUDIO_API_KEY", "")
        ip           = getattr(cfg, "image_provider", {})
        gemini_model = ip.get("google_ai_studio", {}).get("model", gemini_model)

    # Read image gen dimensions from cfg.image_gen (Task 1)
    ig = getattr(cfg, "image_gen", {}) if cfg else {}
    hf_cfg   = ig.get("huggingface", {})
    poll_cfg = ig.get("pollinations", {})
    hf_width  = hf_cfg.get("width",  768)
    hf_height = hf_cfg.get("height", 1344)
    hf_steps  = hf_cfg.get("num_inference_steps", 4)
    poll_width  = poll_cfg.get("width",  1080)
    poll_height = poll_cfg.get("height", 1920)

    # Fallback order from quota.json (single source of truth)
    quota_cfg         = load_quota_config()
    provider_priority = quota_cfg["fallback_chains"]["image_generation"]

    # Encode reference image for Gemini conditioning
    ref_b64: str | None = None
    if reference_image_path and Path(reference_image_path).exists():
        ref_b64 = base64.b64encode(Path(reference_image_path).read_bytes()).decode()

    quota_errors: list[int | None] = []   # error codes from quota-capped providers
    all_providers_attempted = []

    for provider in provider_priority:
        # ── Pre-call quota check ──────────────────────────────────────────────
        if conn is not None:
            can_proceed, reason = check_and_log_quota(provider, conn, check_only=True)
            if not can_proceed:
                log.info("image_gen: skipping %s — %s", provider, reason)
                quota_errors.append(429)
                all_providers_attempted.append(provider)
                continue

        image_data: bytes | None = None
        error_code: int | None = None

        # ── Provider call ─────────────────────────────────────────────────────
        if provider == "huggingface" and hf_token:
            log.info("image_gen: HuggingFace scene=%d seed=%d %dx%d steps=%d",
                     scene_index, seed, hf_width, hf_height, hf_steps)
            image_data, error_code = _hf_generate(
                full_prompt, seed, hf_token,
                width=hf_width, height=hf_height, num_inference_steps=hf_steps,
            )

        elif provider == "google_ai_studio" and gemini_key:
            log.info("image_gen: Google AI Studio scene=%d", scene_index)
            image_data, error_code = _gemini_generate(full_prompt, gemini_key, gemini_model, ref_b64)

        elif provider == "pollinations":
            log.info("image_gen: Pollinations scene=%d seed=%d %dx%d",
                     scene_index, seed, poll_width, poll_height)
            image_data, error_code = _pollinations_generate(
                full_prompt, seed, width=poll_width, height=poll_height
            )

        else:
            log.debug("image_gen: skipping provider=%s (no credentials or unknown)", provider)
            continue

        all_providers_attempted.append(provider)

        # ── Post-call quota log ───────────────────────────────────────────────
        if conn is not None:
            check_and_log_quota(
                provider, conn,
                success=image_data is not None,
                error_code=error_code,
            )

        if image_data:
            path = _save_image(image_data, dest)
            log.info("Image saved: %s (%d bytes)", path, len(image_data))
            return path

        quota_errors.append(error_code)
        log.warning("image_gen: provider=%s failed error_code=%s", provider, error_code)

    # ── All providers failed ──────────────────────────────────────────────────
    # If every failure was a 429, raise QuotaExhaustedError so caller can set waiting_quota
    if all_providers_attempted and all(e == 429 for e in quota_errors):
        raise QuotaExhaustedError(
            f"All image providers quota-capped for scene {scene_index}. "
            f"providers={all_providers_attempted}"
        )

    raise RuntimeError(
        f"All image providers failed for scene {scene_index}. "
        f"prompt={full_prompt[:80]!r} errors={quota_errors}"
    )
