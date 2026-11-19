"""Decide whether the Drive OAuth token needs re-authentication.

Google OAuth refresh tokens expire after 7 days when the app is in "Testing"
mode. GitHub Actions runs this every 6 hours; when the token is within
REAUTH_BEFORE_DAYS of that deadline, or already dead, it sets
`needs_reauth=true` in $GITHUB_OUTPUT and the workflow starts the Telegram
re-auth flow (scripts/drive_reauth.py). It keeps asking every run until a new
token is saved.

Usage:
    python scripts/drive_token_check.py            # check
    python scripts/drive_token_check.py --force     # report needs_reauth regardless
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("drive_token_check")

TOKEN_PATH = ROOT / "drive_token.json"
# Google revokes refresh tokens after 7 days for apps in "Testing" mode
REFRESH_TOKEN_LIFETIME_DAYS = 7
REAUTH_BEFORE_DAYS = 2


def token_age_days(data: dict) -> float | None:
    """Days since `_issued_at`, or None when the token was saved without it."""
    issued = data.get("_issued_at")
    if not issued:
        return None
    return (datetime.now(timezone.utc) - datetime.fromisoformat(issued)).total_seconds() / 86400


def token_alive(data: dict) -> bool:
    """Try a real refresh — catches revoked tokens and ones with no `_issued_at`."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    try:
        Credentials.from_authorized_user_info(data).refresh(Request())
        return True
    except RefreshError as exc:
        log.warning("Token refresh failed: %s", exc)
        return False


def needs_reauth(age_days: float | None, alive: bool) -> bool:
    if not alive:
        return True
    if age_days is None:
        return False
    return REFRESH_TOKEN_LIFETIME_DAYS - age_days <= REAUTH_BEFORE_DAYS


def main():
    if not TOKEN_PATH.exists():
        log.error("drive_token.json not found at %s", TOKEN_PATH)
        due = True
    else:
        data = json.loads(TOKEN_PATH.read_text())
        age = token_age_days(data)
        alive = token_alive(data)
        if age is None:
            log.info("Token has no _issued_at; alive=%s", alive)
        else:
            log.info("Token %.1f days old, expires in ~%.1f days, alive=%s",
                     age, REFRESH_TOKEN_LIFETIME_DAYS - age, alive)
        due = "--force" in sys.argv or needs_reauth(age, alive)

    log.info("needs_reauth=%s", due)
    out = os.getenv("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"needs_reauth={'true' if due else 'false'}\n")


if __name__ == "__main__":
    main()
