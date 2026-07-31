"""
Script generator — design-v3 pivot (niche-driven).

Takes a niche config dict + optional story seed → produces a structured script with
8-15 scenes, each with narration + image_prompt.

Tone is NOT auto-detected — it comes from the niche config.
All decisions are logged to the `decisions` table BEFORE image lookup runs.
"""

import json
import logging
import re
import sqlite3

log = logging.getLogger(__name__)

_VALID_NICHE_IDS = {"mythology", "scary_stories", "heists"}


def _log_decision(
    conn: sqlite3.Connection,
    video_id: int,
    decision_point: str,
    chosen_option: str,
    reasoning: str,
    model_used: str,
) -> None:
    conn.execute(
        """INSERT INTO decisions (video_id, decision_point, chosen_option, reasoning)
           VALUES (?, ?, ?, ?)""",
        (video_id, decision_point, chosen_option, f"[{model_used}] {reasoning}"),
    )
    conn.commit()
    log.info("Decision logged: %s = %s", decision_point, chosen_option)


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

    raise ValueError(f"No valid JSON object found in LLM response: {text[:300]}")


def generate_script(
    niche: dict,
    story_seed: str,
    conn: sqlite3.Connection,
    video_id: int,
    cfg=None,
    myth_type: str | None = None,
    content_type: str = "story",
) -> dict:
    """
    Generate a full video script for a niche.

    Args:
        niche:        Niche config dict from settings.json.
        story_seed:   One-line story concept. Empty = LLM picks within the niche.
        conn:         SQLite connection.
        video_id:     DB video row id.
        cfg:          Config singleton.
        myth_type:    For mythology niche: "hindu" | "norse" | "egypt" | "greek".
                      Determines sub-label used in the prompt.
        content_type: "story" | "teachings" | "facts". Controls narrative format.

    Returns:
        {"niche_id", "story_title", "scene_count", "scenes": [{"narration", "image_prompt"}]}

    Raises:
        RuntimeError on LLM failure or bad JSON.
    """
    from llm_router import call_llm

    cfg_router  = cfg.llm_router if cfg else {}
    min_scenes  = cfg.video.get("min_scenes", 8)  if cfg else 8
    max_scenes  = cfg.video.get("max_scenes", 15) if cfg else 15

    niche_id    = niche.get("id", "unknown")
    niche_label = niche.get("label", niche_id)
    tone        = niche.get("tone", "dramatic")
    art_style   = niche.get("art_style_prompt_suffix", "")

    # Apply mythology sub-type label if provided
    if myth_type and niche_id == "mythology":
        sub = niche.get("sub_types", {}).get(myth_type, {})
        if sub:
            niche_label = sub.get("label", niche_label)
            tone        = sub.get("tone", tone)

    # Content format directive
    if content_type == "teachings":
        format_note = (
            "Format: philosophical teachings and wisdom. Each scene reveals a profound lesson "
            "or truth from the source material. Narration speaks directly to the viewer "
            "as if sharing ancient wisdom."
        )
    elif content_type == "facts":
        format_note = (
            "Format: fascinating facts and explanations. Each scene presents a surprising or "
            "illuminating piece of knowledge about the mythology. Educational but gripping."
        )
    else:
        format_note = (
            "Format: dramatic narrative story with rising tension and resolution."
        )

    story_directive = (
        f'Base the content on: "{story_seed}"'
        if story_seed.strip()
        else f"Choose an engaging, original {niche_label} topic."
    )

    system = (
        "You are a scriptwriter for short-form vertical social media story videos. "
        "You always respond with valid JSON only — no markdown fences, no extra text."
    )

    user_prompt = f"""Write a {niche_label} video script.

Tone: {tone}
{format_note}
{story_directive}
Art style (for reference, do NOT include in narration): {art_style}

Respond with exactly this JSON structure:
{{
  "story_title": "<compelling title, max 8 words>",
  "scenes": [
    {{
      "narration": "<what the narrator says for this scene, 1-2 punchy sentences, max 20 words each>",
      "image_prompt": "<detailed visual description for AI image generation, 1-3 sentences, no art-style words (added automatically). CRITICAL anatomy rules: every character has exactly ONE head unless the deity is specifically known for multiple heads (e.g. Brahma=4 heads, Shesha=many heads). For non-humanoid avatars describe correct animal anatomy (Kurma=single-headed giant tortoise, Matsya=fish, Varaha=boar-headed). For deities with multiple arms state the exact count (e.g. 'four arms'). Faces must be serene and clearly rendered. Specify the main subject prominently in the foreground.>"
    }}
  ]
}}

Use {min_scenes} to {max_scenes} scenes. Each scene is 3-8 seconds of screen time.
Build narrative tension across scenes. Final scene should resolve or land with impact.
Narration must be tight — total video is 45-90 seconds."""

    raw, model_used = call_llm(user_prompt, system=system, cfg_router=cfg_router, temperature=0.85)

    try:
        data = _extract_json(raw)
    except ValueError as e:
        raise RuntimeError(f"script_gen: JSON parse failed. model={model_used} error={e}")

    story_title = str(data.get("story_title", f"Untitled {niche_label} Story")).strip()
    scenes_raw  = data.get("scenes", [])

    if not scenes_raw or not isinstance(scenes_raw, list):
        raise RuntimeError(f"script_gen: no scenes in LLM response. raw={raw[:300]}")

    # ── Log decisions BEFORE returning (the guardrail) ───────────────────────
    _log_decision(
        conn, video_id,
        "script_generation",
        story_title,
        f"niche={niche_id} seed={story_seed[:60] or 'random'} scenes_requested={min_scenes}-{max_scenes}",
        model_used,
    )

    # ── Normalise scenes ─────────────────────────────────────────────────────
    scenes = []
    for s in scenes_raw[:max_scenes]:
        narration    = str(s.get("narration", "")).strip()
        image_prompt = str(s.get("image_prompt", "")).strip()

        if not narration or not image_prompt:
            log.warning("script_gen: skipping incomplete scene: %s", s)
            continue

        scenes.append({"narration": narration, "image_prompt": image_prompt})

    if not scenes:
        raise RuntimeError("script_gen: all scenes invalid after parsing")

    if len(scenes) < min_scenes:
        log.warning(
            "script_gen: only %d scenes returned (min=%d) — proceeding anyway",
            len(scenes), min_scenes,
        )

    scene_count = len(scenes)

    # ── Update videos row ────────────────────────────────────────────────────
    conn.execute(
        """UPDATE videos SET niche_id=?, scene_count=?, prompt=? WHERE id=?""",
        (niche_id, scene_count, story_seed or f"[random {niche_label}]", video_id),
    )
    conn.commit()

    log.info(
        "Script generated: video_id=%d niche=%s title=%r scenes=%d model=%s",
        video_id, niche_id, story_title, scene_count, model_used,
    )

    return {
        "niche_id":    niche_id,
        "story_title": story_title,
        "scene_count": scene_count,
        "scenes":      scenes,
    }
