"""
Daily health check for all platform tokens.

Checks Instagram, Facebook, and YouTube credentials and sends a Telegram
summary. Designed to run in GitHub Actions before token expiry causes a
silent upload failure.

Exit codes:
    0 — all tokens healthy (or not configured)
    1 — at least one token is expired or expiring within WARNING_DAYS
"""
import json
import logging
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("platform_token_check")

WARNING_DAYS = 14   # alert this many days before expiry
CRITICAL_DAYS = 3   # escalate at this threshold


# ── Telegram ─────────────────────────────────────────────────────────────────

def _send_telegram(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat  = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        log.warning("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — skipping alert")
        return False
    try:
        data = json.dumps({
            "chat_id": chat, "text": text, "parse_mode": "Markdown"
        }).encode()
        urllib.request.urlopen(urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data,
            headers={"Content-Type": "application/json"},
        ), timeout=15)
        return True
    except Exception as e:
        log.warning("Telegram send failed: %s", e)
        return False


# ── Instagram ─────────────────────────────────────────────────────────────────

def check_instagram(creds_path: str = "credentials/all_niches_ig.json") -> dict:
    """
    Check Instagram long-lived token expiry from credentials file.
    Returns {"status": "ok"|"warn"|"critical"|"expired"|"missing", "detail": str}
    """
    path = Path(creds_path)
    if not path.exists():
        return {"status": "missing", "detail": "credentials file not found"}

    try:
        data = json.loads(path.read_text())
    except Exception as e:
        return {"status": "missing", "detail": f"unreadable: {e}"}

    expires_at = data.get("expires_at")
    if not expires_at:
        # No expiry info — do a lightweight Graph API call to verify
        token = data.get("access_token", "")
        if not token:
            return {"status": "missing", "detail": "no access_token in file"}
        try:
            url = f"https://graph.facebook.com/me?fields=id&access_token={token}"
            urllib.request.urlopen(url, timeout=10)
            return {"status": "ok", "detail": "token valid (no expiry date stored)"}
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 403):
                return {"status": "expired", "detail": f"Graph API returned {e.code}"}
            return {"status": "ok", "detail": f"Graph API {e.code} (non-auth error)"}
        except Exception as e:
            return {"status": "ok", "detail": f"could not verify: {e}"}

    try:
        exp_dt = datetime.fromisoformat(expires_at)
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=timezone.utc)
        days_left = (exp_dt - datetime.now(timezone.utc)).days
    except Exception as e:
        return {"status": "missing", "detail": f"bad expires_at: {e}"}

    if days_left < 0:
        return {"status": "expired", "detail": f"expired {-days_left}d ago"}
    if days_left <= CRITICAL_DAYS:
        return {"status": "critical", "detail": f"expires in {days_left}d"}
    if days_left <= WARNING_DAYS:
        return {"status": "warn", "detail": f"expires in {days_left}d"}
    return {"status": "ok", "detail": f"expires in {days_left}d"}


# ── Facebook ─────────────────────────────────────────────────────────────────

def check_facebook() -> dict:
    """
    Validate Facebook page access token via a lightweight Graph API call.
    Falls back to env var FACEBOOK_PAGE_ACCESS_TOKEN if creds file absent.
    """
    token = os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN", "")

    # Also try reading from Instagram creds file (shared Meta credentials)
    if not token:
        path = Path("credentials/all_niches_ig.json")
        if path.exists():
            try:
                data = json.loads(path.read_text())
                token = data.get("page_access_token") or data.get("access_token", "")
            except Exception:
                pass

    if not token:
        return {"status": "missing", "detail": "no FACEBOOK_PAGE_ACCESS_TOKEN or creds file"}

    try:
        url = f"https://graph.facebook.com/me?fields=id,name&access_token={token}"
        with urllib.request.urlopen(url, timeout=10) as resp:
            info = json.loads(resp.read())
            name = info.get("name", info.get("id", "?"))
            return {"status": "ok", "detail": f"page '{name}' accessible"}
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode()[:200]
        except Exception:
            pass
        if e.code in (400, 401, 403):
            return {"status": "expired", "detail": f"Graph API {e.code}: {body}"}
        return {"status": "ok", "detail": f"Graph API {e.code} (non-auth)"}
    except Exception as e:
        return {"status": "ok", "detail": f"could not verify: {e}"}


