"""
Prompt refiner — post-process LLM-generated image prompts for better
image retrieval (Pexels/FTS) or image generation (SD/ComfyUI).

Wrapper-only: same scene list in, same scene list out.
Original prompts preserved in 'image_prompt_original'.

Also provides universal prompt enrichment via Ollama: generates rich
200-350 word cinematographer-style briefs usable in any AI image tool,
exported to output/scripts/{slug}_prompts.txt for manual browser use.
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path

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


_UNIVERSAL_SYSTEM = """\
You are a cinematographer and art director writing a detailed visual brief for an AI image generator.
Given a scene's narration and a short image prompt, write a RICH, DETAILED description (200-350 words) that any AI image model can use.

Include ALL of these in every brief:
- Camera: exact lens focal length, angle (low/eye-level/high/dutch), depth of field
- Lighting: quality (hard/soft/diffused), direction (front/side/back/rim), color temperature (warm/cool/neutral), practical sources visible
- Subject: exact position in frame (rule of thirds), body orientation (back to camera, silhouette, etc.), clothing texture and color, any props
- Atmosphere: weather, time of day, fog/haze/smoke density, ambient particle effects
- Color grading: dominant palette, shadow tones, highlight tones, saturation level
- Background: exact architectural or environmental detail, depth layers (foreground/midground/background)
- Texture: film grain level (fine/medium/heavy), lens flare, vignette strength
- Style: photorealistic, cinematic, high detail, 8K

RULES:
- NEVER describe faces clearly — if a human figure appears, they are seen from behind, in silhouette, or face hidden in shadow/hood
- NEVER describe hands in detail — keep them out of frame, gripping something off-frame, or hidden
- Write as continuous prose — NO bullet points, NO labels
- End with: "Photorealistic, cinematic, 8K, high detail. NO faces, NO visible hands."
- Output ONLY valid JSON: {"scene_id": N, "universal_prompt": "..."}
"""

_UNIVERSAL_USER_TMPL = """\
Scene {scene_id} of {total}

Narration (what the viewer hears):
{narration}

Short image prompt:
{image_prompt}

Write the full cinematographer brief for this scene.
"""


def enrich_for_universal(
    scenes: list[dict],
    niche: dict,
    cfg=None,
) -> list[dict]:
    """
    Enrich each scene's image_prompt into a rich 200-350 word universal brief
    via Ollama (local LLM). Stored in scene["image_prompt_universal"].

    Falls back gracefully: if Ollama is unreachable or fails, scenes are
    returned unchanged (image_prompt_universal simply absent).
    """
    from llm_router import call_llm

    cfg_router = cfg.llm_router if cfg else {}
    total = len(scenes)
    result = [dict(s) for s in scenes]
    enriched = 0

    for i, scene in enumerate(scenes):
        user = _UNIVERSAL_USER_TMPL.format(
            scene_id=i + 1,
            total=total,
            narration=scene.get("narration", "").strip(),
            image_prompt=scene.get("image_prompt", "").strip(),
        )
        try:
            raw, model_used = call_llm(
                user,
                system=_UNIVERSAL_SYSTEM,
                cfg_router=cfg_router,
                temperature=0.6,
            )
            data = _extract_json(raw)
            prompt = str(data.get("universal_prompt", "")).strip()
            if len(prompt) > 50:
                result[i]["image_prompt_universal"] = prompt
                enriched += 1
            else:
                log.warning("enrich_for_universal: scene %d got short response, skipping", i)
        except Exception as e:
            log.warning("enrich_for_universal: scene %d failed (%s), skipping", i, e)

    log.info("enrich_for_universal: enriched %d/%d scenes", enriched, total)
    return result


def export_prompts_as_text(
    scenes: list[dict],
    slug: str,
    niche: dict,
    cfg=None,
) -> str:
    """
    Write output/scripts/{slug}_prompts.txt with universal prompts ready to
    copy-paste into any browser-based AI image tool.

    Returns the path written.
    """
    scripts_dir = Path(cfg.paths["scripts"]) if cfg else Path("output/scripts")
    scripts_dir.mkdir(parents=True, exist_ok=True)
    out_path = scripts_dir / f"{slug}_prompts.txt"

    niche_label = niche.get("label", niche.get("id", "Unknown"))
    manual_dir = f"output/images/{slug}/manual/"

    lines: list[str] = [
        f"=== {niche_label.upper()} — {slug} ===",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}  |  {len(scenes)} scenes",
        "",
        f"Drop your generated images here: {manual_dir}",
        f"Name them: scene_00.png, scene_01.png, ... scene_{len(scenes)-1:02d}.png",
        "",
        "=" * 70,
        "",
    ]

    for i, scene in enumerate(scenes):
        template = scene.get("shot", scene.get("template", ""))
        lines.append(f"--- SCENE {i+1:02d}" + (f" ({template})" if template else "") + " ---")
        lines.append(f"Narration: {scene.get('narration', '').strip()}")
        lines.append("")

        universal = scene.get("image_prompt_universal", "").strip()
        short = scene.get("image_prompt", "").strip()

        if universal:
            lines.append("UNIVERSAL PROMPT (copy this into any AI tool):")
            lines.append(universal)
        else:
            lines.append("SHORT PROMPT (Ollama enrichment unavailable):")
            lines.append(short)

        lines.append("")
        lines.append(f"Save as: scene_{i:02d}.png  →  {manual_dir}scene_{i:02d}.png")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    log.info("Prompts exported: %s", out_path)
    return str(out_path)


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
