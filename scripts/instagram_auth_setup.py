"""
Interactive setup for Instagram Reels upload credentials.

Takes a short-lived token from Graph API Explorer, exchanges it for a
60-day long-lived token, discovers the Instagram Business Account ID,
and saves everything to a credentials JSON file.

Usage:
    python scripts/instagram_auth_setup.py <account_id>
    python scripts/instagram_auth_setup.py all_niches_ig

Prerequisites:
    1. Facebook App with Instagram Graph API enabled
    2. Instagram Business or Creator account linked to a Facebook Page
    3. Short-lived User Token from https://developers.facebook.com/tools/explorer/
       with permissions: instagram_basic, instagram_content_publish, pages_read_engagement
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

GRAPH_API_BASE = "https://graph.facebook.com/v21.0"


def _exchange_for_long_lived_token(
    short_token: str, app_id: str, app_secret: str
) -> tuple[str, int]:
    """Exchange short-lived token for a 60-day long-lived token."""
    resp = requests.get(
        f"{GRAPH_API_BASE}/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": short_token,
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    if "access_token" not in data:
        error = data.get("error", {})
        raise RuntimeError(
            f"Token exchange failed: {error.get('message', data)}"
        )

    return data["access_token"], data.get("expires_in", 5184000)


def _get_instagram_account_id(access_token: str) -> tuple[str, str]:
    """Find the Instagram Business Account ID via linked Facebook Pages."""
    # Get pages the user manages
    resp = requests.get(
        f"{GRAPH_API_BASE}/me/accounts",
        params={
            "fields": "id,name,instagram_business_account",
            "access_token": access_token,
        },
        timeout=30,
    )
    resp.raise_for_status()
    pages = resp.json().get("data", [])

    if not pages:
        raise RuntimeError(
            "No Facebook Pages found. Ensure your token has pages_read_engagement "
            "permission and you manage at least one Page."
        )

    # Find pages with linked Instagram accounts
    ig_pages = []
    for page in pages:
        ig = page.get("instagram_business_account")
        if ig:
            ig_pages.append((page["name"], ig["id"]))

    if not ig_pages:
        page_names = ", ".join(p["name"] for p in pages)
        raise RuntimeError(
            f"None of your Pages ({page_names}) have a linked Instagram Business "
            "account. Link an Instagram Business or Creator account to a Facebook "
            "Page in Page Settings > Instagram."
        )

    if len(ig_pages) == 1:
        page_name, ig_id = ig_pages[0]
        print(f"\nFound Instagram account linked to Page '{page_name}': {ig_id}")
        return ig_id, page_name

    # Multiple — let user pick
    print("\nMultiple Instagram accounts found:")
    for i, (name, ig_id) in enumerate(ig_pages, 1):
        print(f"  {i}. {name} (IG ID: {ig_id})")

    while True:
        choice = input(f"\nSelect account [1-{len(ig_pages)}]: ").strip()
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(ig_pages):
                return ig_pages[idx]
        except ValueError:
            pass
        print("Invalid choice, try again.")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/instagram_auth_setup.py <account_id>")
        print("Example: python scripts/instagram_auth_setup.py all_niches_ig")
        sys.exit(1)

    account_id = sys.argv[1]

    print("=" * 60)
    print("Instagram Reels — Credential Setup")
    print("=" * 60)

    print(
        "\nBefore starting, you need:\n"
        "  1. A Facebook App with Instagram Graph API enabled\n"
        "  2. An Instagram Business/Creator account linked to a Facebook Page\n"
        "  3. A short-lived User Token from Graph API Explorer\n"
        "     https://developers.facebook.com/tools/explorer/\n"
        "     Required permissions:\n"
        "       - instagram_basic\n"
        "       - instagram_content_publish\n"
        "       - pages_read_engagement\n"
    )

    app_id = input("Facebook App ID: ").strip()
    app_secret = input("Facebook App Secret: ").strip()
    short_token = input("Short-lived User Token (from Graph API Explorer): ").strip()

    if not all([app_id, app_secret, short_token]):
        print("\nAll three values are required.")
        sys.exit(1)

    # Step 1: Exchange for long-lived token
    print("\nExchanging for long-lived token...")
    long_token, expires_in = _exchange_for_long_lived_token(
        short_token, app_id, app_secret
    )
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    print(f"Got long-lived token (expires: {expires_at.strftime('%Y-%m-%d %H:%M UTC')})")

    # Step 2: Find Instagram Business Account
    print("\nLooking up Instagram Business Account...")
    ig_user_id, page_name = _get_instagram_account_id(long_token)

    # Step 3: Save credentials
    creds = {
        "app_id": app_id,
        "app_secret": app_secret,
        "access_token": long_token,
        "ig_user_id": ig_user_id,
        "page_name": page_name,
        "expires_at": expires_at.isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    dest = Path("credentials") / f"{account_id}.json"
    dest.parent.mkdir(exist_ok=True)
    dest.write_text(json.dumps(creds, indent=2))

    print(f"\nCredentials saved to: {dest}")
    print(f"Instagram Business Account ID: {ig_user_id}")
    print(f"Linked Facebook Page: {page_name}")
    print(f"Token valid until: {expires_at.strftime('%Y-%m-%d %H:%M UTC')}")

    print("\n" + "=" * 60)
    print("NEXT STEPS")
    print("=" * 60)
    print(f"  1. Add this account to social_config.json (if not already there)")
    print(f"  2. Set 'enabled: true' when ready to upload")
    print(f"  3. Token expires every 60 days — re-run this script or the")
    print(f"     upload module will auto-refresh if within 7 days of expiry")
    print(f"\n  To manually refresh later:")
    print(f"    python scripts/instagram_auth_setup.py {account_id}")


if __name__ == "__main__":
    main()
