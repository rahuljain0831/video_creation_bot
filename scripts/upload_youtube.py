"""
Upload a single video to YouTube with captions from social_captions.json.

Usage:
    python scripts/upload_youtube.py <run_slug>                    # upload as private
    python scripts/upload_youtube.py <run_slug> --privacy unlisted
    python scripts/upload_youtube.py <run_slug> --privacy public
    python scripts/upload_youtube.py --list                        # show available slugs
"""

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.youtube_upload import upload_video
from pipeline.social_accounts import get_account_for_niche, load_social_config
from pipeline.social_captions import generate_social_captions
from config import cfg as app_cfg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("upload_youtube")

CAPTIONS_PATH = ROOT / "output" / "social_captions.json"


def _load_captions() -> list[dict]:
    if not CAPTIONS_PATH.exists():
        return []
    return json.loads(CAPTIONS_PATH.read_text(encoding="utf-8"))


def _save_captions(entries: list[dict]) -> None:
    CAPTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CAPTIONS_PATH.write_text(
        json.dumps(entries, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _find_caption_entry(slug: str, captions: list[dict]) -> dict | None:
    for entry in captions:
        if entry.get("run_slug") == slug:
            return entry
    return None


def list_available():
    captions = _load_captions()
    videos_dir = ROOT / "output" / "video"
    print(f"\n{'Slug':<65} {'Video':<6} {'Captions'}")
    print("-" * 85)
    # Show all videos
    seen_slugs = set()
    for mp4 in sorted(videos_dir.glob("*.mp4")):
        slug = mp4.stem
        seen_slugs.add(slug)
        has_caption = _find_caption_entry(slug, captions) is not None
        print(f"{slug:<65} {'YES':<6} {'YES' if has_caption else 'NO'}")
    # Show captions without videos
    for entry in captions:
        slug = entry.get("run_slug", "")
        if slug not in seen_slugs:
            print(f"{slug:<65} {'NO':<6} YES")


def main():
    parser = argparse.ArgumentParser(description="Upload video to YouTube")
    parser.add_argument("slug", nargs="?", help="Video run_slug")
    parser.add_argument("--privacy", default="private", choices=["private", "unlisted", "public"])
    parser.add_argument("--list", action="store_true", help="List available videos")
    parser.add_argument("--account", default=None, help="Account ID override")
    args = parser.parse_args()

    if args.list:
        list_available()
        return

    if not args.slug:
        parser.error("Provide a run_slug or use --list")

    video_path = ROOT / "output" / "video" / f"{args.slug}.mp4"
    if not video_path.exists():
        log.error("Video not found: %s", video_path)
        sys.exit(1)

    # Get captions — generate if missing
    captions = _load_captions()
    entry = _find_caption_entry(args.slug, captions)

    if not entry or not entry.get("youtube"):
        log.info("No captions found — generating via LLM...")
        script_path = ROOT / "output" / "scripts" / f"{args.slug}.json"
        if script_path.exists():
            script_data = json.loads(script_path.read_text(encoding="utf-8", errors="replace"))
            script = script_data.get("script", script_data)
            niche_id = args.slug.split("_", 2)
            # Reconstruct niche_id: handle multi-word like "scary_stories"
            niche_id = script_data.get("niche_id") or args.slug.rsplit("_", 1)[0].rsplit("_", 1)[0]
            niche_cfg = next((n for n in app_cfg.niches if n["id"] == niche_id), None)
            if not niche_cfg:
                # Try with full prefix up to last hyphen-segment
                for n in app_cfg.niches:
                    if args.slug.startswith(n["id"]):
                        niche_cfg = n
                        break
            if niche_cfg:
                result = generate_social_captions(script, niche_cfg, app_cfg)
                if result and result.get("youtube"):
                    entry = {
                        "run_slug": args.slug,
                        "story_title": script.get("story_title", ""),
                        "youtube": result["youtube"],
                    }
                    # Save for future use
                    captions.append({
                        "run_slug": args.slug,
                        "niche": niche_cfg["id"],
                        "story_title": script.get("story_title", ""),
                        **result,
                    })
                    _save_captions(captions)
                    log.info("Captions generated and saved")
                else:
                    log.warning("Caption generation returned empty")
            else:
                log.warning("Could not find niche config for %s", args.slug)
        else:
            log.warning("No script file found at %s", script_path)

    if entry and entry.get("youtube"):
        yt = entry["youtube"]
        # "title" key from batch_generate, "caption" key from social_captions
        title = yt.get("title") or entry.get("story_title") or args.slug
        description = yt.get("description") or yt.get("caption", "")
        hashtags = yt.get("hashtags", [])
        if hashtags:
            description += "\n\n" + " ".join(hashtags)
    else:
        log.warning("No captions available — using slug as title")
        title = args.slug.replace("-", " ").replace("_", " ").title()
        description = ""
        hashtags = []

    # Find credentials
    niche = args.slug.split("_")[0]
    # Try niche-specific YouTube account, fall back to any YouTube account
    cfg = load_social_config()
    acct = None
    if args.account:
        for a in cfg.get("accounts", []):
            if a["account_id"] == args.account:
                acct = a
                break
    else:
        acct = get_account_for_niche(niche, cfg)
        if not acct:
            # Fall back: any enabled YouTube account
            for a in cfg.get("accounts", []):
                if a["platform"] == "youtube" and a.get("enabled"):
                    acct = a
                    break

    if not acct:
        log.error("No YouTube account configured. Add one to social_config.json")
        sys.exit(1)

    creds_file = ROOT / acct["credentials_file"]

    log.info("Uploading: %s", title)
    log.info("Privacy: %s", args.privacy)
    log.info("Account: %s", acct["account_id"])

    video_id = upload_video(
        video_path=video_path,
        title=title,
        description=description,
        tags=[t.lstrip("#") for t in hashtags],
        privacy=args.privacy,
        credentials_file=creds_file,
    )

    print(f"\nUploaded: https://youtu.be/{video_id}")
    print(f"Privacy: {args.privacy}")
    print(f"Title: {title}")


if __name__ == "__main__":
    main()
