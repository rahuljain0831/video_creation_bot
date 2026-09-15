"""SD 1.5 (512p) + Real-ESRGAN (4x) local image generator.

Generates 512x512, upscales 4x to ~2048px, crops/resizes to target portrait.
Load once with build_generator(), pass to generate_image().
"""
import time
from pathlib import Path
import numpy as np
import torch
from PIL import Image

SD_MODEL = "H:/models/stable_diffusion/models--runwayml--stable-diffusion-v1-5/snapshots/451f4fe16113bff5a5d2269ed5ad43b0592e9a14"
ESRGAN_MODEL = Path(__file__).parent.parent / "output/esrgan_test/RealESRGAN_x4plus.pth"

NEGATIVE = (
    "blurry, low quality, watermark, text, logo, duplicate, tiling, "
    "humans, people, faces, hands, arms, bodies"
)


def build_generator():
    """Load SD pipe + ESRGAN upsampler. Call once, reuse across images."""
    from diffusers import StableDiffusionPipeline
    from basicsr.archs.rrdbnet_arch import RRDBNet
    from realesrgan import RealESRGANer

    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True

    print("Loading SD 1.5...", flush=True)
    t0 = time.time()
    pipe = StableDiffusionPipeline.from_pretrained(
        SD_MODEL,
        torch_dtype=torch.float16,
        safety_checker=None,
        requires_safety_checker=False,
        local_files_only=True,
        low_cpu_mem_usage=True,
        device_map="cuda",
    )
    pipe.enable_attention_slicing(slice_size="auto")
    pipe.enable_vae_tiling()
    print(f"  loaded in {time.time()-t0:.1f}s", flush=True)

    print("Loading ESRGAN...", flush=True)
    model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64,
                    num_block=23, num_grow_ch=32, scale=4)
    upsampler = RealESRGANer(
        scale=4,
        model_path=str(ESRGAN_MODEL),
        model=model,
        tile=256, tile_pad=10, pre_pad=0,
        half=True,
        device=torch.device("cuda"),
    )
    print("  ready", flush=True)
    return pipe, upsampler


def generate_image(
    prompt: str,
    output_path: str,
    pipe,
    upsampler,
    seed: int = 42,
    target_w: int = 1080,
    target_h: int = 1920,
    steps: int = 25,
) -> str:
    """Generate one image. Returns output_path."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # SD at 512x512
    # 512×768 portrait — closer to 9:16, avoids heavy side-crop after ESRGAN
    result = pipe(
        prompt=prompt,
        negative_prompt=NEGATIVE,
        width=512, height=768,
        num_inference_steps=steps,
        guidance_scale=7.5,
        generator=torch.Generator("cuda").manual_seed(seed),
    )
    img = result.images[0]

    # ESRGAN 4x upscale
    arr = np.array(img.convert("RGB"))
    out_arr, _ = upsampler.enhance(arr, outscale=4)
    big = Image.fromarray(out_arr)

    # Crop to target ratio, then resize
    tw, th = target_w, target_h
    bw, bh = big.size
    ratio = tw / th
    if bw / bh > ratio:
        new_w = int(bh * ratio)
        big = big.crop(((bw - new_w) // 2, 0, (bw + new_w) // 2, bh))
    else:
        new_h = int(bw / ratio)
        big = big.crop((0, (bh - new_h) // 2, bw, (bh + new_h) // 2))

    big.resize((tw, th), Image.LANCZOS).save(output_path)
    return output_path
