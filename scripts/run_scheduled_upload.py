"""Run a scheduled video upload from GitHub Actions.

Usage: python scripts/run_scheduled_upload.py <schedule_id>
"""
import asyncio
import json
import logging
import os
import sqlite3
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

    # --- DB write-back: update upload_schedule status and platform_post_id ---
    from googleapiclient.http import MediaFileUpload

    db_drive_id = None
    db_path = Path(tempfile.mkdtemp()) / "schedule.db"
    try:
        state_folder_id = _get_subfolder("state")
        db_results = service.files().list(
            q=f"'{state_folder_id}' in parents and name='schedule_db.sqlite' and trashed=false",
            spaces="drive",
            fields="files(id)",
        ).execute()
        db_files = db_results.get("files", [])
        if db_files:
            db_drive_id = db_files[0]["id"]
            download_from_drive(db_drive_id, db_path)
            conn = sqlite3.connect(str(db_path))
            new_status = "done" if success else "failed"
            conn.execute(
                "UPDATE upload_schedule SET status=?, platform_post_id=? WHERE id=?",
                (new_status, post_id, schedule_id),
            )
            conn.commit()
            conn.close()
            media = MediaFileUpload(str(db_path))
            service.files().update(fileId=db_drive_id, media_body=media).execute()
            log.info("Schedule DB updated: schedule_id=%d status=%s", schedule_id, new_status)
        else:
            log.warning("schedule_db.sqlite not found on Drive — skipping DB write-back")
    except Exception as exc:
        log.warning("DB write-back failed: %s", exc)

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
