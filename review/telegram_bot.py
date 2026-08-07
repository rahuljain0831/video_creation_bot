"""
Phase 6 — Telegram review bot.

Flow:
  1. Worker calls send_for_review(video_id, file_path, quote_text)
  2. Bot sends video (no buttons); captions message sent separately with Approve/Reject
  3. Tap Approve → status='approved'
  4. Tap Reject → bot edits message to ask for feedback text
  5. User replies with feedback → if retry_count < 3: run_retry in background thread
                                  else: permanently_rejected
"""
import asyncio
import html as html_lib
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import cfg
from feedback.parser import parse_tags

log = logging.getLogger(__name__)

_APPROVE = "approve"
_REJECT  = "reject"
_INSTAGRAM = "ig_upload"
_INSTAGRAM_RETRY = "ig_retry"
_SKIP_SOCIAL = "skip_social"

MAX_RETRIES = 3

# Shared thread pool for pipeline work (retries + future new-video tasks)
_executor = ThreadPoolExecutor(max_workers=4)

# chat_id → (feedback_id, video_id, retry_count)  — awaiting rejection feedback text
_pending_retry: dict[int, tuple[int, int, int]] = {}

# chat_id → feedback_id  — awaiting optional tag text after approve
_pending_text: dict[int, int] = {}

# chat_id → (feedback_id, video_id)  — awaiting platform selection after approve
_pending_platform: dict[int, tuple[int, int]] = {}

# video_id → account_id  — for Instagram uploads
_instagram_videos: dict[int, str] = {}


# ── Send video for review ─────────────────────────────────────────────────────

def _build_quota_summary_text(conn: sqlite3.Connection) -> str:
    """Build a human-readable quota status line for Telegram captions."""
    try:
        from pipeline.quota_tracker import get_quota_summary
        summary = get_quota_summary(conn)
        lines = [f"{p}: {v}" for p, v in summary.items()]
        return "Quota: " + " | ".join(lines)
    except Exception as e:
        log.warning("quota summary failed: %s", e)
        return ""


async def _send_video_async(
    video_id: int,
    file_path: str,
    quote_text: str,
    conn: sqlite3.Connection | None = None,
) -> None:
    from telegram.request import HTTPXRequest
    async with Bot(
        token=cfg.TELEGRAM_BOT_TOKEN,
        request=HTTPXRequest(read_timeout=120, write_timeout=120,
                             connect_timeout=30, media_write_timeout=300),
    ) as bot:
        # Include quota status + waiting_quota count in caption
        quota_line = ""
        if conn is not None:
            quota_line = _build_quota_summary_text(conn)
            waiting_count = conn.execute(
                "SELECT COUNT(*) FROM videos WHERE status='waiting_quota'"
            ).fetchone()[0]
            if waiting_count:
                quota_line += f" | Waiting: {waiting_count}"

        caption = f'"{quote_text}"\n\n<i>video_id={video_id}</i>'
        if quota_line:
            caption += f"\n<i>{quota_line}</i>"

        with open(file_path, "rb") as f:
            await bot.send_video(
                chat_id=cfg.TELEGRAM_CHAT_ID,
                video=f,
                caption=caption,
                parse_mode="HTML",
                supports_streaming=True,
            )
        log.info("Sent video_id=%d to Telegram", video_id)


