"""Run a scheduled video upload from GitHub Actions.

Usage:
    python scripts/run_scheduled_upload.py <schedule_id>   # process specific schedule
    python scripts/run_scheduled_upload.py                 # poll: process all due uploads
"""
import asyncio
import json
import logging
import os
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

UPLOAD_MAX_ATTEMPTS = 3
UPLOAD_RETRY_BASE_SLEEP = 30   # seconds; doubles each attempt

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("scheduled_upload")


def _notify_telegram(platform, title, success, post_id="", extra=""):
    """Send Telegram notification about upload result."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not bot_token or not chat_id:
        return

    emoji = "✅" if success else "❌"
    msg = f"{emoji} Upload {platform.title()}: {title}"
    if post_id and platform == "youtube":
        msg += f"\nhttps://youtu.be/{post_id}"
    elif post_id:
        msg += f"\nPost ID: {post_id}"
    if extra:
        msg += f"\n{extra}"

    async def notify():
        from telegram import Bot
        from telegram.request import HTTPXRequest
        async with Bot(token=bot_token, request=HTTPXRequest(connect_timeout=30, read_timeout=60)) as bot:
            await bot.send_message(chat_id=chat_id, text=msg)

    try:
        asyncio.run(notify())
    except Exception as e:
        log.warning("Telegram notify failed: %s", e)


def _is_transient(error: Exception) -> bool:
    """True for errors worth retrying (network, rate-limit, server error)."""
    msg = str(error).lower()
    # Auth errors are permanent — retrying won't help
    if any(x in msg for x in ("401", "403", "unauthorized", "forbidden", "invalid_token")):
        return False
    # File/config errors are permanent
    if isinstance(error, (FileNotFoundError, ValueError, json.JSONDecodeError)):
        return False
    # Rate-limit and server errors are transient
    if any(x in msg for x in ("429", "500", "502", "503", "504", "timeout", "connection")):
        return True
    # Default: retry unknown errors (safer than silently dropping)
    return True


def _notify_token_alert(platform: str, error: Exception) -> None:
    """Send Telegram alert when upload fails with an auth error."""
    msg = str(error).lower()
    if not any(x in msg for x in ("401", "403", "unauthorized", "forbidden", "invalid_token")):
        return

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not bot_token or not chat_id:
        return

    alert = (
        f"\U0001f511 *{platform.title()} token expired*\n"
        f"Upload rejected with auth error. Re-run auth setup:\n"
        f"`python scripts/{platform}_auth_setup.py`"
    )

    async def notify():
        from telegram import Bot
        from telegram.request import HTTPXRequest
        async with Bot(token=bot_token, request=HTTPXRequest(connect_timeout=30, read_timeout=60)) as bot:
            await bot.send_message(chat_id=chat_id, text=alert, parse_mode="Markdown")

    try:
        asyncio.run(notify())
    except Exception as e:
        log.warning("Token alert Telegram send failed: %s", e)


_MAX_MANIFEST_RETRIES = 3   # manifest-level retries (each gets UPLOAD_MAX_ATTEMPTS per-run tries)
_RETRY_DELAY_HOURS = 6


def _load_state_db(service):
    """Pull the persistent schedule DB from Drive state/ so posted rows can be
    marked done. Without this, upload_schedule.status/platform_post_id never
    get set, and engagement_tracker.fetch_engagement() has nothing to query —
    it filters on exactly those two columns. Returns (conn, db_path, db_file_id),
    all None if the DB can't be reached (degrades to "upload works, engagement
    tracking doesn't" rather than failing the upload)."""
    from pipeline.drive_storage import _get_subfolder, _retry_drive, download_from_drive
    from config import cfg

    try:
        state_folder_id = _get_subfolder("state")
        req = service.files().list(
            q=f"'{state_folder_id}' in parents and name='schedule_db.sqlite' and trashed=false",
            spaces="drive", fields="files(id)",
        )
        files = _retry_drive(req.execute).get("files", [])
    except Exception as e:
        log.warning("Could not reach Drive state folder (%s) — schedule DB won't be updated", e)
        return None, None, None

    if not files:
        log.warning("No schedule_db.sqlite on Drive state — schedule DB won't be updated")
        return None, None, None

    db_path = Path(cfg.paths["db"])
    db_path.parent.mkdir(parents=True, exist_ok=True)
    download_from_drive(files[0]["id"], db_path)
    return sqlite3.connect(str(db_path)), db_path, files[0]["id"]


