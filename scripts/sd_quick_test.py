"""Quick single-image test to confirm SD v1.5 pipeline works on this machine."""
import sys, time, gc
import torch, psutil
from pathlib import Path
from diffusers import StableDiffusionPipeline

MODEL_PATH = "H:/models/stable_diffusion/models--runwayml--stable-diffusion-v1-5/snapshots/451f4fe16113bff5a5d2269ed5ad43b0592e9a14"
OUT = Path("output/feasibility_test")
OUT.mkdir(parents=True, exist_ok=True)

def mb(n): return f"{n/1024**2:.0f}MB"
def gb(n): return f"{n/1024**3:.1f}GB"

print(f"RAM avail: {gb(psutil.virtual_memory().available)}", flush=True)
print(f"VRAM total: {gb(torch.cuda.get_device_properties(0).total_memory)}", flush=True)

print("\n[1] Loading pipeline direct-to-CUDA (fp16, low_cpu_mem_usage)...", flush=True)
t0 = time.time()
torch.backends.cuda.matmul.allow_tf32 = True
torch.backends.cudnn.allow_tf32 = True
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
torch.cuda.reset_peak_memory_stats()
elapsed_load = time.time() - t0
print(f"    loaded in {elapsed_load:.1f}s | RAM avail: {gb(psutil.virtual_memory().available)} | VRAM: {mb(torch.cuda.memory_allocated())}", flush=True)

print("[2] Generating 512x512 test image...", flush=True)
t2 = time.time()
try:
    result = pipe(
        prompt="a mountain lake at golden hour, photorealistic, 8k",
        negative_prompt="blurry, low quality",
        width=512, height=512,
        num_inference_steps=10,
        guidance_scale=7.5,
        generator=torch.Generator("cuda").manual_seed(42),
    )
    img = result.images[0]
    out_path = OUT / "quick_test_512.png"
    img.save(out_path)
    elapsed = time.time() - t2
    peak = torch.cuda.max_memory_allocated()
    print(f"    512x512: OK in {elapsed:.1f}s | VRAM peak: {mb(peak)} | saved {out_path}", flush=True)
except torch.cuda.OutOfMemoryError as e:
    print(f"    512x512: OOM — {e}", flush=True)

gc.collect(); torch.cuda.empty_cache(); torch.cuda.reset_peak_memory_stats()

print("[3] Generating 720x1280 (9:16 portrait) test image...", flush=True)
t3 = time.time()
try:
    result = pipe(
        prompt="a mountain lake at golden hour, photorealistic, 8k",
        negative_prompt="blurry, low quality",
        width=720, height=1280,
        num_inference_steps=10,
        guidance_scale=7.5,
        generator=torch.Generator("cuda").manual_seed(42),
    )
    img = result.images[0]
    out_path = OUT / "quick_test_720p_portrait.png"
    img.save(out_path)
    elapsed = time.time() - t3
    peak = torch.cuda.max_memory_allocated()
    print(f"    720x1280: OK in {elapsed:.1f}s | VRAM peak: {mb(peak)} | saved {out_path}", flush=True)
except torch.cuda.OutOfMemoryError as e:
    print(f"    720x1280: OOM — {e}", flush=True)

gc.collect(); torch.cuda.empty_cache(); torch.cuda.reset_peak_memory_stats()

print("[4] Generating 1280x720 (16:9 landscape) test image...", flush=True)
t4 = time.time()
try:
    result = pipe(
        prompt="a mountain lake at golden hour, photorealistic, 8k",
        negative_prompt="blurry, low quality",
        width=1280, height=720,
        num_inference_steps=10,
        guidance_scale=7.5,
        generator=torch.Generator("cuda").manual_seed(42),
    )
    img = result.images[0]
    out_path = OUT / "quick_test_720p_landscape.png"
    img.save(out_path)
    elapsed = time.time() - t4
    peak = torch.cuda.max_memory_allocated()
    print(f"    1280x720: OK in {elapsed:.1f}s | VRAM peak: {mb(peak)} | saved {out_path}", flush=True)
except torch.cuda.OutOfMemoryError as e:
    print(f"    1280x720: OOM — {e}", flush=True)

print("\nDONE — check output/feasibility_test/ for images", flush=True)
