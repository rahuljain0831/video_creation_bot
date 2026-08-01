"""
Social media caption generator.

Single LLM call → JSON with captions + hashtags for 6 platforms.
Returns {} on any failure — caller must handle gracefully.
"""
import json
import logging
import re

from llm_router import call_llm

log = logging.getLogger(__name__)

_PLATFORMS = ("youtube", "instagram", "facebook", "tiktok", "pinterest", "linkedin")

_PLATFORM_SPECS = {
    "youtube":   "SEO-optimized description 200-300 chars, 3-5 hashtags",
    "instagram": "casual/engaging 100-150 chars, 15-20 hashtags",
    "facebook":  "conversational 150-200 chars, 3-5 hashtags",
    "tiktok":    "punchy/trend-aware 80-100 chars, 5-10 hashtags",
    "pinterest": "descriptive/searchable 100-150 chars, 5-8 hashtags",
    "linkedin":  "professional/educational tone 150-200 chars, 3-5 hashtags",
}

_EMOJI = {
    "youtube":   "▶️ YouTube",
    "instagram": "📸 Instagram",
    "facebook":  "👍 Facebook",
    "tiktok":    "🎵 TikTok",
    "pinterest": "📌 Pinterest",
    "linkedin":  "💼 LinkedIn",
}


def _extract_json(text: str) -> dict:
    """Extract first JSON object from LLM response — mirrors script_gen pattern."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
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
    raise ValueError(f"No valid JSON in LLM response: {text[:200]}")


def generate_social_captions(script: dict, niche: dict, cfg=None) -> dict:
    """
    Generate platform captions and hashtags for a video script.

    Args:
        script: generate_script() output — needs story_title + scenes[narration]
        niche:  niche config dict (uses tone key)
        cfg:    config singleton for llm_router settings

    Returns:
        {"youtube": {"caption": str, "hashtags": [str]}, ...} for all 6 platforms.
        Returns {} on any failure.
    """
    cfg_router = cfg.llm_router if cfg else {}
    story_title = script.get("story_title", "Untitled")
    narration = " ".join(s["narration"] for s in script.get("scenes", []))
    tone = niche.get("tone", "engaging")

    platform_instructions = "\n".join(
        f'  "{p}": {spec}' for p, spec in _PLATFORM_SPECS.items()
    )

    prompt = f"""You are a social media copywriter. Write captions and hashtags for this video.

Title: {story_title}
Tone: {tone}
Script summary: {narration[:500]}

Write captions for these platforms with these requirements:
{platform_instructions}

Respond with ONLY valid JSON, no markdown fences:
{{
  "youtube":   {{"caption": "...", "hashtags": ["#tag1", "#tag2"]}},
  "instagram": {{"caption": "...", "hashtags": ["#tag1", ...]}},
  "facebook":  {{"caption": "...", "hashtags": ["#tag1", ...]}},
  "tiktok":    {{"caption": "...", "hashtags": ["#tag1", ...]}},
  "pinterest": {{"caption": "...", "hashtags": ["#tag1", ...]}},
  "linkedin":  {{"caption": "...", "hashtags": ["#tag1", ...]}}
}}"""

    try:
        raw, model_used = call_llm(prompt, cfg_router=cfg_router, temperature=0.7)
        data = _extract_json(raw)
    except Exception as e:
        log.warning("social_captions: LLM call or parse failed: %s", e)
        return {}

    result = {}
    for platform in _PLATFORMS:
        entry = data.get(platform, {})
        caption = str(entry.get("caption", "")).strip()
        hashtags = entry.get("hashtags", [])
        if not isinstance(hashtags, list):
            hashtags = []
        if caption:
            result[platform] = {"caption": caption, "hashtags": hashtags}

    if len(result) != len(_PLATFORMS):
        log.warning(
            "social_captions: incomplete response (%d/%d platforms). raw=%s",
            len(result), len(_PLATFORMS), raw[:200],
        )
        return {}

    log.info("social_captions: generated for %d platforms using %s", len(result), model_used)
    return result


def format_telegram_message(story_title: str, captions: dict) -> str:
    """Format all platform captions into a single Telegram-ready text message."""
    lines = [f"📢 Social Media Captions — {story_title}", ""]
    for platform in _PLATFORMS:
        if platform not in captions:
            continue
        data = captions[platform]
        header = _EMOJI.get(platform, platform.title())
        caption = data.get("caption", "")
        hashtags = " ".join(data.get("hashtags", []))
        lines.append(header)
        lines.append(caption)
        if hashtags:
            lines.append(hashtags)
        lines.append("")
    return "\n".join(lines).strip()
