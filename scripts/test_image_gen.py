"""
Verify image generation policy + provider chain.

    python scripts/test_image_gen.py            # policy checks only (offline)
    python scripts/test_image_gen.py --live     # also generate 1 real image
"""

import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import cfg
from pipeline.image_gen import _load_providers, generate_image, ImageGenError
from pipeline.image_policy import (
    LocalGenerationBlocked,
    apply_no_human_policy,
    resolve_image_source,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")

live = "--live" in sys.argv
niches = {n["id"]: n for n in cfg.niches}
failures = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global failures
    status = "PASS" if condition else "FAIL"
    if not condition:
        failures += 1
    print(f"[{status}] {label}{(' — ' + detail) if detail else ''}")


print("\n== Niche image sources ==")
for niche_id, niche in niches.items():
    source = resolve_image_source(niche)
    local_ok = niche.get("allow_local_generation", True)
    print(f"  {niche_id:<15} source={source:<9} allow_local_generation={local_ok}")

print("\n== Policy: mythology never generates locally ==")
myth = niches["mythology"]
check("mythology allow_local_generation is false", myth.get("allow_local_generation") is False)
check("mythology resolves to a retrieval source", resolve_image_source(myth) in ("library", "pexels"),
      resolve_image_source(myth))

try:
    generate_image("Shiva meditating on Mount Kailash", myth, "output/images/_policy_test", 0,
                   cfg=cfg, local_only=True)
    check("local_only generation blocked for mythology", False, "no exception raised")
except LocalGenerationBlocked:
    check("local_only generation blocked for mythology", True)
except ImageGenError as e:
    check("local_only generation blocked for mythology", False, f"wrong error: {e}")

print("\n== Policy: no human figures in generated prompts ==")
positive, negative = apply_no_human_policy(
    "a tall man in a dark coat walks down an abandoned hallway, flickering lamp, fog", ""
)
check("human chunk removed from positive prompt",
      re.search(r"\bman\b", positive, re.IGNORECASE) is None, positive)
check("no-people directive appended", "no people" in positive.lower())
check("human terms present in negative prompt", "person" in negative.lower())
print(f"  positive: {positive}")
print(f"  negative: {negative}")

print("\n== Providers ==")
providers = _load_providers()
if not providers:
    print("  none configured (copy image_keys.example.json to image_keys.json)")
for p in providers:
    key_state = "key" if p["api_key"] else "keyless"
    print(f"  {p['name']:<14} type={p['type']:<13} model={p.get('model', '-'):<45} {key_state}")

if live:
    print("\n== Live generation (scary_stories) ==")
    out_dir = "output/images/_image_gen_test"
    try:
        path = generate_image(
            image_prompt="abandoned Victorian hallway, flickering gas lamp, creeping fog, low angle",
            niche=niches["scary_stories"],
            output_dir=out_dir,
            scene_index=0,
            cfg=cfg,
        )
        size_kb = Path(path).stat().st_size / 1024
        check("live image generated", size_kb > 5, f"{path} ({size_kb:.0f} KB)")
    except ImageGenError as e:
        check("live image generated", False, str(e))

print(f"\n{'ALL CHECKS PASSED' if failures == 0 else f'{failures} CHECK(S) FAILED'}")
sys.exit(1 if failures else 0)
