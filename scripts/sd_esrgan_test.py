"""
Method 2: SD1.5 512p + Real-ESRGAN upscale to 720/1080p.
Compares upscaled output vs SD-direct at same resolution.
Saves side-by-sides to output/esrgan_test/.
Run: python scripts/sd_esrgan_test.py
"""
import time, sys
from pathlib import Path
import torch
import numpy as np
from PIL import Image
from basicsr.archs.rrdbnet_arch import RRDBNet
from realesrgan import RealESRGANer

ROOT = Path(__file__).parent.parent
SRC  = ROOT / "output/feasibility_test"
OUT  = ROOT / "output/esrgan_test"
OUT.mkdir(parents=True, exist_ok=True)

MODEL_URL  = "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth"
MODEL_PATH = ROOT / "output/esrgan_test/RealESRGAN_x4plus.pth"

# 9:16 portrait target sizes
TARGETS = {
    "720p":  (720,  1280),
    "1080p": (1080, 1920),
}

SUBJECTS = ["anime", "kitchen", "scenery", "space"]


def download_model():
    if MODEL_PATH.exists():
        return
    print("Downloading RealESRGAN_x4plus.pth (~67MB)...", flush=True)
    import urllib.request
    urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
    print(f"  saved {MODEL_PATH}", flush=True)


def build_upsampler():
    model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64,
                    num_block=23, num_grow_ch=32, scale=4)
    return RealESRGANer(
        scale=4,
        model_path=str(MODEL_PATH),
        model=model,
        tile=256,          # tile to keep VRAM low; ponytail: global tile size, tune if OOM
        tile_pad=10,
        pre_pad=0,
        half=True,         # fp16 on GPU
        device=torch.device("cuda"),
    )


def upscale_to(upsampler, img_path: Path, target_w: int, target_h: int) -> Image.Image:
    """Upscale with ESRGAN (4x), then crop/resize to exact target."""
    img = Image.open(img_path).convert("RGB")
    arr = np.array(img)
    out_arr, _ = upsampler.enhance(arr, outscale=4)
    big = Image.fromarray(out_arr)
    # Crop to target aspect, then resize
    big_w, big_h = big.size
    target_ratio = target_w / target_h
    big_ratio = big_w / big_h
    if big_ratio > target_ratio:
        # Too wide — crop width
        new_w = int(big_h * target_ratio)
        left = (big_w - new_w) // 2
        big = big.crop((left, 0, left + new_w, big_h))
    elif big_ratio < target_ratio:
        # Too tall — crop height
        new_h = int(big_w / target_ratio)
        top = (big_h - new_h) // 2
        big = big.crop((0, top, big_w, top + new_h))
    return big.resize((target_w, target_h), Image.LANCZOS)


def side_by_side(left: Image.Image, right: Image.Image,
                 label_l: str, label_r: str) -> Image.Image:
    from PIL import ImageDraw
    w, h = left.size
    canvas = Image.new("RGB", (w * 2 + 10, h), (30, 30, 30))
    canvas.paste(left, (0, 0))
    canvas.paste(right, (w + 10, 0))
    draw = ImageDraw.Draw(canvas)
    draw.text((4, 4),     label_l, fill=(255, 255, 0))
    draw.text((w + 14, 4), label_r, fill=(255, 255, 0))
    return canvas


def main():
    download_model()
    print("Building upsampler...", flush=True)
    upsampler = build_upsampler()
    print("  ready\n", flush=True)

    for subject in SUBJECTS:
        src = SRC / f"{subject}_512p_native.png"
        if not src.exists():
            print(f"  SKIP {subject}: no 512p source", flush=True)
            continue

        for label, (tw, th) in TARGETS.items():
            t0 = time.time()
            upscaled = upscale_to(upsampler, src, tw, th)
            elapsed = time.time() - t0

            # Load SD-direct at same res for comparison
            direct_path = SRC / f"{subject}_{label}_portrait_9x16.png"
            out_path = OUT / f"{subject}_{label}_compare.png"

            if direct_path.exists():
                direct = Image.open(direct_path).convert("RGB").resize((tw, th), Image.LANCZOS)
                compare = side_by_side(upscaled, direct, "ESRGAN upscale", "SD direct")
                compare.save(out_path)
                print(f"  {subject} {label}: upscaled in {elapsed:.1f}s — saved {out_path.name}", flush=True)
            else:
                upscaled.save(OUT / f"{subject}_{label}_esrgan.png")
                print(f"  {subject} {label}: upscaled in {elapsed:.1f}s (no direct to compare)", flush=True)

    print("\nDone. Open output/esrgan_test/ and compare left (ESRGAN) vs right (SD direct).")


if __name__ == "__main__":
    main()
