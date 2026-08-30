"""
Upload a video to a Facebook Page via the Graph API.

Uses the same credentials file as Instagram (credentials/all_niches_ig.json)
since the Page is linked to the same app. The user token is exchanged for a
Page Access Token before uploading.
"""

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

log = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.facebook.com/v21.0"

# Status polling config
_POLL_INTERVAL_S = 5
_POLL_MAX_ATTEMPTS = 120  # 10 minutes max


def _refresh_token(data: dict, creds_path: Path) -> dict:
    """Exchange current token for a fresh long-lived token via Meta API."""
    resp = requests.get(
        f"{GRAPH_API_BASE}/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": data["app_id"],
            "client_secret": data["app_secret"],
            "fb_exchange_token": data["access_token"],
        },
        timeout=15,
    )
    resp.raise_for_status()
    token_data = resp.json()
    data["access_token"] = token_data["access_token"]
    if "expires_in" in token_data:
        data["expires_at"] = (
            datetime.now(timezone.utc) + timedelta(seconds=token_data["expires_in"])
        ).isoformat()
    creds_path.write_text(json.dumps(data, indent=2))
    log.info("Facebook token refreshed, expires_at=%s", data.get("expires_at"))
    return data


def _load_credentials(creds_path: Path) -> dict:
    """Load credentials, refresh token if near expiry, validate required fields."""
    data = json.loads(creds_path.read_text())
    required = ("access_token", "page_id", "app_id", "app_secret")
    missing = [k for k in required if not data.get(k)]
    if missing:
        raise ValueError(
            f"Credentials file {creds_path} missing keys: {', '.join(missing)}. "
            "Ensure page_id is set in the credentials file."
        )
    expires_at = data.get("expires_at")
    if expires_at:
        try:
            exp_dt = datetime.fromisoformat(expires_at)
            days_left = (exp_dt - datetime.now(timezone.utc)).days
            if days_left < 7:
                log.info("Facebook token expires in %d days — refreshing", days_left)
                data = _refresh_token(data, creds_path)
        except Exception as exc:
            log.warning("Facebook token refresh failed: %s", exc)
    return data


def _get_page_access_token(user_token: str, page_id: str) -> str:
    """Exchange a user access token for a Page Access Token."""
    resp = requests.get(
        f"{GRAPH_API_BASE}/{page_id}",
        params={
            "fields": "access_token",
            "access_token": user_token,
        },
        timeout=30,
    )
    resp.raise_for_status()
    result = resp.json()

    page_token = result.get("access_token")
    if not page_token:
        raise RuntimeError(
            f"Could not get Page Access Token for page {page_id}: {result}"
        )

    log.info("Obtained Page Access Token for page %s", page_id)
    return page_token


def _upload_video_direct(
    page_id: str,
    page_token: str,
    video_path: Path,
    title: str,
    description: str,
) -> str:
    """Upload a video directly to a Facebook Page via the /videos endpoint.

    This uses the resumable upload flow:
    1. Start upload session
    2. Upload file data
    3. Finish upload

    Returns the video/post ID.
    """
    file_size = video_path.stat().st_size

    # Step 1: Start upload session
    log.info("Starting Facebook upload session for %s (%d bytes)...",
             video_path.name, file_size)
    start_resp = requests.post(
        f"{GRAPH_API_BASE}/{page_id}/videos",
        data={
            "upload_phase": "start",
            "file_size": file_size,
            "access_token": page_token,
        },
        timeout=60,
    )
    if not start_resp.ok:
        log.error("Facebook upload start failed: %s", start_resp.text)
    start_resp.raise_for_status()
    start_result = start_resp.json()

    upload_session_id = start_result.get("upload_session_id")
    video_id = start_result.get("video_id")
    if not upload_session_id:
        raise RuntimeError(f"Failed to start upload session: {start_result}")

    log.info("Upload session started: video_id=%s, session=%s",
             video_id, upload_session_id)

    # Step 2: Transfer the video file
    with open(video_path, "rb") as f:
        transfer_resp = requests.post(
            f"{GRAPH_API_BASE}/{page_id}/videos",
            data={
                "upload_phase": "transfer",
                "upload_session_id": upload_session_id,
                "start_offset": "0",
                "access_token": page_token,
            },
            files={
                "video_file_chunk": (video_path.name, f, "video/mp4"),
            },
            timeout=600,
        )
    transfer_resp.raise_for_status()
    transfer_result = transfer_resp.json()
    log.info("Video data transferred: %s", transfer_result)

    # Step 3: Finish upload
    finish_resp = requests.post(
        f"{GRAPH_API_BASE}/{page_id}/videos",
        data={
            "upload_phase": "finish",
            "upload_session_id": upload_session_id,
            "title": title[:254],
            "description": description,
            "access_token": page_token,
        },
        timeout=60,
    )
    finish_resp.raise_for_status()
    finish_result = finish_resp.json()

    if not finish_result.get("success", False) and not video_id:
        raise RuntimeError(f"Upload finish failed: {finish_result}")

    log.info("Upload finished: video_id=%s", video_id)
    return video_id


