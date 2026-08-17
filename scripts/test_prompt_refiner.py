"""
Standalone test for prompt refiner.

Usage:
    python scripts/test_prompt_refiner.py                          # latest script
    python scripts/test_prompt_refiner.py output/scripts/xyz.json  # specific script
    python scripts/test_prompt_refiner.py --target generation      # SD-style prompts
"""

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import cfg
from pipeline.prompt_refiner import refine_image_prompts


def find_latest_script() -> Path:
    scripts_dir = Path(cfg.paths["scripts"])
    scripts = sorted(scripts_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not scripts:
        print("No scripts found in", scripts_dir)
        sys.exit(1)
    return scripts[0]


def main():
    parser = argparse.ArgumentParser(description="Test prompt refiner")
    parser.add_argument("script_path", nargs="?", help="Path to script JSON")
    parser.add_argument("--target", default="search", choices=["search", "generation"])
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")

    script_path = Path(args.script_path) if args.script_path else find_latest_script()
    print(f"\nScript: {script_path}")

    with open(script_path) as f:
        data = json.load(f)

    niche = data.get("niche", {})
    scenes = data.get("script", {}).get("scenes", [])

    if not scenes:
        print("No scenes in script")
        sys.exit(1)

    print(f"Niche: {niche.get('id', '?')}  |  Scenes: {len(scenes)}  |  Target: {args.target}\n")
    print("=" * 80)

    refined = refine_image_prompts(scenes, niche, target=args.target, cfg=cfg)

    for i, (orig, ref) in enumerate(zip(scenes, refined)):
        print(f"\n--- Scene {i + 1} ---")
        print(f"Narration:  {orig['narration']}")
        print(f"ORIGINAL:   {orig['image_prompt']}")
        refined_prompt = ref.get("image_prompt", "")
        changed = refined_prompt != orig["image_prompt"]
        print(f"REFINED:    {refined_prompt}" + ("  [changed]" if changed else "  (unchanged)"))

    print("\n" + "=" * 80)
    changed_count = sum(
        1 for o, r in zip(scenes, refined)
        if r.get("image_prompt") != o.get("image_prompt")
    )
    print(f"Refined: {changed_count}/{len(scenes)} prompts changed")


if __name__ == "__main__":
    main()
