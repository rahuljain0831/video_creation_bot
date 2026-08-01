"""
Phase 6 — Telegram review bot.

Flow:
  1. Worker calls send_for_review(video_id, file_path, quote_text)
  2. Bot sends video + quote with Approve / Reject buttons
  3. Tap button → status updated, basic feedback recorded
  4. Optional: reply text after tapping → Ollama parses tags and appends to feedback row
"""
import asyncio
import logging
import sqlite3
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

# In-memory map: chat_id → last feedback_row_id awaiting text detail
# (simple single-user bot — no persistence needed)
_pending_text: dict[int, int] = {}


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
    bot = Bot(token=cfg.TELEGRAM_BOT_TOKEN,
              request=HTTPXRequest(read_timeout=120, write_timeout=120,
                                   connect_timeout=30, media_write_timeout=300))
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

    if action == _APPROVE:
        new_status = "approved"
        rating = "good"
        label  = "✅ Approved"
    else:
        new_status = "rejected"
        rating = "bad"
        label  = "❌ Rejected"

    conn = sqlite3.connect(cfg.paths["db"])
    conn.execute("UPDATE videos SET status = ? WHERE id = ?", (new_status, video_id))
    conn.execute(
        "INSERT INTO feedback (video_id, rating, source) VALUES (?, ?, 'manual')",
        (video_id, rating),
    )
    conn.commit()
    feedback_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()

    # Store feedback_id so a follow-up text message can append tags
    _pending_text[chat_id] = feedback_id

    log.info("video_id=%d → %s (feedback_id=%d)", video_id, new_status, feedback_id)

    try:
        await query.edit_message_caption(
            caption=f"{query.message.caption}\n\n<b>{label}</b>\n<i>Reply with feedback text (optional)</i>",
            parse_mode="HTML",
            reply_markup=None,
        )
    except Exception as e:
        if "not modified" not in str(e).lower():
            raise


# ── Text reply handler ────────────────────────────────────────────────────────

async def _handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    text    = update.message.text.strip()

    feedback_id = _pending_text.pop(chat_id, None)
    if feedback_id is None:
        return  # unsolicited message — ignore

    conn = sqlite3.connect(cfg.paths["db"])
    row  = conn.execute(
        "SELECT rating FROM feedback WHERE id = ?", (feedback_id,)
    ).fetchone()

    if not row:
        conn.close()
        return

    rating = row[0]
    tags   = parse_tags(text, rating)

    conn.execute(
        "UPDATE feedback SET feedback_text = ?, parsed_tags = ? WHERE id = ?",
        (text, __import__("json").dumps(tags) if tags else None, feedback_id),
    )
    conn.commit()
    conn.close()

    log.info("feedback_id=%d text saved, tags=%s", feedback_id, tags)

    reply = f"Tags: {', '.join(tags)}" if tags else "Saved (no tags extracted)"
    await update.message.reply_text(reply)


# ── Bot runner ────────────────────────────────────────────────────────────────

def run_bot() -> None:
    if not cfg.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set in .env")

    app = Application.builder().token(cfg.TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CallbackQueryHandler(_handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_text))

    log.info("Telegram review bot polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    run_bot()
