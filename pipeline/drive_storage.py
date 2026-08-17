"""Google Drive storage for scheduled video uploads.

Uses a service account — no user OAuth required.
Folder structure in Drive:
  video-uploads/
    pending/    — videos awaiting scheduled upload
    uploaded/   — successfully uploaded to social platform
    failed/     — upload failed
"""
import io
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

log = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
_ROOT_FOLDER_NAME = "video-uploads"
_TOKEN_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "drive_token.json")

_service_cache = None


def _build_service():
    """Build and cache Google Drive API service using OAuth2 user credentials.

    First run opens browser for login. Token cached in drive_token.json for reuse.
    Falls back to service account if GOOGLE_DRIVE_AUTH=service_account.

    Returns:
        googleapiclient.discovery.Resource: Google Drive API service object
    """
    global _service_cache

    if _service_cache is not None:
        return _service_cache

    # Check for service account override
    if os.getenv("GOOGLE_DRIVE_AUTH") == "service_account":
        from google.oauth2.service_account import Credentials as SACredentials
        creds_path = os.getenv("GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON")
        if not creds_path:
            raise ValueError("GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON env var not set.")
        creds = SACredentials.from_service_account_file(creds_path, scopes=_SCOPES)
        _service_cache = build("drive", "v3", credentials=creds)
        return _service_cache

    # OAuth2 user credentials
    creds = None
    if os.path.exists(_TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(_TOKEN_PATH, _SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            oauth_creds = os.getenv(
                "GOOGLE_DRIVE_OAUTH_JSON",
                os.path.join(os.path.dirname(os.path.dirname(__file__)), "oauth_credentials.json"),
            )
            if not os.path.exists(oauth_creds):
                raise ValueError(
                    f"OAuth credentials not found at {oauth_creds}. "
                    "Download from Google Cloud Console → Credentials → OAuth client ID."
                )
            flow = InstalledAppFlow.from_client_secrets_file(oauth_creds, _SCOPES)
            creds = flow.run_local_server(port=0)

        # Save token for reuse
        with open(_TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
        log.info("Drive OAuth token saved to %s", _TOKEN_PATH)

    _service_cache = build("drive", "v3", credentials=creds)
    return _service_cache


def _get_or_create_folder(name: str, parent_id: str | None = None) -> str:
    """Query Drive for folder by name+parent. Create if missing. Return folder ID.

    Args:
        name: Folder name
        parent_id: Parent folder ID (or None for root)

    Returns:
        str: Folder ID
    """
    service = _build_service()

    # Build query: match name and parent (if specified)
    query_parts = [f"name = '{name}'", "mimeType = 'application/vnd.google-apps.folder'", "trashed = false"]
    if parent_id:
        query_parts.append(f"'{parent_id}' in parents")

    query = " and ".join(query_parts)

    # Query for existing folder (includeItemsFromAllDrives lets SA find shared folders)
    results = service.files().list(
        q=query, spaces="drive", fields="files(id)", pageSize=1,
        includeItemsFromAllDrives=True, supportsAllDrives=True,
    ).execute()
    files = results.get("files", [])

    if files:
        return files[0]["id"]

    # Create if not found
    file_metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        file_metadata["parents"] = [parent_id]

    folder = service.files().create(body=file_metadata, fields="id").execute()
    return folder["id"]


def _get_root_folder_id() -> str:
    """Return root folder ID — prefers GOOGLE_DRIVE_FOLDER_ID env var (shared folder),
    falls back to creating/finding a 'video-uploads' folder."""
    shared_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
    if shared_id:
        return shared_id
    return _get_or_create_folder(_ROOT_FOLDER_NAME)


def _get_subfolder(subfolder: str) -> str:
    """Get ID for <root>/<subfolder>, creating sub if needed.

    Supports env var overrides: GOOGLE_DRIVE_PENDING_ID, GOOGLE_DRIVE_UPLOADED_ID,
    GOOGLE_DRIVE_FAILED_ID — needed when service account can't discover shared folders
    via parent query.

    Args:
        subfolder: Subfolder name (e.g., "pending", "uploaded", "failed")

    Returns:
        str: Subfolder ID
    """
    env_key = f"GOOGLE_DRIVE_{subfolder.upper()}_ID"
    env_id = os.getenv(env_key)
    if env_id:
        return env_id

    root_id = _get_root_folder_id()
    sub_id = _get_or_create_folder(subfolder, parent_id=root_id)
    return sub_id


def upload_to_drive(local_path: Path, folder_name: str = "pending") -> str:
    """Upload file to subfolder using resumable upload. Return file ID.

    Args:
        local_path: Path to local file to upload
        folder_name: Subfolder name in video-uploads (e.g., "pending")

    Returns:
        str: Drive file ID
    """
    local_path = Path(local_path)
    if not local_path.exists():
        raise FileNotFoundError(f"File not found: {local_path}")

    service = _build_service()
    folder_id = _get_subfolder(folder_name)

    file_metadata = {
        "name": local_path.name,
        "parents": [folder_id],
    }

    media = MediaFileUpload(str(local_path), resumable=True)
    file_obj = service.files().create(body=file_metadata, media_body=media, fields="id").execute()

    return file_obj["id"]


def download_from_drive(file_id: str, dest_path: Path) -> Path:
    """Download file from Drive using MediaIoBaseDownload. Create parent dirs. Return dest_path.

    Args:
        file_id: Drive file ID
        dest_path: Destination path for downloaded file

    Returns:
        Path: Destination path
    """
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    service = _build_service()

    request = service.files().get_media(fileId=file_id)
    with io.FileIO(str(dest_path), "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()

    return dest_path


def move_drive_file(file_id: str, dest_folder_name: str) -> None:
    """Get current parents, update file with addParents/removeParents.

    Args:
        file_id: Drive file ID
        dest_folder_name: Destination subfolder name in video-uploads
    """
    service = _build_service()

    # Get current parents
    file_obj = service.files().get(fileId=file_id, fields="parents").execute()
    previous_parents = ",".join(file_obj.get("parents", []))

    # Get destination folder ID
    dest_folder_id = _get_subfolder(dest_folder_name)

    # Move file
    service.files().update(
        fileId=file_id,
        addParents=dest_folder_id,
        removeParents=previous_parents,
        fields="id, parents",
    ).execute()


def delete_old_files(folder_name: str, older_than_days: int = 7) -> int:
    """List files in folder older than cutoff, delete each, return count.

    Args:
        folder_name: Subfolder name in video-uploads
        older_than_days: Delete files older than this many days (default 7)

    Returns:
        int: Number of files deleted
    """
    service = _build_service()
    folder_id = _get_subfolder(folder_name)

    # Calculate cutoff time
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    cutoff_str = cutoff.isoformat()

    # Query for files older than cutoff
    query = f"'{folder_id}' in parents and createdTime < '{cutoff_str}' and trashed = false"

    results = service.files().list(q=query, spaces="drive", fields="files(id)", pageSize=100).execute()
    files = results.get("files", [])

    # Delete each file
    for file_obj in files:
        service.files().delete(fileId=file_obj["id"]).execute()
        log.info(f"Deleted file {file_obj['id']} from {folder_name}")

    return len(files)
