"""Re-authenticate Google Drive OAuth and print token for GitHub secret update.

Opens browser for Google login. After auth completes:
1. Saves new drive_token.json locally
2. Prints the token JSON for pasting into GitHub secret DRIVE_OAUTH_TOKEN

Usage:
    python scripts/drive_reauth.py
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

TOKEN_PATH = ROOT / "drive_token.json"
OAUTH_CREDS_PATH = ROOT / "oauth_credentials.json"
SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def main():
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not OAUTH_CREDS_PATH.exists():
        print(f"ERROR: {OAUTH_CREDS_PATH} not found.")
        print("Download from Google Cloud Console > Credentials > OAuth client ID.")
        sys.exit(1)

    print("Opening browser for Google login...")
    print("Authenticate with: resinscapers@gmail.com\n")

    flow = InstalledAppFlow.from_client_secrets_file(str(OAUTH_CREDS_PATH), SCOPES)
    creds = flow.run_local_server(port=8090, open_browser=True)

    # Save with issued timestamp
    token_data = json.loads(creds.to_json())
    token_data["_issued_at"] = datetime.now(timezone.utc).isoformat()

    with open(TOKEN_PATH, "w") as f:
        json.dump(token_data, f, indent=2)

    print(f"\n✅ Token saved to {TOKEN_PATH}")
    print(f"\n{'='*60}")
    print("Update GitHub secret DRIVE_OAUTH_TOKEN with this value:")
    print(f"{'='*60}")
    print(json.dumps(token_data))
    print(f"{'='*60}")
    print("\nGo to: https://github.com/rahuljain0831/video_creation_bot/settings/secrets/actions")
    print("Edit DRIVE_OAUTH_TOKEN and paste the JSON above.")


if __name__ == "__main__":
    main()