def _save_state_db(service, db_path, db_file_id) -> None:
    from googleapiclient.http import MediaFileUpload
    from pipeline.drive_storage import _retry_drive

    media = MediaFileUpload(str(db_path))
    _retry_drive(service.files().update(fileId=db_file_id, media_body=media).execute)
    log.info("Schedule DB synced back to Drive state")


def _write_retry_manifest(manifest: dict, retry_count: int) -> None:
    """Write a new manifest to Drive pending/ with updated scheduled_at and retry_count."""
    from pipeline.drive_storage import upload_to_drive

    new = {
        **manifest,
        "retry_count": retry_count,
        "scheduled_at": (
            datetime.now(timezone.utc) + timedelta(hours=_RETRY_DELAY_HOURS)
        ).strftime("%Y-%m-%d %H:%M:%S"),
    }
    schedule_id = manifest["schedule_id"]
    tmp = Path(tempfile.mkdtemp()) / f"{schedule_id}_retry{retry_count}_schedule.json"
    tmp.write_text(json.dumps(new, indent=2))
    upload_to_drive(tmp, folder_name="pending")
    log.info("Retry manifest written: schedule_id=%d retry=%d/%d",
             schedule_id, retry_count, _MAX_MANIFEST_RETRIES)


def process_schedule(manifest, manifest_drive_id, service, conn=None):
    """Process a single schedule manifest. Returns True on success."""
    from pipeline.drive_storage import download_from_drive, move_drive_file

    _REQUIRED_KEYS = ("schedule_id", "platform", "drive_file_id")
    missing = [k for k in _REQUIRED_KEYS if k not in manifest]
    if missing:
        log.error("Manifest missing required keys %s — skipping", missing)
        return False

    schedule_id = manifest["schedule_id"]
    platform = manifest["platform"]
    drive_file_id = manifest["drive_file_id"]
    title = manifest.get("title", "Untitled")

    media_type = manifest.get("media_type", "video")
    suffix = ".jpg" if media_type == "image" else ".mp4"

    log.info("Processing schedule_id=%d platform=%s media=%s title=%s",
             schedule_id, platform, media_type, title)

    tmp_dir = Path(tempfile.mkdtemp())
    video_path = tmp_dir / f"media{suffix}"
    download_from_drive(drive_file_id, video_path)
    log.info("Downloaded media: %s (%d bytes)", video_path, video_path.stat().st_size)

    last_error = None
    upload_results = []
    from scripts.upload_all_platforms import upload_all
    for attempt in range(1, UPLOAD_MAX_ATTEMPTS + 1):
        try:
            upload_results = upload_all(
                video_path=video_path,
                title=title,
                description=manifest.get("caption", ""),
                hashtags=manifest.get("hashtags", []),
                platforms_filter=[platform],
                niche_id=manifest.get("niche_id"),
                media_type=media_type,
            )
            last_error = None
            break   # success
        except Exception as e:
            last_error = e
            if not _is_transient(e) or attempt == UPLOAD_MAX_ATTEMPTS:
                log.error("Upload failed (attempt %d/%d, non-retryable): %s",
                          attempt, UPLOAD_MAX_ATTEMPTS, e)
                break
            sleep = UPLOAD_RETRY_BASE_SLEEP * (2 ** (attempt - 1))
            log.warning("Upload attempt %d/%d failed (%s) — retrying in %ds",
                        attempt, UPLOAD_MAX_ATTEMPTS, e, sleep)
            time.sleep(sleep)

    if last_error is not None:
        log.error("Upload failed for schedule_id=%d after %d attempts: %s",
                  schedule_id, UPLOAD_MAX_ATTEMPTS, last_error)
        _notify_token_alert(platform, last_error)

        retry_count = manifest.get("retry_count", 0) + 1
        if _is_transient(last_error) and retry_count <= _MAX_MANIFEST_RETRIES:
            # Reschedule — don't move to failed/ yet
            try:
                _write_retry_manifest(manifest, retry_count)
                retry_at = (datetime.now(timezone.utc) + timedelta(hours=_RETRY_DELAY_HOURS))
                retry_ist = (retry_at + timedelta(hours=5, minutes=30)).strftime("%H:%M IST")
                _notify_telegram(
                    platform, title, False,
                    extra=f"Retry {retry_count}/{_MAX_MANIFEST_RETRIES} scheduled {retry_ist}",
                )
                log.info("Rescheduled: schedule_id=%d retry=%d/%d at +%dh",
                         schedule_id, retry_count, _MAX_MANIFEST_RETRIES, _RETRY_DELAY_HOURS)
            except Exception as exc:
                log.error("Failed to write retry manifest: %s", exc)
                _notify_telegram(platform, title, False)
        else:
            _notify_telegram(platform, title, False)
        return False

    success = False
    post_id = ""
    for r in upload_results:
        log.info("  %s: %s", r["platform"], r["status"])
        if r["status"] == "success":
            success = True
            post_id = r.get("video_id") or r.get("media_id", "")

    if conn is not None:
        try:
            conn.execute(
                "UPDATE upload_schedule SET status=?, platform_post_id=? WHERE id=?",
                ("done" if success else "failed", post_id or None, schedule_id),
            )
            conn.commit()
        except Exception as e:
            log.warning("Failed to update schedule DB row id=%d: %s", schedule_id, e)

    # Move video on Drive
    dest = "uploaded" if success else "failed"
    try:
        move_drive_file(drive_file_id, dest)
        if manifest_drive_id:
            move_drive_file(manifest_drive_id, dest)
    except Exception as e:
        log.warning("Drive move failed: %s", e)

    _notify_telegram(platform, title, success, post_id)
    return success


