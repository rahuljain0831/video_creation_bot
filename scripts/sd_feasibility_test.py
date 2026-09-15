"""
SD v1.5 Feasibility Analysis — RTX 3050 Laptop 4GB VRAM
Tests: 720p / 1080p image generation + short video frames (5s / 10s / 15s)
Prompts: scenery, kitchen, anime character, space
"""

import os, sys, time, json, gc, math, subprocess
import torch
import psutil
from pathlib import Path
from PIL import Image
from diffusers import StableDiffusionPipeline, StableDiffusionImg2ImgPipeline
import imageio
import numpy as np

# ── paths ──────────────────────────────────────────────────────────────────────
MODEL_PATH = "H:/models/stable_diffusion/models--runwayml--stable-diffusion-v1-5/snapshots/451f4fe16113bff5a5d2269ed5ad43b0592e9a14"
OUTPUT_DIR = Path("output/feasibility_test")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

RESULTS = []  # collected benchmark rows

# ── hardware helpers ───────────────────────────────────────────────────────────
def vram_used_mb():
    if torch.cuda.is_available():
        return torch.cuda.memory_allocated() / 1024**2
    return 0.0

def vram_peak_mb():
    if torch.cuda.is_available():
        return torch.cuda.max_memory_allocated() / 1024**2
    return 0.0

def ram_used_gb():
    return psutil.virtual_memory().used / 1024**3

def reset_peak():
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()

def clear_cuda():
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

def gpu_util_pct():
    """Query GPU utilization via nvidia-smi."""
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=utilization.gpu,temperature.gpu,clocks.current.sm",
             "--format=csv,noheader,nounits"],
            timeout=3
        ).decode().strip()
        util, temp, clock = out.split(",")
        return f"GPU {util.strip()}% | {temp.strip()}°C | {clock.strip()}MHz"
    except Exception:
        return "nvidia-smi unavailable"

def hw_snapshot(label=""):
    snap = {
        "label": label,
        "vram_alloc_mb": round(vram_used_mb(), 1),
        "vram_peak_mb": round(vram_peak_mb(), 1),
        "ram_used_gb": round(ram_used_gb(), 2),
        "ram_avail_gb": round(psutil.virtual_memory().available / 1024**3, 2),
    }
    return snap

# ── resolution configs ─────────────────────────────────────────────────────────
# SD v1.5 native = 512×512. Higher res = more VRAM.
# For 9:16 portrait (phone video), pick heights divisible by 64.
RESOLUTIONS = {
    "512p_native": (512, 512),        # baseline — always works
    "720p_landscape": (1280, 720),    # 16:9
    "720p_portrait": (720, 1280),     # 9:16 (phone)
    "1080p_landscape": (1920, 1080),  # 16:9
    "1080p_portrait": (1080, 1920),   # 9:16 (phone)
}

