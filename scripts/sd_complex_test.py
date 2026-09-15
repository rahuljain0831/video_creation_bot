"""
SD v1.5 Complex Scenario Test
Phase 0: Generate prompts via local Ollama (llama3.2:3b), then kill Ollama
Phase 1: GPU mode  — 512p + 720p for all subjects
Phase 2: CPU offload — 1080p for all subjects
No video generation.
"""

import os, sys, time, json, gc, subprocess
import torch
import psutil
from pathlib import Path
from PIL import Image

MODEL_PATH = "H:/models/stable_diffusion/models--runwayml--stable-diffusion-v1-5/snapshots/451f4fe16113bff5a5d2269ed5ad43b0592e9a14"
OUTPUT_DIR = Path("output/complex_test")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
PROMPTS_FILE = OUTPUT_DIR / "generated_prompts.json"

OLLAMA_MODEL = "llama3.2:3b"
OLLAMA_URL = "http://localhost:11434/api/generate"

# ── resolution groups ──────────────────────────────────────────────────────────
GPU_RESOLUTIONS = {
    "512p":          (512,  512),
    "720p_portrait": (720,  1280),
    "720p_landscape":(1280, 720),
}
CPU_RESOLUTIONS = {
    "1080p_portrait":  (1080, 1920),
    "1080p_landscape": (1920, 1080),
}

# ── subjects Ollama will expand into full SD prompts ───────────────────────────
SCENERY_SUBJECTS = [
    "ancient cedar forest with shafts of golden light and mist",
    "volcanic lava fields at night with glowing rivers of magma",
    "arctic tundra with northern lights aurora borealis",
    "tropical jungle waterfall with hidden pool and exotic flowers",
    "sahara desert at golden hour with towering sand dunes casting shadows",
    "cherry blossom forest in full bloom with falling petals",
    "scottish highland moors at dawn with heather and dramatic clouds",
    "deep underwater coral reef with bioluminescent sea life",
    "monsoon rain on a mountain valley with dramatic storm clouds",
    "lavender fields in provence at sunset with old stone farmhouse",
]

SAMURAI_SUBJECTS = [
    "lone samurai standing in dense bamboo forest at dawn, mist swirling around his feet, katana drawn",
]

HORROR_SUBJECTS = [
    "abandoned asylum corridor at night, flickering fluorescent lights, shadows creeping on peeling walls",
    "ancient cemetery at midnight under a blood red moon, gnarled dead trees, thick fog",
    "dark forest with a glowing ritual circle of candles, shadowy figures, eerie atmosphere",
    "haunted Victorian mansion on a cliff during a lightning storm, silhouette in window",
]

