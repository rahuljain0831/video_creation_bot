"""
Script generator — design-v3, Phase 1.

Takes a one-line user prompt → produces a structured script with:
  - tone (auto-detected)
  - ending_type (LLM-decided)
  - topic (inferred)
  - 1-2 scenes, each with video_prompt + narration + mood

GUARDRAIL: all decisions are logged to the `decisions` table BEFORE
scene_fetcher is ever called. This prevents quota spend on bad tone reads.
"""

import json
import logging
import re
import sqlite3

log = logging.getLogger(__name__)

_VALID_TONES = {"warm", "funny", "educational", "inspirational", "dramatic", "calming"}
_VALID_ENDINGS = {"punchline", "tip", "reveal", "other"}
_VALID_MOODS = {"calm", "energetic", "dramatic", "warm", "funny"}


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


def _extract_json(text: str) -> list | dict:
    """Extract first JSON object/array from LLM response."""
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Find JSON block
    match = re.search(r"```(?:json)?\s*([\[\{].*?[\]\}])\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Find raw JSON array or object
    match = re.search(r"([\[\{].*[\]\}])", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    raise ValueError(f"No valid JSON found in LLM response: {text[:300]}")


def generate_script(
    prompt: str,
    conn: sqlite3.Connection,
    video_id: int,
    cfg=None,
) -> dict:
    """
    Generate a full video script from a user prompt.

    Logs tone_detection, ending_choice, topic_inference decisions to DB
    BEFORE returning scenes (the guardrail).

    Returns:
        {
            "tone": str,
            "ending_type": str,
            "topic": str,
            "scene_count": int,
            "scenes": [
                {
                    "video_prompt": str,   # for video-gen API
                    "narration": str,      # for TTS
                    "mood": str,           # for clip selection
                    "search": str,         # alias for scene_fetcher compat
                    "description": str,    # alias for scene_fetcher compat
                }
            ]
        }
    """
    from llm_router import call_llm

    cfg_router = cfg.llm_router if cfg else {}
    min_scenes = cfg.video.get("min_scenes", 1) if cfg else 1
    max_scenes = cfg.video.get("max_scenes", 2) if cfg else 2

    # ── Single structured LLM call ────────────────────────────────────────────
    # All decisions captured in one call, logged individually to decisions table.
    # This is the guardrail: everything logged before scene_fetcher is called.

    system = (
        "You are a video script writer for short vertical social media videos (15-20 seconds). "
        "You always respond with valid JSON only — no extra text, no markdown fences."
    )

    scene_count_instruction = (
        f"Use {min_scenes} scene"
        if min_scenes == max_scenes
        else f"Use {min_scenes} or {max_scenes} scenes (choose based on complexity)"
    )

    user_prompt = f"""Analyze this video prompt and write a complete script.

Prompt: "{prompt}"

Respond with this exact JSON structure:
{{
  "tone": "<one of: warm|funny|educational|inspirational|dramatic|calming>",
  "tone_reasoning": "<one sentence why>",
  "ending_type": "<one of: punchline|tip|reveal|other>",
  "ending_reasoning": "<one sentence why>",
  "topic": "<2-4 word topic label>",
  "scenes": [
    {{
      "video_prompt": "<what the video should visually show, 1-2 sentences for AI video generation>",
      "narration": "<what narrator says, max 2 short sentences, must fit in 7-10 seconds>",
      "mood": "<one of: calm|energetic|dramatic|warm|funny>"
    }}
  ]
}}

{scene_count_instruction}. Keep narration tight — the whole video is 15-20 seconds."""

    raw, model_used = call_llm(user_prompt, system=system, cfg_router=cfg_router, temperature=0.8)

    # Parse response
    try:
        data = _extract_json(raw)
    except ValueError as e:
        raise RuntimeError(f"script_gen: JSON parse failed. model={model_used} error={e}")

    # Extract + validate fields
    tone = str(data.get("tone", "warm")).lower().strip()
    if tone not in _VALID_TONES:
        log.warning("Unexpected tone '%s', defaulting to 'warm'", tone)
        tone = "warm"

    ending_type = str(data.get("ending_type", "other")).lower().strip()
    if ending_type not in _VALID_ENDINGS:
        log.warning("Unexpected ending_type '%s', defaulting to 'other'", ending_type)
        ending_type = "other"

    topic = str(data.get("topic", prompt[:40])).strip()
    tone_reasoning = str(data.get("tone_reasoning", "")).strip()
    ending_reasoning = str(data.get("ending_reasoning", "")).strip()

    scenes_raw = data.get("scenes", [])
    if not scenes_raw or not isinstance(scenes_raw, list):
        raise RuntimeError(f"script_gen: no scenes in LLM response. raw={raw[:300]}")

    # ── Log all decisions BEFORE returning (the guardrail) ───────────────────
    _log_decision(conn, video_id, "tone_detection", tone, tone_reasoning, model_used)
    _log_decision(conn, video_id, "ending_choice", ending_type, ending_reasoning, model_used)
    _log_decision(conn, video_id, "topic_inference", topic, f"inferred from: {prompt}", model_used)

    # ── Normalise scenes ──────────────────────────────────────────────────────
    scenes = []
    for s in scenes_raw[:max_scenes]:
        video_prompt = str(s.get("video_prompt", "")).strip()
        narration    = str(s.get("narration", "")).strip()
        mood         = str(s.get("mood", "calm")).lower().strip()

        if mood not in _VALID_MOODS:
            mood = "calm"

        if not video_prompt or not narration:
            log.warning("Skipping incomplete scene: %s", s)
            continue

        scenes.append({
            "video_prompt": video_prompt,
            "narration":    narration,
            "mood":         mood,
            # scene_fetcher compat aliases
            "search":      video_prompt[:80],
            "description": video_prompt,
        })

    if not scenes:
        raise RuntimeError("script_gen: all scenes were invalid after parsing")

    scene_count = len(scenes)

    # ── Update videos row with script metadata ────────────────────────────────
    conn.execute(
        """UPDATE videos
           SET tone=?, ending_type=?, topic=?, scene_count=?, prompt=?
           WHERE id=?""",
        (tone, ending_type, topic, scene_count, prompt, video_id),
    )
    conn.commit()

    log.info(
        "Script generated: video_id=%d tone=%s ending=%s topic=%s scenes=%d model=%s",
        video_id, tone, ending_type, topic, scene_count, model_used,
    )

    return {
        "tone":        tone,
        "ending_type": ending_type,
        "topic":       topic,
        "scene_count": scene_count,
        "scenes":      scenes,
    }