# ── ultra-detailed prompts ─────────────────────────────────────────────────────
PROMPTS = {
    "scenery": {
        "positive": (
            "ultra-detailed photorealistic landscape, golden hour light, sweeping alpine meadow "
            "covered in lush knee-high emerald grass with scattered wildflowers in purple, yellow "
            "and white, a crystal-clear mountain stream winding through the center with smooth "
            "river-polished stones visible beneath the water surface, snow-capped granite mountain "
            "peaks in the background with wisps of cloud clinging to the upper ridges, "
            "foreground with detailed fern fronds and moss-covered rocks, ancient pine trees "
            "with deeply furrowed bark lining the left edge, volumetric golden rays breaking "
            "through scattered cumulus clouds, reflective wet rocks near the stream bank, "
            "perfectly sharp depth of field, Hasselblad medium format lens rendering, "
            "RAW photograph, 8K resolution, National Geographic quality, award-winning nature photography"
        ),
        "negative": (
            "blurry, low quality, cartoon, painting, watermark, text, people, animals, "
            "overexposed, underexposed, flat lighting, gray sky, pollution, urban elements"
        ),
    },
    "kitchen": {
        "positive": (
            "ultra-detailed photorealistic modern farmhouse kitchen interior, "
            "shaker-style white painted wooden cabinet doors with brushed nickel cup-pull handles "
            "arranged in two rows — upper cabinets reaching to 9-foot ceiling, lower cabinets with "
            "three deep drawer units on the left side and two-door units on the right, "
            "thick white Carrara marble countertop with grey veining running across the full "
            "kitchen perimeter, large 36-inch apron-front farmhouse sink in matte white fireclay "
            "positioned under a double-pane window overlooking a garden, "
            "stainless steel Bosch dishwasher flush with cabinetry to the left of the sink, "
            "a 48-inch six-burner dual-fuel range in matte black with brass knobs and a matching "
            "matte black range hood above it vented to exterior, "
            "open floating oak shelves on the right wall holding: white ceramic mixing bowls stacked "
            "in three sizes, clear glass mason jars filled with pasta and lentils, a cast-iron "
            "skillet hanging on a wall-mounted pot rail above, copper measuring cups hanging in row, "
            "a 10-inch chef's knife and bread knife resting in a walnut magnetic knife block on "
            "the countertop, a KitchenAid stand mixer in cream positioned at the corner, "
            "a gooseneck matte black kitchen faucet with separate spray handle, "
            "hexagonal white subway tile backsplash from countertop to upper cabinets, "
            "pendant lights with Edison bulb over an 8-foot kitchen island with shiplap sides "
            "painted dark navy, island has four bar stools with woven seagrass seats, "
            "hardwood oak flooring with visible wood grain, morning window light casting long shadows, "
            "photorealistic interior design photography, 8K, ultra sharp, architectural digest quality"
        ),
        "negative": (
            "people, messy, dirty, low quality, blurry, cartoon, sketch, outdated appliances, "
            "fluorescent lighting, linoleum floor, cheap cabinets, cluttered, overexposed"
        ),
    },
    "anime": {
        "positive": (
            "ultra-detailed anime illustration, full body portrait of a female warrior character, "
            "standing in a confident three-quarter pose, "
            "face: high cheekbones, small pointed nose, large expressive violet eyes with detailed "
            "iris showing radial fiber pattern and a bright catchlight reflection, long dark upper "
            "lashes, thin arched eyebrows, soft pink lip tint on full lips with visible cupid's bow, "
            "small delicate ears partially hidden by hair, smooth fair porcelain skin with subtle "
            "blush on cheeks, "
            "hair: long flowing silver-white hair reaching waist, parted slightly left of center, "
            "multiple thin braids woven through the loose strands, hair tips fading to pale lavender, "
            "wind-blown movement with individual strands visible against the background, "
            "neck: slender with visible clavicle, thin gold chain necklace with a crescent moon pendant, "
            "shoulders: slightly broad for a warrior, straight posture with right shoulder slightly forward, "
            "arms: toned but feminine, right arm raised holding a glowing longsword upward, left arm "
            "extended slightly with open palm facing viewer, visible finger joints and nails painted "
            "dark purple, slender wrists, "
            "torso: form-fitting silver breastplate with intricate engraved floral motifs, dark navy "
            "leather undershirt visible at neckline and shoulders, slim waist with a wide brown "
            "leather belt holding three small pouches, "
            "legs: narrow hip flare into fitted dark slate-grey leather trousers with visible stitching "
            "detail along the outer seam, thighs proportionally athletic, knees visible through fabric, "
            "calves wrapped in crisscross leather greaves from knee to ankle, "
            "feet: knee-high dark leather boots with silver buckles, slight heel, toes pointing forward "
            "in a wide stance, "
            "background: bokeh forest at dusk with fireflies, magical atmosphere, "
            "style: Makoto Shinkai inspired, saturated colors, cel-shaded with soft ambient occlusion, "
            "8K, masterpiece, official art quality"
        ),
        "negative": (
            "ugly, deformed hands, extra fingers, missing fingers, bad anatomy, mutated, "
            "low quality, blurry, watermark, text, western cartoon, 3D render, realistic photo, "
            "male features, flat color, no shading, amateur art, wrong proportions"
        ),
    },
    "space": {
        "positive": (
            "ultra-detailed photorealistic deep space scene, "
            "foreground: rugged barren asteroid surface occupying lower third of frame, "
            "surface composed of dark charcoal-grey regolith with jagged angular rocks casting "
            "sharp shadows in starlight, fine dust particles visible in microgravity floating "
            "just above the surface, a single cracked boulder showing internal crystalline "
            "structure in turquoise and gold catching distant starlight, "
            "midground: gas giant planet filling 40% of the frame on the right side, "
            "planet shows swirling banded atmosphere in amber, cream and terracotta tones, "
            "multiple thin ring systems extending from planet edge — composed of individual "
            "ice chunks and rocky debris visible at high detail, planet surface shows a massive "
            "hexagonal storm system similar to Saturn's north pole in deep burgundy tones, "
            "small crescent moon partially lit visible at planet's lower left, "
            "background: absolute deep black space filled with thousands of stars in varying "
            "brightness — foreground stars larger with subtle 6-point diffraction spikes, "
            "background stars forming the dense band of the Milky Way core, "
            "a large emission nebula in deep teal and magenta occupying the upper left quadrant "
            "with wisps of gas cloud detail and embedded proto-stars glowing bright white, "
            "a distant blue-white binary star system visible as two overlapping point sources "
            "connected by a faint gas stream, "
            "lighting: single cold blue-white star light source from upper right casting hard "
            "shadows across the asteroid surface, "
            "style: NASA Hubble Space Telescope composite photograph quality, "
            "photorealistic, 8K, scientifically accurate, award-winning astrophotography"
        ),
        "negative": (
            "cartoon, painting, watermark, text, people, spaceships, artificial structures, "
            "blurry, low quality, overexposed stars, flat sky, milky uniform background, "
            "incorrect physics, toy-like planets"
        ),
    },
}

