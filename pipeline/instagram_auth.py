"""Instagram account authentication and credential management."""
import json
from datetime import datetime
from pathlib import Path


def load_instagram_account(account_id: str, cfg) -> tuple[dict, dict]:
    """
    Load Instagram account config and credentials.

    Args:
        account_id: Account identifier (e.g., 'reels_creator')
        cfg: Config object with paths and social_config

    Returns:
        (account_dict, credentials_dict)

    Raises:
        FileNotFoundError: If credentials file not found
        ValueError: If account not found in social_config or credentials invalid
    """
    social_config_path = Path(cfg.project_root) / "social_config.json"
    if not social_config_path.exists():
        raise FileNotFoundError(f"social_config.json not found at {social_config_path}")

    with open(social_config_path) as f:
        social_config = json.load(f)

    account = None
    for acc in social_config.get("accounts", []):
        if acc.get("account_id") == account_id:
            account = acc
            break

    if not account:
        raise ValueError(f"Account '{account_id}' not found in social_config.json")

    if not account.get("enabled"):
        raise ValueError(f"Account '{account_id}' is disabled in social_config.json")

    creds_path = Path(cfg.project_root) / account["credentials_file"]
    if not creds_path.exists():
        raise FileNotFoundError(
            f"Credentials file not found at {creds_path}. "
            f"Run: python scripts/meta_token_exchange.py {account_id} <short_token> "
            f"<app_id> <app_secret> <page_id> <ig_business_id>"
        )

    with open(creds_path) as f:
        creds = json.load(f)

    if "access_token" not in creds:
        raise ValueError(f"Missing 'access_token' in credentials file {creds_path}")

    if "ig_business_id" not in creds and "ig_business_id" not in account:
        raise ValueError(
            f"Missing 'ig_business_id' in credentials file or account config"
        )

    return account, creds


def is_token_expiring_soon(creds: dict, warn_days: int = 7) -> bool:
    """
    Check if access token expires within warn_days.

    Args:
        creds: Credentials dict with optional 'expires_at' ISO string
        warn_days: Number of days to check ahead

    Returns:
        True if token expires within warn_days, False otherwise
    """
    if "expires_at" not in creds:
        return False

    try:
        expires_at = datetime.fromisoformat(creds["expires_at"])
        now = datetime.now(expires_at.tzinfo) if expires_at.tzinfo else datetime.now()
        delta_days = (expires_at - now).days
        return delta_days <= warn_days
    except ValueError:
        return False


def validate_credentials(creds: dict) -> bool:
    """
    Validate that credentials have required fields.

    Args:
        creds: Credentials dict

    Returns:
        True if valid, False otherwise
    """
    required = ["access_token"]
    return all(creds.get(field) for field in required)