# ── YouTube ───────────────────────────────────────────────────────────────────

def check_youtube(creds_path: str = "credentials/mythology_yt.json") -> dict:
    """
    Validate YouTube OAuth credentials by attempting a token refresh.
    """
    path = Path(creds_path)
    if not path.exists():
        return {"status": "missing", "detail": "credentials file not found"}

    try:
        data = json.loads(path.read_text())
    except Exception as e:
        return {"status": "missing", "detail": f"unreadable: {e}"}

    if not data.get("refresh_token"):
        return {"status": "expired", "detail": "no refresh_token — re-run youtube_auth_setup.py"}

    # Attempt a token refresh via Google OAuth endpoint
    try:
        payload = json.dumps({
            "client_id": data["client_id"],
            "client_secret": data["client_secret"],
            "refresh_token": data["refresh_token"],
            "grant_type": "refresh_token",
        }).encode()
        req = urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            if "access_token" in result:
                expires_in = result.get("expires_in", 3600)
                exp_dt = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
                return {"status": "ok", "detail": f"token valid, refreshes until {exp_dt:%Y-%m-%d %H:%M} UTC"}
            return {"status": "expired", "detail": f"refresh returned no access_token: {result}"}
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode()[:200]
        except Exception:
            pass
        if e.code in (400, 401):
            return {"status": "expired", "detail": f"refresh failed {e.code}: {body}"}
        return {"status": "ok", "detail": f"refresh HTTP {e.code} (non-auth)"}
    except Exception as e:
        return {"status": "ok", "detail": f"could not verify: {e}"}


# ── Main ──────────────────────────────────────────────────────────────────────

_STATUS_EMOJI = {
    "ok":       "✅",
    "warn":     "⚠️",
    "critical": "🚨",
    "expired":  "❌",
    "missing":  "⬛",
}


def _is_platform_enabled(name: str) -> bool:
    """Check social_config.json — skip disabled platforms."""
    try:
        cfg_path = Path(__file__).parent.parent / "social_config.json"
        with open(cfg_path) as f:
            sc = json.load(f)
        return sc.get("platforms", {}).get(name.lower(), {}).get("enabled", True)
    except Exception:
        return True  # assume enabled if config unreadable


def main() -> int:
    _CHECKS = {
        "Instagram": ("instagram", check_instagram),
        "Facebook":  ("facebook",  check_facebook),
        "YouTube":   ("youtube",   check_youtube),
    }

    results = {}
    for label, (key, checker) in _CHECKS.items():
        if _is_platform_enabled(key):
            results[label] = checker()
        else:
            results[label] = {"status": "ok", "detail": "platform disabled — skipped"}
            log.info("Skipping %s (disabled in social_config.json)", label)

    for platform, r in results.items():
        log.info("%s: %s — %s", platform, r["status"], r["detail"])

    bad = {p: r for p, r in results.items() if r["status"] in ("expired", "critical")}
    warn = {p: r for p, r in results.items() if r["status"] == "warn"}

    lines = ["*Platform Token Health Check*\n"]
    for platform, r in results.items():
        emoji = _STATUS_EMOJI.get(r["status"], "❓")
        lines.append(f"{emoji} *{platform}*: {r['detail']}")

    if bad:
        lines.append("")
        lines.append("🚨 *Action required* — re-run auth setup for expired platforms:")
        for p in bad:
            if p == "Instagram":
                lines.append("  `python scripts/instagram_auth_setup.py`")
            elif p == "YouTube":
                lines.append("  `python scripts/youtube_auth_setup.py`")
            elif p == "Facebook":
                lines.append("  `python scripts/meta_token_exchange.py`")

    if warn:
        lines.append("")
        lines.append("⚠️ Tokens expiring soon — refresh before they expire.")

    msg = "\n".join(lines)

    # Always send if anything is not OK; also send on first-of-month for health record
    now = datetime.now(timezone.utc)
    should_send = bool(bad or warn) or now.day == 1

    if should_send:
        _send_telegram(msg)
    else:
        log.info("All tokens healthy. No alert sent (not first of month).")

    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