# ── video config ───────────────────────────────────────────────────────────────
VIDEO_DURATIONS = [5, 10, 15]  # seconds
VIDEO_FPS = 8                   # SD can produce ~8fps worth of frames feasibly
# frames per video = duration * FPS
# strategy: generate 1 keyframe per second via img2img noise walk


# ── pipeline loader ────────────────────────────────────────────────────────────
def load_pipeline(cpu_offload=False):
    """Load SD v1.5 fp16. Use sequential CPU offload for big resolutions."""
    print(f"\n[LOAD] Loading SD v1.5 fp16 (cpu_offload={cpu_offload})")
    reset_peak()
    t0 = time.time()

    # TF32 — free ~10% speedup on Ampere, no precision loss for images
    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True

    if cpu_offload:
        # CPU offload: load to CPU first, then sequential offload per sub-module
        pipe = StableDiffusionPipeline.from_pretrained(
            MODEL_PATH,
            torch_dtype=torch.float16,
            safety_checker=None,
            requires_safety_checker=False,
            local_files_only=True,
        )
        pipe.enable_sequential_cpu_offload()
        pipe.enable_vae_tiling()
    else:
        # Direct-to-CUDA: peak CPU RAM = one weight tensor at a time (~MB, not ~2.4GB).
        # Mandatory on this machine — available RAM fluctuates <3GB due to other processes.
        pipe = StableDiffusionPipeline.from_pretrained(
            MODEL_PATH,
            torch_dtype=torch.float16,
            safety_checker=None,
            requires_safety_checker=False,
            local_files_only=True,
            low_cpu_mem_usage=True,
            device_map="cuda",
        )
        pipe.enable_attention_slicing(slice_size="auto")
        pipe.enable_vae_tiling()
    # NOTE: torch.compile removed — recompiles every time input shape changes (each resolution),
    # CPU-bound compilation takes 2-5min per shape, shows 0% GPU, net loss for a multi-res benchmark.

    load_time = time.time() - t0
    snap = hw_snapshot("after_load")
    print(f"[LOAD] Done in {load_time:.1f}s | VRAM peak: {snap['vram_peak_mb']:.0f}MB")
    return pipe, load_time


