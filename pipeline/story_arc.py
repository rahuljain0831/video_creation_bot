"""
Story arc generator — converts a quote into a 3-scene visual narrative via Ollama.
"""
import json
import logging

import requests

log = logging.getLogger(__name__)

_PROMPT = """You are a visual storyteller. Given a motivational quote, create a 3-scene story arc showing its emotional journey.

Output ONLY a JSON array of exactly 3 scenes. Each scene has:
- "search": 3-5 word stock footage search term (concrete, visual, no people)
- "mood": one of: calm, intense, uplifting, inspiring, dramatic, peaceful, energetic
- "description": one sentence of what the viewer sees (used as AI video prompt)

Example for "Rise above the storm":
[
  {{"search": "dark storm clouds lightning", "mood": "intense", "description": "Dark threatening storm clouds fill the sky with electric energy"}},
  {{"search": "waves crashing rocks", "mood": "dramatic", "description": "Powerful ocean waves pound relentlessly against jagged coastal rocks"}},
  {{"search": "eagle soaring clear sky", "mood": "uplifting", "description": "A lone eagle soars freely above the clouds into brilliant sunlight"}}
]

Rules:
- No people, no faces — nature, animals, elements, landscapes only
- Search terms must be concrete and searchable on stock footage sites
- Scenes must tell a progression: challenge → struggle → resolution

Quote: "{quote}"
Output:"""


def generate_story_arc(
    quote_text: str,
    ollama_base_url: str = "http://localhost:11434",
    model: str = "llama3.1:8b",
    num_gpu: int = 0,
    timeout: int = 300,
) -> list[dict]:
    """
    Returns list of 3 scene dicts: [{search, mood, description}, ...]
    Returns [] on any failure — caller must handle fallback.
    """
    try:
        prompt = _PROMPT.format(quote=quote_text.strip())
        r = requests.post(
            f"{ollama_base_url}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"num_gpu": num_gpu, "temperature": 0.7},
            },
            timeout=timeout,
        )
        r.raise_for_status()
        raw = r.json()["response"].strip()

        # Extract JSON array from response (model may add prose around it)
        start = raw.find("[")
        end   = raw.rfind("]") + 1
        if start == -1 or end == 0:
            log.warning("No JSON array in story arc response: %s", raw[:200])
            return []

        scenes = json.loads(raw[start:end])

        # Validate structure
        if not isinstance(scenes, list) or len(scenes) < 2:
            log.warning("Story arc returned fewer than 2 scenes: %s", scenes)
            return []

        valid = []
        for s in scenes[:3]:
            if isinstance(s, dict) and "search" in s and "mood" in s:
                valid.append({
                    "search":      str(s.get("search", "")).strip(),
                    "mood":        str(s.get("mood", "calm")).strip(),
                    "description": str(s.get("description", "")).strip(),
                })

        if len(valid) < 2:
            log.warning("Not enough valid scenes after parse: %s", valid)
            return []

        # Pad to exactly 3 if only 2 returned
        while len(valid) < 3:
            valid.append(valid[-1])

        log.info("Story arc: %s", [(s["mood"], s["search"]) for s in valid])
        return valid

    except Exception as e:
        log.warning("Story arc generation failed: %s", e)
        return []


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    quote = sys.argv[1] if len(sys.argv) > 1 else "The only chains that bind you are forged by fear."
    scenes = generate_story_arc(quote)
    print(json.dumps(scenes, indent=2))
