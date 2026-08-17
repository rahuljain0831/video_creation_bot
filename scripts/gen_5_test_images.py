"""Generate 5 HD test images across different niches via ComfyUI."""
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import cfg
from pipeline.comfyui_gen import generate_comfyui_image, ComfyUIError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")

TEST_SCENES = [
    # Mythology is intentionally absent: allow_local_generation=false for that
    # niche, so local generation is blocked by pipeline/image_policy.py.
    {
        "niche": {"id": "heists", "art_style_prompt_suffix": "sleek noir-thriller digital painting, high contrast, cinematic composition"},
        "prompt": "Massive steel vault door half open, scattered blueprints on a table, single overhead lamp, empty room, low angle shot",
    },
    {
        "niche": {"id": "scary_stories", "art_style_prompt_suffix": "dark moody horror illustration, desaturated palette, heavy shadow, cinematic composition"},
        "prompt": "Abandoned Victorian hallway, flickering gas lamp, long shadows, peeling wallpaper, fog creeping through doorway, low angle shot, eerie green tint",
    },
    {
        "niche": {"id": "space_science", "art_style_prompt_suffix": "cosmic nebula deep space photography cinematic"},
        "prompt": "Giant ringed exoplanet rising over alien ocean, bioluminescent waves, two moons visible, wide angle, volumetric light rays through atmosphere",
    },
    {
        "niche": {"id": "finance_facts", "art_style_prompt_suffix": "finance business city skyline charts cinematic"},
        "prompt": "Massive golden vault door half-open, stacks of gold bars inside, dramatic spotlight, close-up, shallow depth of field, cinematic color grading",
    },
    {
        "niche": {"id": "ai_tech_tools", "art_style_prompt_suffix": "modern technology screen interface minimal cinematic"},
        "prompt": "Futuristic holographic control panel floating in dark room, blue and cyan glowing UI elements, data streams, wide shot, neon rim lighting",
    },
]

output_dir = "output/images/comfyui_5niche_test"
Path(output_dir).mkdir(parents=True, exist_ok=True)

total = 0
for i, scene in enumerate(TEST_SCENES):
    niche = scene["niche"]
    prompt = scene["prompt"]
    print(f"\n{'='*60}")
    print(f"[{i+1}/5] Niche: {niche['id']}")
    print(f"Prompt: {prompt[:80]}...")

    t0 = time.time()
    try:
        path = generate_comfyui_image(
            image_prompt=prompt,
            niche=niche,
            output_dir=output_dir,
            scene_index=i,
            cfg=cfg,
        )
        elapsed = time.time() - t0
        total += elapsed
        size_kb = Path(path).stat().st_size / 1024
        print(f"Output: {path}")
        print(f"Size: {size_kb:.0f} KB  |  Time: {elapsed:.1f}s")
    except ComfyUIError as e:
        elapsed = time.time() - t0
        print(f"FAILED ({elapsed:.1f}s): {e}")

print(f"\n{'='*60}")
print(f"Total time: {total:.1f}s  |  Avg: {total/5:.1f}s per image")
print(f"Output dir: {output_dir}")