# ── single image benchmark ─────────────────────────────────────────────────────
def run_image_test(pipe, prompt_name, width, height, res_label, steps=20):
    """Generate one image, capture timing + VRAM, save result."""
    prompt_data = PROMPTS[prompt_name]
    print(f"\n  [{prompt_name}] {res_label} ({width}×{height}) steps={steps}")

    reset_peak()
    clear_cuda()
    print(f"    pre-inference: {gpu_util_pct()}")
    t0 = time.time()
    oom = False
    img = None

    try:
        result = pipe(
            prompt=prompt_data["positive"],
            negative_prompt=prompt_data["negative"],
            width=width,
            height=height,
            num_inference_steps=steps,
            guidance_scale=7.5,
            generator=torch.Generator("cuda").manual_seed(42),
        )
        img = result.images[0]
        elapsed = time.time() - t0

        fname = OUTPUT_DIR / f"{prompt_name}_{res_label}.png"
        img.save(fname)
        print(f"    OK {elapsed:.1f}s | VRAM peak {vram_peak_mb():.0f}MB | {gpu_util_pct()} | saved {fname.name}")

    except torch.cuda.OutOfMemoryError as e:
        elapsed = time.time() - t0
        oom = True
        print(f"    OOM after {elapsed:.1f}s — {str(e)[:80]}")
        clear_cuda()
    except Exception as e:
        elapsed = time.time() - t0
        oom = True
        print(f"    ERROR after {elapsed:.1f}s — {str(e)[:120]}")
        clear_cuda()

    row = {
        "type": "image",
        "prompt": prompt_name,
        "resolution": res_label,
        "width": width,
        "height": height,
        "steps": steps,
        "elapsed_sec": round(elapsed, 1),
        "vram_peak_mb": round(vram_peak_mb(), 0),
        "ram_used_gb": round(ram_used_gb(), 2),
        "oom": oom,
        "feasible": not oom,
    }
    RESULTS.append(row)
    return img, not oom


# ── video frame generation ─────────────────────────────────────────────────────
def generate_video_frames(pipe, prompt_name, width, height, duration_sec, fps=VIDEO_FPS):
    """
    Generate short video via img2img noise walk:
    1. Generate anchor frame (txt2img)
    2. For each subsequent keyframe: add slight noise + img2img at low strength
    3. Interpolate between keyframes using PIL cross-fade
    4. Save as mp4 via imageio
    """
    prompt_data = PROMPTS[prompt_name]
    n_keyframes = duration_sec + 1   # one keyframe per second
    n_total_frames = duration_sec * fps

    print(f"\n  [VIDEO] {prompt_name} {width}x{height} {duration_sec}s ({n_keyframes} keyframes -> {n_total_frames} frames)")

    reset_peak()
    clear_cuda()
    t0 = time.time()
    oom = False
    keyframes = []

    try:
        # ── anchor frame ──
        result = pipe(
            prompt=prompt_data["positive"],
            negative_prompt=prompt_data["negative"],
            width=width,
            height=height,
            num_inference_steps=20,
            guidance_scale=7.5,
            generator=torch.Generator("cuda").manual_seed(100),
        )
        anchor = result.images[0]
        keyframes.append(anchor)
        print(f"    anchor frame done | VRAM {vram_peak_mb():.0f}MB")

        # ── img2img pipeline for subsequent keyframes ──
        img2img_pipe = StableDiffusionImg2ImgPipeline(
            vae=pipe.vae,
            text_encoder=pipe.text_encoder,
            tokenizer=pipe.tokenizer,
            unet=pipe.unet,
            scheduler=pipe.scheduler,
            safety_checker=None,
            feature_extractor=None,
            requires_safety_checker=False,
        )
        if hasattr(pipe, '_execution_device') and str(pipe._execution_device) == 'cpu':
            img2img_pipe.enable_sequential_cpu_offload()
        else:
            img2img_pipe = img2img_pipe.to("cuda")
        img2img_pipe.enable_attention_slicing(slice_size=1)
        img2img_pipe.enable_vae_tiling()

        for i in range(1, n_keyframes):
            seed = 100 + i * 7
            strength = 0.25   # low strength = stays close to anchor
            kf_result = img2img_pipe(
                prompt=prompt_data["positive"],
                negative_prompt=prompt_data["negative"],
                image=keyframes[-1],
                strength=strength,
                num_inference_steps=12,
                guidance_scale=7.5,
                generator=torch.Generator("cuda").manual_seed(seed),
            )
            keyframes.append(kf_result.images[0])
            print(f"    keyframe {i}/{n_keyframes-1} done")

        # ── interpolate between keyframes ──
        all_frames = []
        interp_count = fps  # frames between each pair of keyframes

        for ki in range(len(keyframes) - 1):
            arr0 = np.array(keyframes[ki], dtype=np.float32)
            arr1 = np.array(keyframes[ki + 1], dtype=np.float32)
            for f in range(interp_count):
                alpha = f / interp_count
                blended = ((1 - alpha) * arr0 + alpha * arr1).clip(0, 255).astype(np.uint8)
                all_frames.append(blended)

        elapsed = time.time() - t0
        out_path = OUTPUT_DIR / f"video_{prompt_name}_{width}x{height}_{duration_sec}s.mp4"

        # write mp4
        writer = imageio.get_writer(str(out_path), fps=fps, codec='libx264', quality=8)
        for frame in all_frames:
            writer.append_data(frame)
        writer.close()

        file_mb = out_path.stat().st_size / 1024**2
        print(f"    VIDEO OK {elapsed:.1f}s | {len(all_frames)} frames | {file_mb:.1f}MB | {out_path.name}")

    except torch.cuda.OutOfMemoryError as e:
        elapsed = time.time() - t0
        oom = True
        print(f"    VIDEO OOM after {elapsed:.1f}s — {str(e)[:80]}")
        clear_cuda()
    except Exception as e:
        elapsed = time.time() - t0
        oom = True
        print(f"    VIDEO ERROR after {elapsed:.1f}s — {e}")
        clear_cuda()

    row = {
        "type": "video",
        "prompt": prompt_name,
        "resolution": f"{width}x{height}",
        "width": width,
        "height": height,
        "duration_sec": duration_sec,
        "fps": fps,
        "elapsed_sec": round(elapsed, 1),
        "vram_peak_mb": round(vram_peak_mb(), 0),
        "ram_used_gb": round(ram_used_gb(), 2),
        "oom": oom,
        "feasible": not oom,
    }
    RESULTS.append(row)


