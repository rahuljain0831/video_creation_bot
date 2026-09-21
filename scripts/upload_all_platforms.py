"""
Upload a single video to all enabled platforms (YouTube, Instagram, Facebook).

Reads social_config.json to determine which platforms are active, then
uploads to each one with appropriate captions.

Usage:
    python scripts/upload_all_platforms.py <video_path>
    python scripts/upload_all_platforms.py <video_path> --title "My Video" --description "About..."
    python scripts/upload_all_platforms.py <video_path> --hashtags mythology shiva hindu
    python scripts/upload_all_platforms.py <video_path> --platforms instagram facebook
    python scripts/upload_all_platforms.py <video_path> --dry-run
"""

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.social_accounts import load_social_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("upload_all_platforms")


def _find_script_json(video_path: Path) -> dict | None:
    """Try to find a matching script JSON based on video filename."""
    slug = video_path.stem
    script_path = ROOT / "output" / "scripts" / f"{slug}.json"
    if script_path.exists():
        try:
            data = json.loads(script_path.read_text(encoding="utf-8", errors="replace"))
            log.info("Found script JSON: %s", script_path)
            return data
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Could not read script JSON %s: %s", script_path, exc)
    return None


def _extract_title_from_script(script_data: dict) -> str | None:
    """Extract a human-readable title from a script JSON."""
    script = script_data.get("script", script_data)
    title = script.get("story_title") or script.get("title")
    if title:
        return title
    return None


def _extract_niche_from_slug(slug: str) -> str:
    """Best-effort niche extraction from a slug like 'mythology_story-of-shiva_42'."""
    # Known multi-word niches
    for prefix in ("scary_stories", ):
        if slug.startswith(prefix):
            return prefix
    return slug.split("_")[0]


def _build_description(
    title: str,
    description: str | None,
    hashtags: list[str] | None,
) -> str:
    """Build a full description string with hashtags appended."""
    parts = []
    if description:
        parts.append(description)
    if hashtags:
        tag_str = " ".join(
            t if t.startswith("#") else f"#{t}" for t in hashtags
        )
        parts.append(tag_str)
    return "\n\n".join(parts) if parts else title


def _get_accounts_for_platform(
    platform: str, cfg: dict, niche_id: str | None = None,
) -> list[dict]:
    """Return the enabled accounts a video should be posted to on `platform`.

    An account with `"exclusive": true` serves only its own niche, and that
    niche posts nowhere else — on ANY platform, so a batch script that schedules
    it on a platform it has no exclusive account for gets nothing back rather
    than a legacy account. Every other account keeps the original behaviour
    (all videos go to all of them), so existing niches are unaffected. Without
    a niche_id (the manual CLI) exclusive accounts are never used.
    """
    platform_cfg = cfg.get("platforms", {}).get(platform, {})
    if not platform_cfg.get("enabled"):
        return []
    all_accounts = [a for a in cfg.get("accounts", []) if a.get("enabled")]
    accounts = [a for a in all_accounts if a["platform"] == platform]
    if niche_id is not None and any(
        a.get("exclusive") and a["niche"] == niche_id for a in all_accounts
    ):
        return [a for a in accounts if a.get("exclusive") and a["niche"] == niche_id]
    return [a for a in accounts if not a.get("exclusive")]


def _upload_youtube(
    video_path: Path, title: str, description: str,
    hashtags: list[str], account: dict, dry_run: bool,
    media_type: str = "video",
) -> dict:
    """Upload to YouTube, return result dict."""
    if media_type != "video":
        return {"platform": "youtube", "account": account["account_id"],
                "status": "error", "error": "youtube takes video only"}
    creds_file = ROOT / account["credentials_file"]
    if not creds_file.exists():
        return {"platform": "youtube", "account": account["account_id"],
                "status": "error", "error": f"Credentials not found: {creds_file}"}

    if dry_run:
        return {"platform": "youtube", "account": account["account_id"],
                "status": "dry_run", "credentials": str(creds_file)}

    from pipeline.youtube_upload import upload_video
    video_id = upload_video(
        video_path=video_path,
        title=title[:100],
        description=description,
        tags=[t.lstrip("#") for t in hashtags] if hashtags else [],
        privacy="public",
        credentials_file=creds_file,
    )
    return {
        "platform": "youtube",
        "account": account["account_id"],
        "status": "success",
        "video_id": video_id,
        "url": f"https://youtu.be/{video_id}",
    }


