"""Check Drive OAuth token age and send Telegram reminder before expiry.

Google OAuth refresh tokens expire after 7 days when the app is in "Testing"
mode. This script runs daily via GitHub Actions and sends a Telegram reminder
1 day before that deadline.

Usage:
    python scripts/drive_token_check.py            # check and notify if needed
    python scripts/drive_token_check.py --force     # send reminder regardless
"""
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("drive_token_check")

TOKEN_PATH = ROOT / "drive_token.json"
# Google revokes refresh tokens after 7 days for apps in "Testing" mode
REFRESH_TOKEN_LIFETIME_DAYS = 7
REMINDER_BEFORE_DAYS = 1


def get_token_age_days():
    """Return (age_in_days, issued_at) or (None, None) if unknown."""
    if not TOKEN_PATH.exists():
        return None, None

    with open(TOKEN_PATH) as f:
        data = json.load(f)

    issued = data.get("_issued_at")
    if not issued:
        # Fall back to file modification time
        mtime = datetime.fromtimestamp(TOKEN_PATH.stat().st_mtime, tz=timezone.utc)
        age = (datetime.now(timezone.utc) - mtime).total_seconds() / 86400
        return age, mtime.isoformat()

    issued_dt = datetime.fromisoformat(issued)
    age = (datetime.now(timezone.utc) - issued_dt).total_seconds() / 86400
    return age, issued


def send_telegram(msg):
    """Send a Telegram message."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not bot_token or not chat_id:
        log.warning("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set, skipping notify")
        return False

    async def notify():
        from telegram import Bot
        from telegram.request import HTTPXRequest
        async with Bot(token=bot_token, request=HTTPXRequest(connect_timeout=30, read_timeout=60)) as bot:
            await bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")

    asyncio.run(notify())
    return True


def main():
    force = "--force" in sys.argv

    age_days, issued_at = get_token_age_days()

    if age_days is None:
        log.error("drive_token.json not found at %s", TOKEN_PATH)
        sys.exit(1)

    expires_in = REFRESH_TOKEN_LIFETIME_DAYS - age_days
    log.info("Token issued: %s (%.1f days ago), expires in ~%.1f days", issued_at, age_days, expires_in)

    if not force and expires_in > REMINDER_BEFORE_DAYS:
        log.info("Token still fresh. No reminder needed.")
        return

    if expires_in <= 0:
        urgency = "🚨 *EXPIRED*"
    else:
        urgency = f"⚠️ *Expires in {expires_in:.0f} day(s)*"

    msg = (
        f"{urgency}\n\n"
        f"Drive OAuth token needs re-authentication.\n"
        f"Reauthenticate with resinscapers@gmail.com\n\n"
        f"Run on your PC:\n"
        f"`python scripts/drive_reauth.py`"
    )

    if send_telegram(msg):
        log.info("Reminder sent to Telegram")
    else:
        # Print to stdout so CI logs capture it
        print(f"REMINDER: {msg}")


if __name__ == "__main__":
    main()
