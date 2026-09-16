"""
Telegram notifications.

No review gate — run_niche.py auto-approves every video (content safety is
enforced upstream by pipeline/image_critic.py's mandatory nsfw check and each
niche's image_prompt_rules banning humans/faces/hands outright). This module
just sends an FYI video message after the fact; nothing waits on it.
"""
import asyncio
import logging
import sqlite3
from pathlib import Path

from telegram import Bot

from config import cfg

log = logging.getLogger(__name__)


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
    """Sync wrapper — safe to call from the worker. FYI notification, no gate."""
    if not cfg.TELEGRAM_BOT_TOKEN or not cfg.TELEGRAM_CHAT_ID:
        log.warning("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — skipping send")
        return
    if not Path(file_path).exists():
        log.error("Video file not found: %s", file_path)
        return
    asyncio.run(_send_video_async(video_id, file_path, quote_text, conn))