# ── main ───────────────────────────────────────────────────────────────────────
def print_results():
    print("\n" + "="*80)
    print("FEASIBILITY REPORT - SD v1.5 on RTX 3050 Laptop 4GB VRAM")
    print("="*80)

    # image results
    img_rows = [r for r in RESULTS if r["type"] == "image"]
    vid_rows = [r for r in RESULTS if r["type"] == "video"]

    print("\n-- IMAGE GENERATION ----------------------------------------------")
    print(f"{'PROMPT':<18} {'RESOLUTION':<20} {'TIME':>8} {'VRAM':>10} {'STATUS'}")
    print("-"*70)
    for r in img_rows:
        status = "OK " if r["feasible"] else "OOM"
        print(f"{r['prompt']:<18} {r['resolution']:<20} {r['elapsed_sec']:>6.1f}s {r['vram_peak_mb']:>8.0f}MB  {status}")

    print("\n-- VIDEO GENERATION ----------------------------------------------")
    print(f"{'PROMPT':<18} {'RESOLUTION':<16} {'DUR':>5} {'TIME':>8} {'VRAM':>10} {'STATUS'}")
    print("-"*70)
    for r in vid_rows:
        status = "OK " if r["feasible"] else "OOM"
        print(f"{r['prompt']:<18} {r['resolution']:<16} {r['duration_sec']:>3}s {r['elapsed_sec']:>7.1f}s {r['vram_peak_mb']:>8.0f}MB  {status}")

    # summary
    total = len(RESULTS)
    ok = sum(1 for r in RESULTS if r["feasible"])
    print(f"\n-- SUMMARY: {ok}/{total} tests feasible -----------------------------------------")

    # recommendations
    print("\n-- RECOMMENDATIONS -----------------------------------------------")
    img_ok_res = {r["resolution"] for r in img_rows if r["feasible"]}
    img_fail_res = {r["resolution"] for r in img_rows if not r["feasible"]}
    vid_ok = [(r["resolution"], r["duration_sec"]) for r in vid_rows if r["feasible"]]
    vid_fail = [(r["resolution"], r["duration_sec"]) for r in vid_rows if not r["feasible"]]

    for res in sorted(img_ok_res):
        print(f"  OK  Image @ {res}: FEASIBLE")
    for res in sorted(img_fail_res):
        print(f"  OOM Image @ {res}: use tiled/upscale post-process instead")
    for res, dur in vid_ok:
        print(f"  OK  Video {res} {dur}s: FEASIBLE")
    for res, dur in vid_fail:
        print(f"  OOM Video {res} {dur}s")

    print("\n  MEMORY CEILING NOTE:")
    print("  RTX 3050 4GB: SD v1.5 UNet fp16 ~2.0GB + VAE ~0.5GB + text enc ~0.5GB = ~3.0GB")
    print("  512p: ~3.2GB fits. 720p: ~3.6-3.9GB fits with slicing. 1080p: ~5.5GB+ OOM.")
    print("  CPU offload enables 1080p but at 5-10x slower speed (DRAM bandwidth bound).")
    print("\n  VIDEO NOTE:")
    print("  AnimateDiff motion modules not present - using img2img keyframe walk.")
    print("  For true temporal coherence: download AnimateDiff v3 motion module (~1.8GB).")
    print("  Current approach: passable for b-roll, not for character animation.")

    # save JSON
    report_path = OUTPUT_DIR / "feasibility_report.json"
    with open(report_path, "w") as f:
        json.dump(RESULTS, f, indent=2)
    print(f"\n  Full results → {report_path}")
    print("="*80)