def _upload_instagram(
    video_path: Path, title: str, description: str,
    hashtags: list[str], account: dict, dry_run: bool,
    media_type: str = "video",
) -> dict:
    """Upload to Instagram as a Reel (or a feed photo when media_type='image')."""
    creds_file = ROOT / account["credentials_file"]
    if not creds_file.exists():
        return {"platform": "instagram", "account": account["account_id"],
                "status": "error", "error": f"Credentials not found: {creds_file}"}

    if dry_run:
        return {"platform": "instagram", "account": account["account_id"],
                "status": "dry_run", "credentials": str(creds_file)}

    # Instagram uses caption (title + description combined)
    caption = title
    if description and description != title:
        caption = f"{title}\n\n{description}"

    if media_type == "image":
        from pipeline.instagram_upload import upload_image_post
        media_id = upload_image_post(
            image_path=video_path, caption=caption,
            hashtags=hashtags, credentials_file=creds_file,
        )
    else:
        from pipeline.instagram_upload import upload_reel
        media_id = upload_reel(
            video_path=video_path, caption=caption,
            hashtags=hashtags, credentials_file=creds_file,
        )
    return {
        "platform": "instagram",
        "account": account["account_id"],
        "status": "success",
        "media_id": media_id,
    }


def _upload_facebook(
    video_path: Path, title: str, description: str,
    hashtags: list[str], account: dict, dry_run: bool,
    media_type: str = "video",
) -> dict:
    """Upload to Facebook Page, return result dict."""
    if media_type != "video":
        return {"platform": "facebook", "account": account["account_id"],
                "status": "error", "error": "facebook takes video only"}
    creds_file = ROOT / account["credentials_file"]
    if not creds_file.exists():
        return {"platform": "facebook", "account": account["account_id"],
                "status": "error", "error": f"Credentials not found: {creds_file}"}

    if dry_run:
        return {"platform": "facebook", "account": account["account_id"],
                "status": "dry_run", "credentials": str(creds_file)}

    from pipeline.facebook_upload import upload_video
    full_desc = _build_description(title, description, hashtags)
    video_id = upload_video(
        video_path=video_path,
        title=title,
        description=full_desc,
        credentials_file=creds_file,
    )
    return {
        "platform": "facebook",
        "account": account["account_id"],
        "status": "success",
        "video_id": video_id,
    }


_PLATFORM_UPLOADERS = {
    "youtube": _upload_youtube,
    "instagram": _upload_instagram,
    "facebook": _upload_facebook,
}


