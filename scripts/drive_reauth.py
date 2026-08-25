"""Re-authenticate Google Drive OAuth via Telegram.

Flow:
1. Sends reminder to Telegram asking for approval
2. Polls Telegram for "approve" reply
3. Sends short OAuth link to Telegram
4. Polls Telegram for redirect URL with auth code
5. Exchanges code for token, saves, notifies

Usage:
    python scripts/drive_reauth.py
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

TOKEN_PATH = ROOT / "drive_token.json"
OAUTH_CREDS_PATH = ROOT / "oauth_credentials.json"
SCOPES = ["https://www.googleapis.com/auth/drive.file"]
REDIRECT_URI = "http://localhost:8090/"
POLL_TIMEOUT = 600


def _tg(method, **params):
    """Raw Telegram Bot API call. Retries on 409 Conflict."""
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    url = f"https://api.telegram.org/bot{token}/{method}"
    body = json.dumps(params).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    for attempt in range(4):
        try:
            resp = urllib.request.urlopen(req, timeout=60)
            return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 409 and attempt < 3:
                wait = 10 * (attempt + 1)
                print(f"  Telegram 409 conflict, retry in {wait}s...")
                time.sleep(wait)
                continue
            raise


def _send(text):
    """Send message to Telegram."""
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    _tg("sendMessage", chat_id=chat_id, text=text, parse_mode="Markdown",
         disable_web_page_preview=True)


def _poll_telegram(match_fn, label):
    """Poll Telegram for message matching match_fn."""
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    result = _tg("getUpdates", timeout=1)
    updates = result.get("result", [])
    offset = (updates[-1]["update_id"] + 1) if updates else None

    print(f"  Polling Telegram for {label}...")
    deadline = time.time() + POLL_TIMEOUT

    while time.time() < deadline:
        params = {"timeout": 15, "allowed_updates": ["message"]}
        if offset is not None:
            params["offset"] = offset
        try:
            result = _tg("getUpdates", **params)
        except Exception as exc:
            print(f"  Poll error: {exc}")
            time.sleep(5)
            continue

        for update in result.get("result", []):
            offset = update["update_id"] + 1
            msg = update.get("message", {})
            if str(msg.get("chat", {}).get("id", "")) != str(chat_id):
                continue
            text = (msg.get("text") or "").strip()
            if text and match_fn(text):
                return text

    return None


def _shorten(url):
    """Shorten URL via TinyURL."""
    try:
        api = f"https://tinyurl.com/api-create.php?url={urllib.parse.quote(url, safe='')}"
        req = urllib.request.Request(api, headers={"User-Agent": "reauth"})
        resp = urllib.request.urlopen(req, timeout=10)
        return resp.read().decode().strip()
    except Exception:
        return url


def _extract_code(text):
    """Extract OAuth code from redirect URL or raw code text."""
    if "code=" in text:
        try:
            code = parse_qs(urlparse(text).query).get("code", [None])[0]
            if code:
                return code
        except Exception:
            pass
    if text.startswith("4/") or text.startswith("4%2F"):
        return text
    return None


def main():
    if not OAUTH_CREDS_PATH.exists():
        print(f"ERROR: {OAUTH_CREDS_PATH} not found.")
        sys.exit(1)

    # Step 1: ask for approval
    _send(
        "\U0001f511 *Drive OAuth Token Refresh*\n\n"
        "Token needs re-authentication.\n"
        "Reauthenticate with resinscapers@gmail.com\n\n"
        "Reply *approve* to get re-auth link."
    )
    print("Reminder sent. Waiting for 'approve'...")

    reply = _poll_telegram(
        lambda t: t.lower().strip() in ("approve", "yes", "ok"),
        "'approve'",
    )
    if not reply:
        _send("\u274c Re-auth timed out.")
        print("Timed out.")
        sys.exit(1)

    print("Approved!")

    # Step 2: generate and send short auth link
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(OAUTH_CREDS_PATH), SCOPES)
    flow.redirect_uri = REDIRECT_URI
    auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")
    short = _shorten(auth_url)

    _send(
        "\U0001f517 *Open this link and login:*\n\n"
        f"{short}\n\n"
        "After login, page won't load.\n"
        "*Copy full URL* from browser bar and paste here."
    )
    print(f"Auth link sent: {short}")

    # Step 3: wait for redirect URL with code
    code_text = _poll_telegram(
        lambda t: _extract_code(t) is not None,
        "auth code",
    )

    code = _extract_code(code_text) if code_text else None
    if not code:
        _send("\u274c Re-auth timed out.")
        print("Timed out waiting for code.")
        sys.exit(1)

    # Step 4: exchange and save
    print("Exchanging code for token...")
    flow.fetch_token(code=code)

    token_data = json.loads(flow.credentials.to_json())
    token_data["_issued_at"] = datetime.now(timezone.utc).isoformat()
    with open(TOKEN_PATH, "w") as f:
        json.dump(token_data, f, indent=2)

    _send("\u2705 Drive OAuth token refreshed!")
    print(f"\nToken saved to {TOKEN_PATH}")
    print(f"\n{'='*60}")
    print("Update GitHub secret DRIVE_OAUTH_TOKEN:")
    print(f"{'='*60}")
    print(json.dumps(token_data))
    print(f"{'='*60}")
    print("\nhttps://github.com/rahuljain0831/video_creation_bot/settings/secrets/actions")


if __name__ == "__main__":
    main()