def find_due_manifests(service):
    """Scan Drive pending folder for schedule manifests that are due now."""
    from pipeline.drive_storage import download_from_drive, _get_subfolder

    folder_id = _get_subfolder("pending")
    results = service.files().list(
        q=f"'{folder_id}' in parents and name contains '_schedule.json' and trashed=false",
        spaces="drive",
        fields="files(id, name)",
        includeItemsFromAllDrives=True, supportsAllDrives=True,
    ).execute()

    now_utc = datetime.now(timezone.utc)
    due = []

    tmp_dir = Path(tempfile.mkdtemp())
    for f in results.get("files", []):
        local = tmp_dir / f["name"]
        download_from_drive(f["id"], local)
        try:
            data = json.loads(local.read_text())
        except Exception:
            continue

        scheduled_str = data.get("scheduled_at", "")
        if not scheduled_str:
            continue

        scheduled_at = datetime.strptime(scheduled_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        if scheduled_at <= now_utc:
            due.append((data, f["id"]))
            log.info("Due: schedule_id=%d platform=%s scheduled=%s",
                     data.get("schedule_id"), data.get("platform"), scheduled_str)

    return due


def main():
    from pipeline.drive_storage import _build_service

    service = _build_service()
    conn, db_path, db_file_id = _load_state_db(service)

    if len(sys.argv) > 1 and sys.argv[1]:
        # Specific schedule_id mode
        schedule_id = int(sys.argv[1])
        log.info("Processing specific schedule_id=%d", schedule_id)

        from pipeline.drive_storage import download_from_drive, _get_subfolder
        folder_id = _get_subfolder("pending")
        results = service.files().list(
            q=f"'{folder_id}' in parents and name contains '_schedule.json' and trashed=false",
            spaces="drive",
            fields="files(id, name)",
            includeItemsFromAllDrives=True, supportsAllDrives=True,
        ).execute()

        tmp_dir = Path(tempfile.mkdtemp())
        for f in results.get("files", []):
            local = tmp_dir / f["name"]
            download_from_drive(f["id"], local)
            data = json.loads(local.read_text())
            if data.get("schedule_id") == schedule_id:
                success = process_schedule(data, f["id"], service, conn)
                if conn is not None:
                    _save_state_db(service, db_path, db_file_id)
                sys.exit(0 if success else 1)

        log.warning("No manifest found for schedule_id=%d — likely processed by concurrent run", schedule_id)
        sys.exit(0)

    else:
        # Poll mode: find and process all due uploads
        log.info("Poll mode: checking for due uploads...")
        due = find_due_manifests(service)

        if not due:
            log.info("No uploads due right now")
            sys.exit(0)

        log.info("Found %d due uploads", len(due))
        failures = 0
        for manifest, drive_id in due:
            if not process_schedule(manifest, drive_id, service, conn):
                failures += 1

        if conn is not None:
            _save_state_db(service, db_path, db_file_id)

        log.info("Done: %d processed, %d failed", len(due), failures)
        sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
