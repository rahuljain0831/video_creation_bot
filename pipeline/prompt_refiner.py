"""
Prompt refiner — post-process LLM-generated image prompts for better
image retrieval (Pexels/FTS) or image generation (SD/ComfyUI).

Wrapper-only: same scene list in, same scene list out.
Original prompts preserved in 'image_prompt_original'.
"""

import json
import logging
import re

log = logging.getLogger(__name__)


def _build_system_prompt(target: str, niche: dict) -> str:
    """System prompt for the refinement LLM call."""
    art_style = niche.get("art_style_prompt_suffix", "")

    if target == "generation":
        return (
            "You refine image prompts for Stable Diffusion / AI image generation. "
            "Convert narrative descriptions into concrete, visual, model-ready prompts. "
            "Rules:\n"
            "- Replace conceptual/symbolic language with visible physical details\n"
            "- NEVER include humans, human figures, faces, hands, or body parts — "
            "generated anatomy distorts. Describe environments, objects, animals, "
            "landscapes, and abstract visuals instead\n"
            "- Add composition terms (close-up, wide shot, low angle, etc.)\n"
            "- Add lighting terms (dramatic lighting, golden hour, rim light, etc.)\n"
            "- Keep scene-specific content — what is unique about THIS scene\n"
            f"- Append this art style to every prompt: {art_style}\n"
            "- Each refined prompt: 1-3 sentences, under 200 characters\n"
            "- Respond with valid JSON only — no markdown fences"
        )
    else:  # target == "search"
        return (
            "You refine image prompts for stock photo search (Pexels) and text search (FTS). "
            "Convert narrative descriptions into concrete, keyword-rich search queries. "
            "Rules:\n"
            "- Replace abstract/symbolic language with visible, searchable subjects\n"
            "- Use nouns and adjectives that stock photo sites index well\n"
            "- Keep scene-specific content — what is unique about THIS scene\n"
            "- Each refined prompt: short phrase, 5-15 words, no full sentences\n"
            "- Respond with valid JSON only — no markdown fences"
        )


def _build_user_prompt(scenes: list[dict]) -> str:
    """User prompt with all scenes for batched refinement."""
    scene_data = []
    for i, scene in enumerate(scenes):
        scene_data.append({
            "scene_id": i,
            "narration": scene.get("narration", ""),
            "image_prompt": scene.get("image_prompt", ""),
        })

    return (
        "Refine each scene's image_prompt. The narration provides context for what "
        "the viewer hears — the image must match what's being said.\n\n"
        "Input:\n"
        f"{json.dumps(scene_data, indent=2)}\n\n"
        'Respond with: {"refined": [{"scene_id": 0, "image_prompt": "..."}, ...]}'
    )


def _extract_json(text: str) -> dict:
    """Extract first JSON object from LLM response."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    match = re.search(r"(\{.*\})", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    raise ValueError(f"No valid JSON in response: {text[:300]}")


def refine_image_prompts(
    scenes: list[dict],
    niche: dict,
    target: str = "search",
    cfg=None,
) -> list[dict]:
    """
    Refine each scene's image_prompt for better image retrieval/generation.

    Args:
        scenes: List of {"narration", "image_prompt"} dicts.
        niche:  Niche config dict from settings.json.
        target: "search" (Pexels/FTS) or "generation" (SD/ComfyUI).
        cfg:    Config singleton.

    Returns:
        New list with same structure. image_prompt values replaced,
        originals preserved in 'image_prompt_original'.
        On any failure, returns original scenes unchanged.
    """
    from llm_router import call_llm

    cfg_router = cfg.llm_router if cfg else {}

    system = _build_system_prompt(target, niche)
    user = _build_user_prompt(scenes)

    try:
        raw, model_used = call_llm(user, system=system, cfg_router=cfg_router, temperature=0.4)
        data = _extract_json(raw)
    except Exception as e:
        log.warning("prompt_refiner: LLM call failed (%s), returning original prompts", e)
        return scenes

    refined_list = data.get("refined", [])
    if not refined_list or not isinstance(refined_list, list):
        log.warning("prompt_refiner: unexpected response structure, returning originals")
        return scenes

    # Build lookup by scene_id
    refined_map = {}
    for item in refined_list:
        sid = item.get("scene_id")
        prompt = item.get("image_prompt", "").strip()
        if sid is not None and prompt:
            refined_map[int(sid)] = prompt

    # Apply refined prompts, preserving originals
    result = []
    applied = 0
    for i, scene in enumerate(scenes):
        new_scene = dict(scene)
        if i in refined_map:
            new_scene["image_prompt_original"] = scene.get("image_prompt", "")
            new_scene["image_prompt"] = refined_map[i]
            applied += 1
        result.append(new_scene)

    log.info(
        "prompt_refiner: refined %d/%d prompts (target=%s, model=%s)",
        applied, len(scenes), target, model_used if 'model_used' in dir() else "unknown",
    )

    return result
