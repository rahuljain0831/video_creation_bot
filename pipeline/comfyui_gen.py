"""
ComfyUI local image generation.

Chain: SD1.5 checkpoint (512x768) -> ADetailer face/hand fix -> Real-ESRGAN upscale.
Communicates with ComfyUI via its REST API (default http://127.0.0.1:8188).

Same input/output contract as get_library_image / get_pexels_image:
    generate_comfyui_image(image_prompt, niche, output_dir, scene_index, cfg) -> str (path)
"""

import json
import logging
import random
import shutil
import time
from pathlib import Path
from urllib.parse import urlencode

import requests

from pipeline.image_policy import apply_no_human_policy, assert_local_generation_allowed

log = logging.getLogger(__name__)

_ROOT = Path(__file__).parent.parent


class ComfyUIError(RuntimeError):
    """Raised on ComfyUI connection or generation failure."""


def _get_config(cfg) -> dict:
    """Read comfyui config, with defaults."""
    defaults = {
        "base_url": "http://127.0.0.1:8188",
        "workflow_path": "config/comfyui_workflow.json",
        "default_checkpoint": "realisticVisionV51.safetensors",
        "steps": 25,
        "cfg_scale": 7,
        "width": 512,
        "height": 768,
        "timeout_seconds": 120,
        "negative_prompt": (
            "deformed, blurry, bad anatomy, disfigured, poorly drawn face, "
            "mutation, extra limb, ugly, poorly drawn hands, missing limb, "
            "long neck, out of frame"
        ),
    }
    if cfg and hasattr(cfg, "comfyui"):
        for k, v in cfg.comfyui.items():
            defaults[k] = v
    return defaults


def _health_check(base_url: str) -> bool:
    """Check if ComfyUI is running."""
    try:
        resp = requests.get(f"{base_url}/system_stats", timeout=5)
        return resp.status_code == 200
    except Exception:
        return False


def _load_workflow(workflow_path: str) -> dict:
    """Load and return the workflow template."""
    path = _ROOT / workflow_path
    if not path.exists():
        raise ComfyUIError(f"Workflow template not found: {path}")
    with open(path) as f:
        return json.load(f)


def _prepare_workflow(
    workflow: dict,
    positive_prompt: str,
    negative_prompt: str,
    checkpoint: str,
    steps: int,
    cfg_scale: int,
    width: int,
    height: int,
) -> dict:
    """
    Replace placeholder tokens in workflow with actual values.
    Returns a deep copy with substitutions applied.
    """
    raw = json.dumps(workflow)

    replacements = {
        '"{steps}"': str(steps),
        '"{cfg_scale}"': str(cfg_scale),
        '"{width}"': str(width),
        '"{height}"': str(height),
        "{positive_prompt}": positive_prompt.replace('"', '\\"'),
        "{negative_prompt}": negative_prompt.replace('"', '\\"'),
        "{checkpoint}": checkpoint,
    }

    for token, value in replacements.items():
        raw = raw.replace(token, value)

    # Parse, then fix seed (integer field, can't do string replacement)
    result = json.loads(raw)
    for node_id, node in result.items():
        inputs = node.get("inputs", {})
        if "seed" in inputs and inputs["seed"] == -1:
            inputs["seed"] = random.randint(1, 2**32 - 1)

    return result