def send_for_review(
    video_id: int,
    file_path: str,
    quote_text: str,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Sync wrapper — safe to call from the worker."""
    if not cfg.TELEGRAM_BOT_TOKEN or not cfg.TELEGRAM_CHAT_ID:
        log.warning("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — skipping send")
        return
    if not Path(file_path).exists():
        log.error("Video file not found: %s", file_path)
        return
    asyncio.run(_send_video_async(video_id, file_path, quote_text, conn))


# ── Button callback ───────────────────────────────────────────────────────────

async def _handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    action, video_id_str = query.data.split(":", 1)
    video_id = int(video_id_str)
    chat_id  = query.message.chat_id

    conn = sqlite3.connect(cfg.paths["db"])
    conn.execute("PRAGMA journal_mode=WAL")

    if action == _APPROVE:
        conn.execute("UPDATE videos SET status='approved' WHERE id=?", (video_id,))
        conn.execute(
            "INSERT INTO feedback (video_id, rating, source) VALUES (?, 'good', 'manual')",
            (video_id,),
        )
        conn.commit()
        feedback_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()

        _pending_platform[chat_id] = (feedback_id, video_id)
        log.info("video_id=%d approved (feedback_id=%d)", video_id, feedback_id)

        label = "✅ Approved"
        suffix = "<i>Select a platform to post (optional)</i>"

    else:
        # Read current retry_count to show user how many retries remain
        row = conn.execute(
            "SELECT retry_count FROM videos WHERE id=?", (video_id,)
        ).fetchone()
        retry_count = row[0] if row else 0

        conn.execute("UPDATE videos SET status='rejected' WHERE id=?", (video_id,))
        conn.execute(
            "INSERT INTO feedback (video_id, rating, source) VALUES (?, 'bad', 'manual')",
            (video_id,),
        )
        conn.commit()
        feedback_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()

        _pending_retry[chat_id] = (feedback_id, video_id, retry_count)
        log.info(
            "video_id=%d rejected (feedback_id=%d retry_count=%d)",
            video_id, feedback_id, retry_count,
        )

        retries_left = MAX_RETRIES - retry_count
        label = "❌ Rejected"
        suffix = (
            f"<i>Reply with what needs changing ({retries_left} retr{'y' if retries_left == 1 else 'ies'} left)</i>"
        )

    try:
        if query.message.caption is not None:
            # Media message (video/photo) — edit caption
            original = html_lib.escape(query.message.caption or "")
            new_caption = f"{original}\n\n<b>{label}</b>\n{suffix}"
            if len(new_caption) > 1020:
                new_caption = original[:800] + f"...\n\n<b>{label}</b>\n{suffix}"

            if action == _APPROVE:
                # Send platform selection buttons
                keyboard = [
                    [InlineKeyboardButton("📸 Post to Instagram", callback_data=f"{_INSTAGRAM}:{video_id}")],
                    [InlineKeyboardButton("📋 Later", callback_data=f"{_SKIP_SOCIAL}:{video_id}")],
                ]
                await query.edit_message_caption(
                    caption=new_caption,
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
            else:
                await query.edit_message_caption(
                    caption=new_caption,
                    parse_mode="HTML",
                    reply_markup=None,
                )
        else:
            # Plain text message (social captions) — just remove keyboard + reply status
            await query.edit_message_reply_markup(reply_markup=None)
            msg = await context.bot.send_message(
                chat_id=chat_id,
                text=f"video_id={video_id}: <b>{label}</b>\n{suffix}",
                parse_mode="HTML",
            )

            if action == _APPROVE:
                # Send platform selection as separate message
                keyboard = [
                    [InlineKeyboardButton("📸 Post to Instagram", callback_data=f"{_INSTAGRAM}:{video_id}")],
                    [InlineKeyboardButton("📋 Later", callback_data=f"{_SKIP_SOCIAL}:{video_id}")],
                ]
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="Select a platform to post this video:",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
    except Exception as e:
        if "not modified" not in str(e).lower():
            log.warning("edit_message failed (non-fatal): %s", e)


# ── Text reply handler ────────────────────────────────────────────────────────

async def _handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    text    = update.message.text.strip()

    # ── Rejection feedback path (triggers retry) ──────────────────────────────
    retry_entry = _pending_retry.pop(chat_id, None)
    if retry_entry is not None:
        feedback_id, video_id, retry_count = retry_entry

        # Save feedback text
        conn = sqlite3.connect(cfg.paths["db"])
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "UPDATE feedback SET feedback_text=? WHERE id=?",
            (text, feedback_id),
        )
        conn.commit()

        if retry_count >= MAX_RETRIES:
            conn.execute(
                "UPDATE videos SET status='permanently_rejected' WHERE id=?",
                (video_id,),
            )
            conn.commit()
            conn.close()
            log.info("video_id=%d permanently rejected after %d retries", video_id, retry_count)
            await update.message.reply_text(
                f"Max retries ({MAX_RETRIES}) reached for video {video_id}. "
                "Marked as permanently rejected."
            )
            return

        conn.close()

        # Submit retry to thread pool — non-blocking
        loop = asyncio.get_event_loop()
        loop.run_in_executor(_executor, _run_retry_safe, video_id, text, chat_id)

        retries_used = retry_count + 1
        await update.message.reply_text(
            f"Regenerating video (retry {retries_used}/{MAX_RETRIES}) with your feedback...\n"
            "New video will arrive shortly."
        )
        return

    # ── Platform selection followed by optional notes ──────────────────────────
    platform_entry = _pending_platform.pop(chat_id, None)
    if platform_entry is not None:
        feedback_id, video_id = platform_entry

        # Save optional notes
        conn = sqlite3.connect(cfg.paths["db"])
        conn.execute("PRAGMA journal_mode=WAL")
        row = conn.execute(
            "SELECT rating FROM feedback WHERE id=?", (feedback_id,)
        ).fetchone()

        if row:
            rating = row[0]
            tags = parse_tags(text, rating)

            conn.execute(
                "UPDATE feedback SET feedback_text=?, parsed_tags=? WHERE id=?",
                (text, __import__("json").dumps(tags) if tags else None, feedback_id),
            )
            conn.commit()
            log.info("feedback_id=%d notes saved, tags=%s", feedback_id, tags)
            reply = f"Tags: {', '.join(tags)}" if tags else "Saved (no tags extracted)"
        else:
            reply = "Saved"

        conn.close()
        await update.message.reply_text(reply)
        return

    # ── Approve follow-up notes path (if no platform selection pending) ────────
    feedback_id = _pending_text.pop(chat_id, None)
    if feedback_id is None:
        return  # unsolicited message — ignore

    conn = sqlite3.connect(cfg.paths["db"])
    row  = conn.execute(
        "SELECT rating FROM feedback WHERE id=?", (feedback_id,)
    ).fetchone()

    if not row:
        conn.close()
        return

    rating = row[0]
    tags   = parse_tags(text, rating)

    conn.execute(
        "UPDATE feedback SET feedback_text=?, parsed_tags=? WHERE id=?",
        (text, __import__("json").dumps(tags) if tags else None, feedback_id),
    )
    conn.commit()
    conn.close()

    log.info("feedback_id=%d text saved, tags=%s", feedback_id, tags)
    reply = f"Tags: {', '.join(tags)}" if tags else "Saved (no tags extracted)"
    await update.message.reply_text(reply)


# ── Retry wrapper (runs in thread pool) ──────────────────────────────────────

def _run_retry_safe(video_id: int, feedback: str, chat_id: int) -> None:
    """Run run_retry in a worker thread; notify Telegram on failure."""
    from pipeline.retry import run_retry

    try:
        new_id = run_retry(video_id, feedback, cfg)
        log.info("retry done: original_id=%d new_id=%d", video_id, new_id)
    except Exception as exc:
        log.exception("run_retry failed: video_id=%d", video_id)
        # Notify user of failure — fire-and-forget via new event loop
        try:
            async def _notify():
                from telegram.request import HTTPXRequest
                async with Bot(
                    token=cfg.TELEGRAM_BOT_TOKEN,
                    request=HTTPXRequest(connect_timeout=30, read_timeout=60),
                ) as bot:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=f"Retry failed for video {video_id}: {exc}",
                    )
            asyncio.run(_notify())
        except Exception as notify_err:
            log.warning("Failed to send retry-failure notification: %s", notify_err)


# ── Instagram upload handlers ─────────────────────────────────────────────────

def _run_instagram_upload_safe(video_id: int, chat_id: int, account_id: str = "reels_creator") -> None:
    """Run Instagram upload in worker thread; notify Telegram of result."""
    import json
    import sqlite3
    from pathlib import Path
    from pipeline import instagram_auth, instagram_upload, social_captions

    try:
        conn = sqlite3.connect(cfg.paths["db"])
        conn.execute("PRAGMA journal_mode=WAL")

        # Load video
        cursor = conn.execute(
            "SELECT file_path, niche_id FROM videos WHERE id=?", (video_id,)
        )
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"Video {video_id} not found")

        file_path, niche_id = row

        # Load account and credentials
        account, creds = instagram_auth.load_instagram_account(account_id, cfg)

        # Load script and generate captions
        import settings
        script_dir = Path(cfg.paths.get("scripts", "output/scripts"))
        script_files = list(script_dir.glob(f"*_{video_id}.json"))

        caption_text = "Check out this amazing video!"
        hashtags = []

        if script_files:
            with open(script_files[0]) as f:
                script = json.load(f)

            # Load niche config for tone
            niche_config = None
            for n in cfg.niches:
                if n["id"] == niche_id:
                    niche_config = n
                    break

            if niche_config:
                try:
                    captions_result = social_captions.generate_social_captions(
                        script, niche_config, cfg
                    )
                    if captions_result:
                        ig_data = captions_result.get("instagram", {})
                        caption_text = ig_data.get("caption", caption_text)
                        hashtags = ig_data.get("hashtags", [])
                except Exception as e:
                    log.warning(f"Failed to generate captions: {e}")

        # Upload asynchronously
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(
                instagram_upload.upload_reel_to_instagram(
                    video_id, file_path, caption_text, hashtags, account, creds, conn, cfg
                )
            )
        finally:
            loop.close()
            conn.close()

        # Notify user
        try:
            async def _notify():
                from telegram.request import HTTPXRequest
                async with Bot(
                    token=cfg.TELEGRAM_BOT_TOKEN,
                    request=HTTPXRequest(connect_timeout=30, read_timeout=60),
                ) as bot:
                    if result["success"]:
                        text = f"✅ Posted to Instagram!\n{result['post_url']}"
                    else:
                        text = f"❌ Instagram upload failed:\n{result['error_msg']}"
                        if result["error_code"] in (429, 408, 500):
                            text += "\n\n🔄 Will retry"

                    keyboard = []
                    if not result["success"] and result["error_code"] not in (401,):
                        keyboard = [
                            [InlineKeyboardButton("🔄 Retry", callback_data=f"{_INSTAGRAM_RETRY}:{video_id}")]
                        ]

                    await bot.send_message(
                        chat_id=chat_id,
                        text=text,
                        reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None,
                    )
            asyncio.run(_notify())
        except Exception as notify_err:
            log.warning("Failed to send Instagram result notification: %s", notify_err)

        log.info("Instagram upload result: video_id=%d success=%s", video_id, result["success"])

    except Exception as exc:
        log.exception("Instagram upload failed: video_id=%d", video_id)
        try:
            async def _notify():
                from telegram.request import HTTPXRequest
                async with Bot(
                    token=cfg.TELEGRAM_BOT_TOKEN,
                    request=HTTPXRequest(connect_timeout=30, read_timeout=60),
                ) as bot:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=f"❌ Instagram upload error: {exc}",
                    )
            asyncio.run(_notify())
        except Exception as notify_err:
            log.warning("Failed to send upload-error notification: %s", notify_err)


async def _handle_instagram_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle Instagram upload button click."""
    query = update.callback_query
    await query.answer()

    action, video_id_str = query.data.split(":", 1)
    video_id = int(video_id_str)
    chat_id = query.message.chat_id

    await context.bot.send_message(
        chat_id=chat_id,
        text="⏳ Uploading to Instagram...",
    )

    # Remove platform selection buttons
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception as e:
        if "not modified" not in str(e).lower():
            log.warning("edit_message_reply_markup failed: %s", e)

    loop = asyncio.get_event_loop()
    loop.run_in_executor(_executor, _run_instagram_upload_safe, video_id, chat_id)


async def _handle_instagram_retry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle Instagram retry button click."""
    query = update.callback_query
    await query.answer()

    action, video_id_str = query.data.split(":", 1)
    video_id = int(video_id_str)
    chat_id = query.message.chat_id

    await context.bot.send_message(
        chat_id=chat_id,
        text="🔄 Retrying Instagram upload...",
    )

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception as e:
        if "not modified" not in str(e).lower():
            log.warning("edit_message_reply_markup failed: %s", e)

    loop = asyncio.get_event_loop()
    loop.run_in_executor(_executor, _run_instagram_upload_safe, video_id, chat_id)


async def _handle_skip_social(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle skip social media button click."""
    query = update.callback_query
    await query.answer()

    action, video_id_str = query.data.split(":", 1)
    video_id = int(video_id_str)

    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=f"Skipped social media posting for video {video_id}.",
    )

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception as e:
        if "not modified" not in str(e).lower():
            log.warning("edit_message_reply_markup failed: %s", e)


# ── Bot runner ────────────────────────────────────────────────────────────────

def run_bot() -> None:
    if not cfg.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set in .env")

    app = Application.builder().token(cfg.TELEGRAM_BOT_TOKEN).build()

    # Callback handlers (order matters: more specific patterns first)
    app.add_handler(
        CallbackQueryHandler(_handle_instagram_upload, pattern=f"^{_INSTAGRAM}:")
    )
    app.add_handler(
        CallbackQueryHandler(_handle_instagram_retry, pattern=f"^{_INSTAGRAM_RETRY}:")
    )
    app.add_handler(
        CallbackQueryHandler(_handle_skip_social, pattern=f"^{_SKIP_SOCIAL}:")
    )
    app.add_handler(CallbackQueryHandler(_handle_callback))  # Default callback handler

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_text))

    log.info("Telegram review bot polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    run_bot()
