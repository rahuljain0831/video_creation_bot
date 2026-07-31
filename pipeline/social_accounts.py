"""
Loads social_config.json + resolves credentials per account.
Read-only registry — does not perform any uploads.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent / "social_config.json"


def load_social_config() -> dict:
    with open(_CONFIG_PATH) as f:
        return json.load(f)


def get_account_for_niche(niche_id: str, cfg: dict | None = None) -> dict | None:
    """Returns the first enabled account for this niche, on an enabled
    platform, or None if nothing is wired up yet."""
    cfg = cfg or load_social_config()
    for acct in cfg.get("accounts", []):
        if acct["niche"] != niche_id or not acct.get("enabled"):
            continue
        platform_cfg = cfg["platforms"].get(acct["platform"], {})
        if not platform_cfg.get("enabled"):
            continue
        return acct
    return None


def load_credentials(account: dict) -> dict:
    cred_path = Path(__file__).parent.parent / account["credentials_file"]
    if not cred_path.exists():
        raise FileNotFoundError(
            f"No credentials for account {account['account_id']} — "
            f"run the setup script for {account['platform']} first."
        )
    with open(cred_path) as f:
        return json.load(f)


def is_token_expiring_soon(account: dict, warn_days: int = 5) -> bool:
    expires_at = account.get("expires_at")
    if not expires_at:
        return False
    exp = datetime.fromisoformat(expires_at)
    return (exp - datetime.now(timezone.utc)).days <= warn_days
