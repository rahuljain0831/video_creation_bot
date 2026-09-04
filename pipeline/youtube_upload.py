"""
Upload a video to YouTube using the Data API v3.

Requires credentials from scripts/youtube_auth_setup.py.
"""

import json
import logging
from pathlib import Path

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

log = logging.getLogger(__name__)


def _load_youtube_credentials(creds_path: Path) -> Credentials:
    data = json.loads(creds_path.read_text())
    required = ("refresh_token", "token_uri", "client_id", "client_secret")
    missing = [k for k in required if not data.get(k)]
    if missing:
        raise ValueError(
            f"YouTube credentials {creds_path} missing keys: {', '.join(missing)}. "
            "Re-run scripts/youtube_auth_setup.py."
        )
    creds = Credentials(
        token=data.get("token"),
        refresh_token=data["refresh_token"],
        token_uri=data["token_uri"],
        client_id=data["client_id"],
        client_secret=data["client_secret"],
        scopes=data.get("scopes", ["https://www.googleapis.com/auth/youtube.upload"]),
    )
    if creds.expired or not creds.valid:
        try:
            creds.refresh(Request())
        except Exception as exc:
            raise RuntimeError(f"YouTube token refresh failed: {exc}") from exc
        data["token"] = creds.token
        creds_path.write_text(json.dumps(data, indent=2))
        log.info("Refreshed YouTube access token")
    return creds


def upload_video(
    video_path: str | Path,
    title: str,
    description: str,
    tags: list[str] | None = None,
    category_id: str = "22",  # 22 = People & Blogs
    privacy: str = "private",
    credentials_file: str | Path = "credentials/mythology_yt.json",
) -> str:
    """
    Upload a video to YouTube.

    Args:
        video_path: Path to mp4 file
        title: Video title (max 100 chars)
        description: Video description
        tags: List of tags (without # prefix)
        category_id: YouTube category ID
        privacy: "private", "unlisted", or "public"
        credentials_file: Path to OAuth credentials JSON

    Returns:
        YouTube video ID
    """
    video_path = Path(video_path)
    creds_path = Path(credentials_file)

    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")
    if not creds_path.exists():
        raise FileNotFoundError(f"Credentials not found: {creds_path}")

    creds = _load_youtube_credentials(creds_path)
    youtube = build("youtube", "v3", credentials=creds)

    # Strip # from tags if present
    clean_tags = [t.lstrip("#") for t in (tags or [])]

    body = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "tags": clean_tags,
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        resumable=True,
        chunksize=10 * 1024 * 1024,  # 10 MB chunks
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    log.info("Uploading %s (%s) as %s...", video_path.name, title, privacy)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            log.info("Upload progress: %d%%", pct)

    video_id = response["id"]
    log.info("Upload complete: https://youtu.be/%s", video_id)
    return video_id
