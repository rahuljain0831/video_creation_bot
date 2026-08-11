"""
Upload a video as an Instagram Reel via the Facebook Graph API.

Requires credentials from scripts/instagram_auth_setup.py.
"""

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

log = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.facebook.com/v21.0"
TEMP_HOSTS = [
    {"name": "uguu", "url": "https://uguu.se/upload", "field": "files[]"},
    {"name": "tmpfiles", "url": "https://tmpfiles.org/api/v1/upload", "field": "file"},
    {"name": "0x0.st", "url": "https://0x0.st", "field": "file"},
    {"name": "litterbox", "url": "https://litterbox.catbox.moe/resources/internals/api.php", "field": "fileToUpload"},
]

# Status polling config
_POLL_INTERVAL_S = 5
_POLL_MAX_ATTEMPTS = 60  # 5 minutes max


def _upload_to_temp_host(video_path: Path) -> str:
    """Upload a local video to a temporary public host.

    Tries multiple hosts in order until one succeeds. Returns a public URL.
    The Instagram Graph API needs the video at a public URL to create
    a Reel container — this bridges local files to that requirement.
    """
    errors = []
    for host in TEMP_HOSTS:
        log.info("Trying temp host: %s ...", host["name"])
        try:
            with open(video_path, "rb") as f:
                if host["name"] == "litterbox":
                    resp = requests.post(
                        host["url"],
                        data={"reqtype": "fileupload", "time": "24h"},
                        files={host["field"]: (video_path.name, f, "video/mp4")},
                        timeout=300,
                    )
                else:
                    resp = requests.post(
                        host["url"],
                        files={host["field"]: (video_path.name, f, "video/mp4")},
                        timeout=300,
                    )
            resp.raise_for_status()

            # Parse URL from response (varies by host)
            if host["name"] == "uguu":
                data = resp.json()
                url = data["files"][0]["url"]
            elif host["name"] == "tmpfiles":
                data = resp.json()
                # tmpfiles.org needs /dl/ inserted for direct download
                raw_url = data["data"]["url"]
                url = raw_url.replace("tmpfiles.org/", "tmpfiles.org/dl/", 1)
            else:
                url = resp.text.strip()

            if url.startswith("http"):
                log.info("Temp upload done via %s: %s", host["name"], url)
                return url
            errors.append(f"{host['name']}: unexpected response: {url}")
        except Exception as exc:
            errors.append(f"{host['name']}: {exc}")
            log.warning("Temp host %s failed: %s", host["name"], exc)
    raise RuntimeError(f"All temp hosts failed: {'; '.join(errors)}")


def _load_credentials(creds_path: Path) -> dict:
    """Load Instagram credentials and refresh the token if close to expiry."""
    data = json.loads(creds_path.read_text())
    required = ("access_token", "ig_user_id", "app_id", "app_secret")
    missing = [k for k in required if not data.get(k)]
    if missing:
        raise ValueError(
            f"Credentials file {creds_path} missing keys: {', '.join(missing)}. "
            "Re-run scripts/instagram_auth_setup.py."
        )

    # Auto-refresh if token expires within 7 days
    expires_at = data.get("expires_at")
    if expires_at:
        exp_dt = datetime.fromisoformat(expires_at)
        days_left = (exp_dt - datetime.now(timezone.utc)).days
        if days_left < 7:
            log.info("Instagram token expires in %d days — refreshing...", days_left)
            data = _refresh_long_lived_token(data, creds_path)
        elif days_left < 0:
            log.warning("Instagram token is expired. Attempting refresh...")
            data = _refresh_long_lived_token(data, creds_path)

    return data