# ── Ollama prompt generation ───────────────────────────────────────────────────
def generate_prompt_via_ollama(subject: str, style_hint: str = "photorealistic") -> dict:
    import urllib.request
    template = (
        f"Generate a Stable Diffusion v1.5 image prompt for: {subject}\n"
        f"Style: {style_hint}\n"
        "Rules: positive prompt max 60 words, focus on lighting/atmosphere/composition/visual details. "
        "Negative prompt max 20 words covering common SD artifacts.\n"
        "Output ONLY two lines:\n"
        "POSITIVE: <prompt>\n"
        "NEGATIVE: <prompt>"
    )
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "prompt": template,
        "stream": False,
        "options": {"temperature": 0.7, "num_predict": 200, "num_gpu": 0}
    }).encode()

    req = urllib.request.Request(OLLAMA_URL, data=payload,
                                  headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    text = data["response"].strip()

    positive, negative = "", ""
    for line in text.splitlines():
        if line.upper().startswith("POSITIVE:"):
            positive = line.split(":", 1)[1].strip()
        elif line.upper().startswith("NEGATIVE:"):
            negative = line.split(":", 1)[1].strip()

    # fallback if parse fails
    if not positive:
        positive = subject
    if not negative:
        negative = "blurry, low quality, watermark, text, deformed"

    return {"subject": subject, "positive": positive, "negative": negative}


def generate_all_prompts():
    print("\n[OLLAMA] Generating prompts via", OLLAMA_MODEL)
    all_prompts = {"scenery": [], "samurai": [], "horror": []}

    for i, s in enumerate(SCENERY_SUBJECTS):
        print(f"  scenery {i+1}/{len(SCENERY_SUBJECTS)}: {s[:50]}...")
        all_prompts["scenery"].append(generate_prompt_via_ollama(s, "photorealistic landscape photography"))

    for s in SAMURAI_SUBJECTS:
        print(f"  samurai: {s[:50]}...")
        all_prompts["samurai"].append(generate_prompt_via_ollama(s, "cinematic photorealistic"))

    for s in HORROR_SUBJECTS:
        print(f"  horror: {s[:50]}...")
        all_prompts["horror"].append(generate_prompt_via_ollama(s, "dark cinematic horror photography"))

    with open(PROMPTS_FILE, "w") as f:
        json.dump(all_prompts, f, indent=2)
    print(f"[OLLAMA] Saved {sum(len(v) for v in all_prompts.values())} prompts to {PROMPTS_FILE}")
    return all_prompts


def kill_ollama():
    print("\n[OLLAMA] Killing all Ollama processes to free RAM/GPU...")
    subprocess.run(["taskkill", "/F", "/IM", "ollama.exe", "/T"],
                   capture_output=True)
    subprocess.run(["taskkill", "/F", "/IM", "ollama_llama_server.exe", "/T"],
                   capture_output=True)
    time.sleep(3)
    gc.collect()
    print("[OLLAMA] Done")


# ── SD helpers ─────────────────────────────────────────────────────────────────
def clear_cuda():
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

def vram_peak_mb():
    return torch.cuda.max_memory_allocated() / 1024**2 if torch.cuda.is_available() else 0

def reset_peak():
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()


def load_pipeline(cpu_offload=False):
    from diffusers import StableDiffusionPipeline
    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True
    reset_peak()

    if cpu_offload:
        pipe = StableDiffusionPipeline.from_pretrained(
            MODEL_PATH, torch_dtype=torch.float16,
            safety_checker=None, requires_safety_checker=False,
            local_files_only=True,
        )
        pipe.enable_sequential_cpu_offload()
        pipe.vae.enable_tiling()
    else:
        pipe = StableDiffusionPipeline.from_pretrained(
            MODEL_PATH, torch_dtype=torch.float16,
            safety_checker=None, requires_safety_checker=False,
            local_files_only=True, low_cpu_mem_usage=True, device_map="cuda",
        )
        pipe.enable_attention_slicing(slice_size="auto")
        pipe.vae.enable_tiling()
    print(f"  [LOAD] VRAM peak {vram_peak_mb():.0f}MB | cpu_offload={cpu_offload}")
    return pipe


def run_image(pipe, category, name, positive, negative, width, height, res_label, steps=20):
    reset_peak()
    clear_cuda()
    oom = False
    elapsed = 0
    try:
        result = pipe(
            prompt=positive,
            negative_prompt=negative,
            width=width, height=height,
            num_inference_steps=steps,
            guidance_scale=7.5,
            generator=torch.Generator("cuda").manual_seed(42),
        )
        img = result.images[0]
        elapsed = 0  # calculated outside if needed
        safe_name = name.replace(" ", "_").replace("/", "_")[:40]
        fname = OUTPUT_DIR / f"{category}_{safe_name}_{res_label}.png"
        img.save(fname)
        peak = vram_peak_mb()
        print(f"    OK {peak:.0f}MB VRAM | {fname.name}")
        return True
    except torch.cuda.OutOfMemoryError:
        print(f"    OOM")
        clear_cuda()
        return False
    except Exception as e:
        print(f"    ERROR: {str(e)[:80]}")
        clear_cuda()
        return False


# ── main ───────────────────────────────────────────────────────────────────────
def main():
    # Phase 0: prompts
    if PROMPTS_FILE.exists():
        print(f"[SKIP] Prompts file exists, loading: {PROMPTS_FILE}")
        with open(PROMPTS_FILE) as f:
            all_prompts = json.load(f)
    else:
        all_prompts = generate_all_prompts()
        kill_ollama()

    # print generated prompts
    print("\n[PROMPTS GENERATED]")
    for category, items in all_prompts.items():
        print(f"\n  -- {category.upper()} --")
        for p in items:
            print(f"  subject : {p['subject'][:60]}")
            print(f"  positive: {p['positive'][:80]}")
            print(f"  negative: {p['negative'][:60]}")
            print()

    # Phase 1: GPU mode — 512p + 720p
    print("\n[PHASE 1] GPU mode — 512p + 720p")
    pipe = load_pipeline(cpu_offload=False)

    for res_label, (w, h) in GPU_RESOLUTIONS.items():
        print(f"\n  Resolution: {res_label} ({w}x{h})")

        if res_label == "512p":
            # scenery: all 10
            print("  Scenery (10 variations):")
            for p in all_prompts["scenery"]:
                name = p["subject"][:30]
                print(f"    {name}...")
                run_image(pipe, "scenery", name, p["positive"], p["negative"], w, h, res_label)
        else:
            # scenery: first 3 only at 720p (representative sample)
            print("  Scenery (3 sample):")
            for p in all_prompts["scenery"][:3]:
                name = p["subject"][:30]
                print(f"    {name}...")
                run_image(pipe, "scenery", name, p["positive"], p["negative"], w, h, res_label)

        # samurai: all resolutions
        print("  Samurai:")
        for p in all_prompts["samurai"]:
            run_image(pipe, "samurai", p["subject"], p["positive"], p["negative"], w, h, res_label)

        # horror: all scenes
        print("  Horror:")
        for p in all_prompts["horror"]:
            name = p["subject"][:30]
            print(f"    {name}...")
            run_image(pipe, "horror", name, p["positive"], p["negative"], w, h, res_label)

    del pipe
    clear_cuda()

    # Phase 2: CPU offload — 1080p
    print("\n[PHASE 2] CPU offload — 1080p")
    pipe = load_pipeline(cpu_offload=True)

    for res_label, (w, h) in CPU_RESOLUTIONS.items():
        print(f"\n  Resolution: {res_label} ({w}x{h})")

        # scenery: first 3
        print("  Scenery (3 sample):")
        for p in all_prompts["scenery"][:3]:
            name = p["subject"][:30]
            print(f"    {name}...")
            run_image(pipe, "scenery", name, p["positive"], p["negative"], w, h, res_label)

        # samurai
        print("  Samurai:")
        for p in all_prompts["samurai"]:
            run_image(pipe, "samurai", p["subject"], p["positive"], p["negative"], w, h, res_label)

        # horror
        print("  Horror:")
        for p in all_prompts["horror"]:
            name = p["subject"][:30]
            print(f"    {name}...")
            run_image(pipe, "horror", name, p["positive"], p["negative"], w, h, res_label)

    del pipe
    clear_cuda()

    print("\n[DONE] All outputs in:", OUTPUT_DIR.absolute())
    print(f"Total images: {len(list(OUTPUT_DIR.glob('*.png')))}")


if __name__ == "__main__":
    main()
