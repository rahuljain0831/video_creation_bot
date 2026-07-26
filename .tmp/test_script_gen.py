"""
Quote/Video Automation - Script Generation Test (design.md open question)

Tests whether a local Ollama model can reliably produce a 1-2 scene shot
breakdown WITH tone detection and ending reasoning, from a single-line prompt.

Run this on the laptop where Ollama is actually installed:
    python test_script_gen.py

Requires: pip install requests
"""

import json
import requests

OLLAMA_URL = "http://localhost:11434/api/generate"

# Models to compare - test both, per design.md Phase 1 decision
MODELS_TO_TEST = ["qwen3.5:2b", "llama3.1:8b"]

TEST_PROMPTS = [
    "two cats of different breeds cooking, food spills, they laugh",
    "how to care for a broken heart plant",
]

SYSTEM_PROMPT = """You are a short-form video script writer. Given a one-line \
video concept, produce a JSON shot breakdown for a 15-20 second video.

Rules:
- Output ONLY valid JSON, no preamble, no markdown fences.
- 1-2 scenes maximum. Each scene is a distinct shot, not a sub-beat.
- Detect the tone yourself from the prompt (e.g. comedic, wholesome, calm, \
informative). Do not ask the user to specify it.
- Decide how the video ends (punchline, tip, reveal, etc) based on what \
best fits the content - there is no fixed rule.
- For each scene, write a video-generation prompt suitable for an AI video \
model (Veo/Kling style): concrete visual description, camera framing, \
action, mood.
- Write a short narration/caption line per scene, matching the tone.

Output this exact JSON shape:
{
  "detected_tone": "<one or two words>",
  "tone_reasoning": "<one sentence explaining why>",
  "ending_type": "<punchline | tip | reveal | other>",
  "ending_reasoning": "<one sentence explaining the choice>",
  "scenes": [
    {
      "scene_number": 1,
      "duration_seconds": <int>,
      "video_gen_prompt": "<detailed prompt for Veo/Kling>",
      "narration": "<caption or voiceover line>"
    }
  ]
}
"""


def call_ollama(model, prompt, use_json_mode=False):
    payload = {
        "model": model,
        "system": SYSTEM_PROMPT,
        "prompt": f"Video concept: {prompt}",
        "stream": False,
    }
    if use_json_mode:
        payload["format"] = "json"
    resp = requests.post(OLLAMA_URL, json=payload, timeout=120)
    resp.raise_for_status()
    body = resp.json()
    if "response" not in body:
        raise RuntimeError(f"Unexpected Ollama response shape: {json.dumps(body)[:500]}")
    return body["response"]


def strip_markdown_fences(text):
    """Some models wrap JSON in ```json ... ``` even when told not to."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.endswith("```"):
            t = t.rsplit("```", 1)[0]
    return t.strip()


def evaluate(parsed, prompt):
    """Cheap automated checks - not a substitute for reading it yourself."""
    issues = []
    scenes = parsed.get("scenes", [])
    if not (1 <= len(scenes) <= 2):
        issues.append(f"scene count is {len(scenes)}, expected 1-2")
    total_duration = sum(s.get("duration_seconds", 0) for s in scenes)
    if not (15 <= total_duration <= 20):
        issues.append(f"total duration {total_duration}s, expected 15-20s")
    if not parsed.get("detected_tone"):
        issues.append("no tone detected")
    if not parsed.get("tone_reasoning"):
        issues.append("no tone reasoning given")
    if not parsed.get("ending_reasoning"):
        issues.append("no ending reasoning given")
    for i, s in enumerate(scenes):
        if len(s.get("video_gen_prompt", "")) < 30:
            issues.append(f"scene {i+1} video_gen_prompt looks too thin")
    return issues


def try_get_json(model, prompt):
    """Attempt plain generation first; retry with format=json if parsing fails."""
    for use_json_mode in (False, True):
        try:
            raw = call_ollama(model, prompt, use_json_mode=use_json_mode)
        except Exception as e:
            print(f"  ERROR calling model (json_mode={use_json_mode}): {e}")
            continue

        raw_len = len(raw or "")
        cleaned = strip_markdown_fences(raw)

        try:
            return json.loads(cleaned), raw
        except json.JSONDecodeError as e:
            print(f"  Parse failed (json_mode={use_json_mode}, raw_len={raw_len}): {e}")
            if raw_len == 0:
                print("  -> Ollama returned an empty string. Check: is the model name "
                      "exactly right (run `ollama list`)? Is Ollama actually running?")
            else:
                print(f"  Raw output was:\n  {raw[:800]}")
    return None, None


def check_model_available(model):
    try:
        resp = requests.get("http://localhost:11434/api/tags", timeout=10)
        resp.raise_for_status()
        names = [m["name"] for m in resp.json().get("models", [])]
        if model not in names:
            print(f"  WARNING: '{model}' not found in `ollama list`. Available: {names}")
            return False
        return True
    except Exception as e:
        print(f"  WARNING: could not reach Ollama at all ({e}). Is it running?")
        return False


def main():
    for model in MODELS_TO_TEST:
        print(f"\n{'='*70}\nMODEL: {model}\n{'='*70}")
        check_model_available(model)
        for prompt in TEST_PROMPTS:
            print(f"\n--- Prompt: \"{prompt}\" ---")

            parsed, raw = try_get_json(model, prompt)
            if parsed is None:
                print("  GAVE UP on this prompt/model combo after both attempts.")
                continue

            print(json.dumps(parsed, indent=2))

            issues = evaluate(parsed, prompt)
            if issues:
                print(f"\n  FLAGS: {issues}")
            else:
                print("\n  Structural checks passed (still read it yourself for quality).")


if __name__ == "__main__":
    main()