def _refresh_long_lived_token(data: dict, creds_path: Path) -> dict:
    """Exchange a valid long-lived token for a new 60-day long-lived token."""
    resp = requests.get(
        f"{GRAPH_API_BASE}/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": data["app_id"],
            "client_secret": data["app_secret"],
            "fb_exchange_token": data["access_token"],
        },
        timeout=30,
    )
    resp.raise_for_status()
    result = resp.json()

    if "access_token" not in result:
        raise RuntimeError(
            f"Token refresh failed: {result.get('error', result)}"
        )

    data["access_token"] = result["access_token"]
    expires_in = result.get("expires_in", 5184000)  # default 60 days
    data["expires_at"] = (
        datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    ).isoformat()

    creds_path.write_text(json.dumps(data, indent=2))
    log.info("Refreshed Instagram token — valid for %d days", expires_in // 86400)
    return data


def _create_media_container(
    ig_user_id: str,
    access_token: str,
    video_url: str,
    caption: str,
    cover_url: str | None = None,
    share_to_feed: bool = True,
) -> str:
    """Step 1: Create a Reels media container (starts server-side processing)."""
    params = {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "share_to_feed": str(share_to_feed).lower(),
        "access_token": access_token,
    }
    if cover_url:
        params["cover_url"] = cover_url

    resp = requests.post(
        f"{GRAPH_API_BASE}/{ig_user_id}/media",
        data=params,
        timeout=60,
    )
    resp.raise_for_status()
    result = resp.json()

    container_id = result.get("id")
    if not container_id:
        raise RuntimeError(f"Failed to create media container: {result}")

    log.info("Created media container: %s", container_id)
    return container_id


def _poll_container_status(container_id: str, access_token: str) -> str:
    """Step 2: Poll until the container is ready for publishing."""
    for attempt in range(1, _POLL_MAX_ATTEMPTS + 1):
        resp = requests.get(
            f"{GRAPH_API_BASE}/{container_id}",
            params={
                "fields": "status_code,status",
                "access_token": access_token,
            },
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()
        status_code = result.get("status_code", "UNKNOWN")

        if status_code == "FINISHED":
            log.info("Container %s ready for publishing", container_id)
            return status_code
        elif status_code == "ERROR":
            error_msg = result.get("status", "Unknown error during processing")
            raise RuntimeError(
                f"Media processing failed for container {container_id}: {error_msg}"
            )
        elif status_code == "EXPIRED":
            raise RuntimeError(
                f"Media container {container_id} expired before publishing"
            )

        log.info(
            "Container status: %s (attempt %d/%d) — waiting %ds...",
            status_code, attempt, _POLL_MAX_ATTEMPTS, _POLL_INTERVAL_S,
        )
        time.sleep(_POLL_INTERVAL_S)

    raise TimeoutError(
        f"Media container {container_id} did not finish processing "
        f"after {_POLL_MAX_ATTEMPTS * _POLL_INTERVAL_S}s"
    )


def _publish_container(
    ig_user_id: str, container_id: str, access_token: str
) -> str:
    """Step 3: Publish the processed container as a Reel."""
    resp = requests.post(
        f"{GRAPH_API_BASE}/{ig_user_id}/media_publish",
        data={
            "creation_id": container_id,
            "access_token": access_token,
        },
        timeout=60,
    )
    resp.raise_for_status()
    result = resp.json()

    media_id = result.get("id")
    if not media_id:
        raise RuntimeError(f"Publish failed: {result}")

    log.info("Published Reel — media ID: %s", media_id)
    return media_id


def upload_reel(
    video_path: str | Path,
    caption: str,
    hashtags: list[str] | None = None,
    credentials_file: str | Path = "credentials/all_niches_ig.json",
    cover_timestamp_ms: int = 0,
    share_to_feed: bool = True,
    video_url: str | None = None,
) -> str:
    """
    Upload a video as an Instagram Reel.

    The Graph API requires the video to be accessible via a public URL.
    Either pass *video_url* directly (e.g. an S3/GCS pre-signed URL)
    or host the file yourself and supply the URL.

    Args:
        video_path: Local path to mp4 file (used for validation / fallback)
        caption: Reel caption text
        hashtags: List of hashtags (with or without # prefix)
        credentials_file: Path to Instagram credentials JSON
        cover_timestamp_ms: Cover image timestamp in milliseconds
        share_to_feed: Also share to Instagram feed grid (default True)
        video_url: Public URL where the video is hosted. If not provided,
                   the video is auto-uploaded to a temp host.

    Returns:
        Instagram media ID of the published Reel

    Raises:
        FileNotFoundError: If video or credentials file doesn't exist
        RuntimeError: On API errors
        TimeoutError: If processing takes too long
    """
    video_path = Path(video_path)
    creds_path = Path(credentials_file)

    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")
    if not creds_path.exists():
        raise FileNotFoundError(
            f"Credentials not found: {creds_path}. "
            "Run: python scripts/instagram_auth_setup.py"
        )
    if not video_url:
        video_url = _upload_to_temp_host(video_path)

    creds = _load_credentials(creds_path)

    # Build caption with hashtags
    if hashtags:
        tag_str = " ".join(
            t if t.startswith("#") else f"#{t}" for t in hashtags
        )
        full_caption = f"{caption}\n\n{tag_str}"
    else:
        full_caption = caption

    # Instagram captions max 2200 chars
    if len(full_caption) > 2200:
        log.warning("Caption truncated from %d to 2200 chars", len(full_caption))
        full_caption = full_caption[:2197] + "..."

    ig_user_id = creds["ig_user_id"]
    access_token = creds["access_token"]

    log.info("Uploading Reel: %s (%d chars caption)", video_path.name, len(full_caption))

    # Step 1: Create container
    container_id = _create_media_container(
        ig_user_id=ig_user_id,
        access_token=access_token,
        video_url=video_url,
        caption=full_caption,
        share_to_feed=share_to_feed,
    )

    # Step 2: Wait for processing
    _poll_container_status(container_id, access_token)

    # Step 3: Publish
    media_id = _publish_container(ig_user_id, container_id, access_token)

    log.info("Reel published successfully — media ID: %s", media_id)
    return media_id