def _poll_video_status(video_id: str, page_token: str) -> None:
    """Poll until the uploaded video is ready (not still processing)."""
    for attempt in range(1, _POLL_MAX_ATTEMPTS + 1):
        resp = requests.get(
            f"{GRAPH_API_BASE}/{video_id}",
            params={
                "fields": "status",
                "access_token": page_token,
            },
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()
        status = result.get("status", {})
        processing_phase = status.get("processing_phase", {})
        phase_status = processing_phase.get("status", "unknown")

        if phase_status == "complete":
            log.info("Video %s processing complete", video_id)
            return
        elif phase_status == "error":
            errors = processing_phase.get("errors", [])
            raise RuntimeError(
                f"Video processing failed for {video_id}: {errors}"
            )

        log.info(
            "Video processing: %s (attempt %d/%d) -- waiting %ds...",
            phase_status, attempt, _POLL_MAX_ATTEMPTS, _POLL_INTERVAL_S,
        )
        time.sleep(_POLL_INTERVAL_S)

    log.warning(
        "Video %s may still be processing after %ds -- returning anyway",
        video_id, _POLL_MAX_ATTEMPTS * _POLL_INTERVAL_S,
    )


def upload_video(
    video_path: str | Path,
    title: str,
    description: str,
    credentials_file: str | Path = "credentials/all_niches_ig.json",
    wait_for_processing: bool = False,
) -> str:
    """
    Upload a video to a Facebook Page.

    Uses the resumable upload flow via the /{page_id}/videos endpoint.
    The user access token from the credentials file is exchanged for a
    Page Access Token before uploading.

    Args:
        video_path: Path to mp4 file
        title: Video title
        description: Video description text
        credentials_file: Path to credentials JSON (same as Instagram)
        wait_for_processing: If True, poll until Facebook finishes processing

    Returns:
        Facebook video ID

    Raises:
        FileNotFoundError: If video or credentials file doesn't exist
        RuntimeError: On API errors
    """
    video_path = Path(video_path)
    creds_path = Path(credentials_file)

    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")
    if not creds_path.exists():
        raise FileNotFoundError(
            f"Credentials not found: {creds_path}. "
            "Ensure credentials/all_niches_ig.json exists with page_id."
        )

    creds = _load_credentials(creds_path)
    page_id = creds["page_id"]
    user_token = creds["access_token"]

    # Exchange user token for page token
    page_token = _get_page_access_token(user_token, page_id)

    log.info("Uploading video to Facebook Page %s: %s", page_id, video_path.name)

    video_id = _upload_video_direct(
        page_id=page_id,
        page_token=page_token,
        video_path=video_path,
        title=title,
        description=description,
    )

    if wait_for_processing:
        _poll_video_status(video_id, page_token)

    log.info(
        "Facebook upload complete: https://www.facebook.com/%s/videos/%s",
        page_id, video_id,
    )
    return video_id