def main():
    print("="*80)
    print("SD v1.5 FEASIBILITY TEST")
    print(f"GPU: NVIDIA GeForce RTX 3050 Laptop | VRAM: 4GB | RAM: 15.8GB")
    print(f"Output: {OUTPUT_DIR.absolute()}")
    print("="*80)

    # ── PHASE 1: image generation at multiple resolutions ─────────────────────
    print("\n[PHASE 1] Image generation — 512p + 720p (GPU only)")
    pipe_gpu, load_time = load_pipeline(cpu_offload=False)

    for res_label, (w, h) in [
        ("512p_native", (512, 512)),
        ("720p_portrait_9x16", (720, 1280)),
        ("720p_landscape", (1280, 720)),
    ]:
        for prompt_name in PROMPTS:
            run_image_test(pipe_gpu, prompt_name, w, h, res_label)

    # ── PHASE 2: 1080p with CPU offload ───────────────────────────────────────
    print("\n[PHASE 2] Image generation — 1080p (sequential CPU offload)")
    print("  NOTE: CPU offload = slower but enables resolutions beyond VRAM limit")

    # unload GPU pipe first
    del pipe_gpu
    clear_cuda()
    time.sleep(2)

    pipe_cpu, _ = load_pipeline(cpu_offload=True)
    for res_label, (w, h) in [
        ("1080p_portrait_9x16", (1080, 1920)),
        ("1080p_landscape", (1920, 1080)),
    ]:
        # Test with one prompt first (they take ~3-5min each with CPU offload)
        for prompt_name in PROMPTS:
            run_image_test(pipe_cpu, prompt_name, w, h, res_label, steps=20)

    # ── PHASE 3: video generation ──────────────────────────────────────────────
    print("\n[PHASE 3] Video generation — keyframe walk approach")
    del pipe_cpu
    clear_cuda()
    time.sleep(2)

    pipe_vid, _ = load_pipeline(cpu_offload=False)

    # Use portrait 720p for phone-format videos (most practical)
    vid_w, vid_h = 720, 1280
    # Test one prompt × all durations (scenery is fastest to converge)
    for prompt_name in ["scenery", "space"]:
        for duration in VIDEO_DURATIONS:
            generate_video_frames(pipe_vid, prompt_name, vid_w, vid_h, duration)

    # ── PHASE 4: anime + kitchen video at 5s only (most complex prompts) ──────
    for prompt_name in ["kitchen", "anime"]:
        generate_video_frames(pipe_vid, prompt_name, vid_w, vid_h, 5)

    del pipe_vid
    clear_cuda()

    print_results()


if __name__ == "__main__":
    main()