def _queue_prompt(base_url: str, workflow: dict) -> str:
    """Queue a prompt on ComfyUI, return prompt_id."""
    payload = {"prompt": workflow}
    try:
        resp = requests.post(f"{base_url}/prompt", json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise ComfyUIError(f"No prompt_id in response: {data}")
        return prompt_id
    except requests.RequestException as e:
        raise ComfyUIError(f"Failed to queue prompt: {e}")


def _poll_completion(base_url: str, prompt_id: str, timeout: int) -> dict:
    """Poll until prompt completes or timeout. Returns history entry."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = requests.get(f"{base_url}/history/{prompt_id}", timeout=5)
            if resp.status_code == 200:
                history = resp.json()
                if prompt_id in history:
                    entry = history[prompt_id]
                    status = entry.get("status", {})
                    if status.get("completed", False) or status.get("status_str") == "success":
                        return entry
                    if status.get("status_str") == "error":
                        raise ComfyUIError(f"ComfyUI generation error: {status}")
        except requests.RequestException:
            pass
        time.sleep(2)

    raise ComfyUIError(f"ComfyUI generation timed out after {timeout}s (prompt_id={prompt_id})")


def _download_image(base_url: str, history_entry: dict, output_path: Path) -> None:
    """Find output image in history and download it."""
    outputs = history_entry.get("outputs", {})

    # Find the SaveImage node output
    for node_id, node_output in outputs.items():
        images = node_output.get("images", [])
        if images:
            img = images[0]
            filename = img.get("filename")
            subfolder = img.get("subfolder", "")
            img_type = img.get("type", "output")

            params = urlencode({"filename": filename, "subfolder": subfolder, "type": img_type})
            url = f"{base_url}/view?{params}"

            try:
                resp = requests.get(url, timeout=30)
                resp.raise_for_status()
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, "wb") as f:
                    f.write(resp.content)
                log.info("ComfyUI image saved: %s (%d bytes)", output_path, len(resp.content))
                return
            except requests.RequestException as e:
                raise ComfyUIError(f"Failed to download image: {e}")

    raise ComfyUIError("No output images found in ComfyUI history")


def generate_comfyui_image(
    image_prompt: str,
    niche: dict,
    output_dir: str,
    scene_index: int,
    cfg=None,
) -> str:
    """
    Generate an image via local ComfyUI API.

    Chain: SD1.5 512x768 -> ADetailer face/hand fix -> Real-ESRGAN upscale.

    Args:
        image_prompt: Visual description for the scene.
        niche:        Niche config dict.
        output_dir:   Directory to save the image.
        scene_index:  Scene number (0-based).
        cfg:          Config singleton.

    Returns:
        Path to saved image (output_dir/scene_XX.png).

    Raises:
        ComfyUIError on failure.
        LocalGenerationBlocked if the niche forbids locally generated images.
    """
    # Policy: mythology-type niches never use locally generated images.
    assert_local_generation_allowed(niche)

    config = _get_config(cfg)
    base_url = config["base_url"]

    if not _health_check(base_url):
        raise ComfyUIError(f"ComfyUI not running at {base_url}")

    # Build prompts
    art_style = niche.get("art_style_prompt_suffix", "")
    positive = f"{image_prompt}, {art_style}" if art_style else image_prompt
    negative = config["negative_prompt"]

    # Policy: local output never contains human figures (distorted hands/faces).
    # A niche whose subject *is* a human figure (deities) opts out via no_humans: false.
    if niche.get("no_humans", config.get("no_humans", True)):
        positive, negative = apply_no_human_policy(positive, negative)

    # Per-niche checkpoint override
    checkpoint = niche.get("comfyui_checkpoint", config["default_checkpoint"])

    # Load and prepare workflow
    workflow = _load_workflow(config["workflow_path"])
    workflow = _prepare_workflow(
        workflow,
        positive_prompt=positive,
        negative_prompt=negative,
        checkpoint=checkpoint,
        steps=config["steps"],
        cfg_scale=config["cfg_scale"],
        width=config["width"],
        height=config["height"],
    )

    # Queue and wait
    t0 = time.monotonic()
    prompt_id = _queue_prompt(base_url, workflow)
    log.info("ComfyUI queued: prompt_id=%s scene=%d", prompt_id, scene_index)

    history = _poll_completion(base_url, prompt_id, timeout=config["timeout_seconds"])
    elapsed = time.monotonic() - t0
    log.info("ComfyUI generated in %.1fs", elapsed)

    # Download
    output_path = Path(output_dir) / f"scene_{scene_index:02d}.png"
    _download_image(base_url, history, output_path)

    return str(output_path)
