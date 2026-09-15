"""One-off: check every generated image for videos 225-241 for nudity/sexual content."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.image_critic import nsfw_flagged

IDS = list(range(225, 242))

flagged = []
checked = 0
for vid in IDS:
    matches = list(Path("output/images").glob(f"*_{vid}"))
    if not matches:
        print(f"{vid}: NO FOLDER")
        continue
    folder = matches[0]
    for img in sorted(folder.glob("*.png")) + sorted(folder.glob("*.jpg")):
        checked += 1
        if nsfw_flagged(img):
            flagged.append(str(img))
            print(f"FLAGGED: {img}")

print(f"\nChecked {checked} images across {len(IDS)} videos.")
print(f"Flagged: {len(flagged)}")
for f in flagged:
    print(" -", f)
