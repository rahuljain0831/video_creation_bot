"""
Standalone test for ComfyUI image generation.

Usage:
    python scripts/test_comfyui.py "Krishna playing flute under moonlight"
    python scripts/test_comfyui.py "dark abandoned hallway flickering light"
    python scripts/test_comfyui.py --batch 5  # generate 5 test images from saved script

Requires ComfyUI running locally (default http://127.0.0.1:8188).
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import cfg
from pipeline.comfyui_gen import generate_comfyui_image, ComfyUIError


def find_latest_script() -> Path:
    scripts_dir = Path(cfg.paths["scripts"])
    scripts = sorted(scripts_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not scripts:
        print("No scripts found in", scripts_dir)
        sys.exit(1)
    return scripts[0]


def main():
    parser = argparse.ArgumentParser(description="Test ComfyUI image generation")
    parser.add_argument("prompt", nargs="?", help="Image prompt to generate")
    parser.add_argument("--batch", type=int, default=0, help="Generate N images from latest script")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")

    output_dir = "output/images/comfyui_test"
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Default niche for standalone test
    test_niche = {
        "id": "test",
        "art_style_prompt_suffix": "cinematic composition, dramatic lighting, ultra-detailed",
    }

    if args.prompt:
        prompts = [(0, args.prompt)]
    elif args.batch > 0:
        script_path = find_latest_script()
        print(f"Script: {script_path}")
        with open(script_path) as f:
            data = json.load(f)
        test_niche = data.get("niche", test_niche)
        scenes = data.get("script", {}).get("scenes", [])
        prompts = [(i, s["image_prompt"]) for i, s in enumerate(scenes[:args.batch])]
    else:
        parser.print_help()
        sys.exit(1)

    print(f"\nOutput: {output_dir}")
    print(f"Prompts: {len(prompts)}\n")

    total_time = 0
    for scene_idx, prompt in prompts:
        print(f"--- Scene {scene_idx} ---")
        print(f"Prompt: {prompt[:100]}...")
        t0 = time.time()
        try:
            path = generate_comfyui_image(
                image_prompt=prompt,
                niche=test_niche,
                output_dir=output_dir,
                scene_index=scene_idx,
                cfg=cfg,
            )
            elapsed = time.time() - t0
            total_time += elapsed
            size = Path(path).stat().st_size
            print(f"Output: {path}")
            print(f"Size: {size / 1024:.0f} KB  |  Time: {elapsed:.1f}s")
        except ComfyUIError as e:
            elapsed = time.time() - t0
            print(f"FAILED ({elapsed:.1f}s): {e}")
        print()

    if len(prompts) > 1:
        print(f"Total time: {total_time:.1f}s  |  Avg: {total_time / len(prompts):.1f}s per image")


if __name__ == "__main__":
    main()
