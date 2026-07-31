"""
Exchanges a short-lived Graph API user token (from Graph API Explorer) for
a long-lived Page access token (~60 days), and saves credentials.

Usage:
    python scripts/meta_token_exchange.py <account_id> <short_lived_token> \
        <app_id> <app_secret> <page_id> <ig_business_id>
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

GRAPH_URL = "https://graph.facebook.com/v21.0"


def main() -> None:
    account_id, short_token, app_id, app_secret, page_id, ig_id = sys.argv[1:7]

    # Step 1: short-lived user token -> long-lived user token
    resp = requests.get(f"{GRAPH_URL}/oauth/access_token", params={
        "grant_type": "fb_exchange_token",
        "client_id": app_id,
        "client_secret": app_secret,
        "fb_exchange_token": short_token,
    })
    resp.raise_for_status()
    long_user_token = resp.json()["access_token"]

    # Step 2: long-lived user token -> Page access token (does not expire
    # while the user token is valid, but we still track expires_at conservatively)
    resp = requests.get(f"{GRAPH_URL}/{page_id}", params={
        "fields": "access_token",
        "access_token": long_user_token,
    })
    resp.raise_for_status()
    page_token = resp.json()["access_token"]

    expires_at = (datetime.now(timezone.utc) + timedelta(days=60)).isoformat()

    out = {
        "access_token": page_token,
        "page_id": page_id,
        "ig_business_id": ig_id,
        "expires_at": expires_at,
    }
    dest = Path("credentials") / f"{account_id}.json"
    dest.parent.mkdir(exist_ok=True)
    dest.write_text(json.dumps(out, indent=2))
    print(f"Saved {dest}. Update expires_at in social_config.json to: {expires_at}")


if __name__ == "__main__":
    main()
