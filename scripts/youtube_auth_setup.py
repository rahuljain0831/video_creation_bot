"""
Run once per YouTube account after downloading the OAuth client_secret.json
from Google Cloud Console. Opens a browser for consent, then saves a
long-lived credentials file with a refresh token.

Usage:
    python scripts/youtube_auth_setup.py <account_id> <path_to_client_secret.json>
"""

import json
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def main() -> None:
    account_id, client_secret_path = sys.argv[1], sys.argv[2]
    flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, SCOPES)
    creds = flow.run_local_server(port=0)

    out = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
    }
    dest = Path("credentials") / f"{account_id}.json"
    dest.parent.mkdir(exist_ok=True)
    dest.write_text(json.dumps(out, indent=2))
    print(f"Saved {dest}. Now add this account to social_config.json.")


if __name__ == "__main__":
    main()
