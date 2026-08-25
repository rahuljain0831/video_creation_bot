"""
Quota tracker — reads provider limits from quota.json, tracks usage in DB.

Pre-call usage (check_only=True):
    can_proceed, reason = check_and_log_quota(provider, conn, check_only=True)
    if not can_proceed:
        skip_provider()

Post-call usage (check_only=False, default):
    check_and_log_quota(provider, conn, success=True)
    check_and_log_quota(provider, conn, success=False, error_code=429)
"""

import json
import logging
import sqlite3
from datetime import date, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

_QUOTA_JSON = Path(__file__).parent.parent / "quota.json"
_quota_cfg: dict | None = None


def load_quota_config() -> dict:
    """Load quota.json once and cache in module-level variable."""
    global _quota_cfg
    if _quota_cfg is None:
        with open(_QUOTA_JSON) as f:
            _quota_cfg = json.load(f)
    return _quota_cfg


def get_provider_limit(provider: str) -> int:
    """Return daily_limit for provider from quota.json."""
    cfg = load_quota_config()
    return cfg["providers"][provider]["daily_limit"]


def should_reset_today(provider: str, conn: sqlite3.Connection) -> bool:
    """
    Return True if quota reset should happen today for this provider.

    Compares reset_interval from quota.json with last_reset_date in quota_reset_log.
    Returns True if no reset row exists (never reset) or reset interval has passed.
    """
    cfg = load_quota_config()
    interval = cfg["providers"][provider].get("reset_interval", "daily")

    row = conn.execute(
        "SELECT last_reset_date FROM quota_reset_log WHERE provider=? AND reset_interval=?",
        (provider, interval),
    ).fetchone()

    if row is None:
        return True  # never reset — do it now

    try:
        last_reset = date.fromisoformat(row[0])
    except (ValueError, TypeError):
        return True

    today = date.today()

    if interval == "daily":
        return last_reset < today
    elif interval == "weekly":
        # Safe comparison: more than 7 days elapsed
        return today >= last_reset + timedelta(days=7)
    elif interval == "monthly":
        # New month (handle year rollover)
        if today.year > last_reset.year:
            return True
        return today.month > last_reset.month
    else:
        # Unknown interval — treat as daily
        return last_reset < today


def reset_quota_for_provider(provider: str, conn: sqlite3.Connection) -> None:
    """
    Clear units_used for provider if reset interval has passed.
    Updates quota_reset_log to mark reset done.
    """
    if not should_reset_today(provider, conn):
        return

    cfg = load_quota_config()
    interval = cfg["providers"][provider].get("reset_interval", "daily")
    today = date.today().isoformat()

    conn.execute(
        "UPDATE quota_usage SET units_used=0 WHERE provider=? AND date=?",
        (provider, today),
    )
    conn.execute(
        """INSERT INTO quota_reset_log (provider, reset_interval, last_reset_date)
           VALUES (?, ?, ?)
           ON CONFLICT(provider, reset_interval) DO UPDATE SET
               last_reset_date = excluded.last_reset_date,
               reset_at = datetime('now')""",
        (provider, interval, today),
    )
    conn.commit()
    log.info("quota_reset: provider=%s interval=%s reset on %s", provider, interval, today)


def check_and_log_quota(
    provider: str,
    conn: sqlite3.Connection,
    check_only: bool = False,
    success: bool | None = None,
    error_code: int | None = None,
) -> tuple[bool, str]:
    """
    Pre-call check (check_only=True) or post-call log (check_only=False).

    Pre-call:
        can_proceed, reason = check_and_log_quota(provider, conn, check_only=True)

    Post-call success:
        check_and_log_quota(provider, conn, success=True)

    Post-call failure:
        check_and_log_quota(provider, conn, success=False, error_code=429)

    Returns:
        (True, "")  — can proceed / success
        (False, reason_str) — blocked / failed
    """
    cfg = load_quota_config()
    provider_cfg = cfg["providers"].get(provider)
    if provider_cfg is None:
        # Unknown provider — don't block, just warn
        log.debug("quota_tracker: unknown provider=%s, skipping quota check", provider)
        return True, ""

    limit = provider_cfg["daily_limit"]
    today = date.today().isoformat()

    if check_only:
        # Run reset first so stale quota doesn't block valid calls
        reset_quota_for_provider(provider, conn)

        row = conn.execute(
            "SELECT units_used FROM quota_usage WHERE provider=? AND date=?",
            (provider, today),
        ).fetchone()
        used = row[0] if row else 0

        if used >= limit:
            reason = f"{provider} daily limit {limit} reached ({used} used)"
            log.info("quota_tracker: SKIP %s", reason)
            return False, reason
        return True, ""

    else:
        # Post-call: increment counter, store error_code
        conn.execute(
            """INSERT INTO quota_usage (provider, date, units_used, unit_limit, last_error_code)
               VALUES (?, ?, 1, ?, ?)
               ON CONFLICT(provider, date) DO UPDATE SET
                   units_used      = units_used + 1,
                   unit_limit      = excluded.unit_limit,
                   last_error_code = excluded.last_error_code""",
            (provider, today, limit, error_code),
        )
        conn.commit()

        if success:
            return True, ""
        reason = f"{provider} call failed (error_code={error_code})"
        return False, reason


def get_quota_report(conn: sqlite3.Connection) -> list[tuple[str, int, int, float]]:
    """
    Return [(provider, used, limit, percent_used), ...] for today, in config order.
    """
    cfg = load_quota_config()
    today = date.today().isoformat()
    report: list[tuple[str, int, int, float]] = []

    for provider, pcfg in cfg["providers"].items():
        limit = pcfg["daily_limit"]
        row = conn.execute(
            "SELECT units_used FROM quota_usage WHERE provider=? AND date=?",
            (provider, today),
        ).fetchone()
        used = row[0] if row else 0
        pct = (used / limit * 100.0) if limit else 0.0
        report.append((provider, used, limit, pct))

    return report


def get_quota_summary(conn: sqlite3.Connection) -> dict[str, str]:
    """
    Return dict of {provider: "used/limit"} for today.
    Used by Telegram daily summary.
    """
    return {p: f"{used}/{limit}" for p, used, limit, _ in get_quota_report(conn)}


def format_quota_report(conn: sqlite3.Connection) -> str:
    """Multi-line, aligned quota block printed at the end of every run."""
    report = get_quota_report(conn)
    if not report:
        return ""

    width = max(len(p) for p, _, _, _ in report)
    lines = ["── Quota used today ──────────────────────"]
    for provider, used, limit, pct in report:
        lines.append(f"{provider:<{width}}  {used:>6}/{limit:<8} {pct:5.1f}%")
    return "\n".join(lines)