def upload_all(
    video_path: Path,
    title: str,
    description: str | None = None,
    hashtags: list[str] | None = None,
    platforms_filter: list[str] | None = None,
    dry_run: bool = False,
    niche_id: str | None = None,
    media_type: str = "video",
) -> list[dict]:
    """
    Upload a video to all enabled platforms.

    Args:
        video_path: Path to the mp4 file
        title: Video title
        description: Video description (optional)
        hashtags: List of hashtags (optional)
        platforms_filter: If set, only upload to these platforms
        dry_run: If True, show what would happen without uploading
        niche_id: Routes to an exclusive account for that niche, if one exists
                  (see _get_accounts_for_platform)
        media_type: "video" (default, a Reel) or "image" (an Instagram feed photo).

    Returns:
        List of result dicts, one per account attempted
    """
    cfg = load_social_config()
    results = []
    hashtags = hashtags or []
    full_description = _build_description(title, description, hashtags)

    for platform, uploader in _PLATFORM_UPLOADERS.items():
        if platforms_filter and platform not in platforms_filter:
            continue

        accounts = _get_accounts_for_platform(platform, cfg, niche_id)
        if not accounts:
            if platforms_filter and platform in platforms_filter:
                log.warning(
                    "Platform '%s' requested but not enabled or no accounts in social_config.json",
                    platform,
                )
            continue

        for account in accounts:
            log.info(
                "%s upload to %s (account: %s)...",
                "DRY RUN:" if dry_run else "Starting",
                platform, account["account_id"],
            )
            try:
                result = uploader(
                    video_path, title, full_description,
                    hashtags, account, dry_run, media_type,
                )
                results.append(result)
            except Exception as exc:
                log.error("Upload to %s failed: %s", platform, exc)
                results.append({
                    "platform": platform,
                    "account": account["account_id"],
                    "status": "error",
                    "error": str(exc),
                })

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Upload a video to all enabled social platforms",
    )
    parser.add_argument("video_path", help="Path to the mp4 video file")
    parser.add_argument("--title", default=None, help="Video title (auto-detected from script JSON if omitted)")
    parser.add_argument("--description", default=None, help="Video description")
    parser.add_argument("--hashtags", nargs="+", default=None, help="Hashtags (e.g. mythology shiva)")
    parser.add_argument("--platforms", nargs="+", default=None,
                        help="Only upload to these platforms (e.g. instagram youtube facebook)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would happen without actually uploading")
    args = parser.parse_args()

    video_path = Path(args.video_path)
    if not video_path.exists():
        log.error("Video not found: %s", video_path)
        sys.exit(1)
    if video_path.stat().st_size == 0:
        log.error("Video file is empty (0 bytes): %s", video_path)
        sys.exit(1)

    # Resolve title
    title = args.title
    description = args.description
    if not title:
        script_data = _find_script_json(video_path)
        if script_data:
            title = _extract_title_from_script(script_data)
            if not description:
                script = script_data.get("script", script_data)
                description = script.get("description") or script.get("synopsis")
        if not title:
            # Fall back to slug-based title
            title = video_path.stem.replace("-", " ").replace("_", " ").title()
            log.info("No title found -- using slug: %s", title)

    log.info("Title: %s", title)
    log.info("Description: %s", description or "(none)")
    log.info("Hashtags: %s", args.hashtags or "(none)")
    if args.platforms:
        log.info("Platforms filter: %s", args.platforms)
    if args.dry_run:
        log.info("DRY RUN MODE -- no uploads will be performed")

    print()

    results = upload_all(
        video_path=video_path,
        title=title,
        description=description,
        hashtags=args.hashtags,
        platforms_filter=args.platforms,
        dry_run=args.dry_run,
    )

    # Print summary
    print("\n" + "=" * 60)
    print("UPLOAD RESULTS")
    print("=" * 60)

    if not results:
        print("No platforms were uploaded to.")
        print("Check social_config.json to enable platforms and accounts.")
        sys.exit(1)

    for r in results:
        status = r["status"].upper()
        platform = r["platform"]
        account = r["account"]

        if status == "SUCCESS":
            url = r.get("url", "")
            vid = r.get("video_id") or r.get("media_id", "")
            detail = url if url else f"ID: {vid}"
            print(f"  {platform:<12} {account:<25} OK     {detail}")
        elif status == "DRY_RUN":
            creds = r.get("credentials", "")
            print(f"  {platform:<12} {account:<25} DRY    creds: {creds}")
        else:
            error = r.get("error", "unknown error")
            print(f"  {platform:<12} {account:<25} FAIL   {error}")

    print()

    # Exit with error code if any uploads failed
    if any(r["status"] == "error" for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
