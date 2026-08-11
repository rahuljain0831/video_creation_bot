"""Run a scheduled video upload from GitHub Actions.

Usage: python scripts/run_scheduled_upload.py <schedule_id>
"""
import asyncio
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("scheduled_upload")


def main():
    schedule_id = int(sys.argv[1])
    log.info("Processing schedule_id=%d", schedule_id)

    from pipeline.drive_storage import download_from_drive, move_drive_file, _build_service, _get_subfolder
    from pipeline.scheduler import delete_upload_job

    service = _build_service()
    folder_id = _get_subfolder("pending")

    results = service.files().list(
        q=f"'{folder_id}' in parents and name contains '_schedule.json' and trashed=false",
        spaces="drive",
        fields="files(id, name)",
    ).execute()

    tmp_dir = Path(tempfile.mkdtemp())
    manifest = None
    manifest_drive_id = None

    for f in results.get("files", []):
        local = tmp_dir / f["name"]
        download_from_drive(f["id"], local)
        data = json.loads(local.read_text())
        if data.get("schedule_id") == schedule_id:
            manifest = data
            manifest_drive_id = f["id"]
            break

    if not manifest:
        log.error("No manifest found for schedule_id=%d", schedule_id)
        sys.exit(1)

    platform = manifest["platform"]
    drive_file_id = manifest["drive_file_id"]
    caption = manifest.get("caption", "")
    hashtags = manifest.get("hashtags", [])
    title = manifest.get("title", "Untitled")

    video_path = tmp_dir / "video.mp4"
    download_from_drive(drive_file_id, video_path)
    log.info("Downloaded video: %s (%d bytes)", video_path, video_path.stat().st_size)

    from scripts.upload_all_platforms import upload_all
    upload_results = upload_all(
        video_path=video_path,
        title=title,
        description=caption,
        hashtags=hashtags,
        platforms_filter=[platform],
    )

    success = False
    post_id = ""
    for r in upload_results:
        log.info("  %s: %s", r["platform"], r["status"])
        if r["status"] == "success":
            success = True
            post_id = r.get("video_id") or r.get("media_id", "")

    dest = "uploaded" if success else "failed"
    move_drive_file(drive_file_id, dest)
    if manifest_drive_id:
        move_drive_file(manifest_drive_id, dest)

    cronjob_id = manifest.get("cronjob_id", "")
    if cronjob_id:
        delete_upload_job(cronjob_id)

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if bot_token and chat_id:
        emoji = "✅" if success else "❌"
        msg = f"{emoji} Upload {platform.title()}: {title}"
        if post_id and platform == "youtube":
            msg += f"\nhttps://youtu.be/{post_id}"
        elif post_id:
            msg += f"\nPost ID: {post_id}"

        async def notify():
            from telegram import Bot
            from telegram.request import HTTPXRequest
            async with Bot(token=bot_token, request=HTTPXRequest(connect_timeout=30, read_timeout=60)) as bot:
                await bot.send_message(chat_id=chat_id, text=msg)
        try:
            asyncio.run(notify())
        except Exception as e:
            log.warning("Telegram notify failed: %s", e)

    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
