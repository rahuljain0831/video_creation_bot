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

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

log = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
_ROOT_FOLDER_NAME = "video-uploads"

_service_cache = None


def _build_service():
    """Build and cache Google Drive API service.

    Reads credentials path from GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON env var.
    Uses Credentials.from_service_account_file() with drive.file scope.
    Caches in module-level _service_cache.

    Returns:
        googleapiclient.discovery.Resource: Google Drive API service object
    """
    global _service_cache

    if _service_cache is not None:
        return _service_cache

    creds_path = os.getenv("GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON")
    if not creds_path:
        raise ValueError(
            "GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON env var not set. "
            "Point to service account JSON file."
        )

    creds = Credentials.from_service_account_file(creds_path, scopes=_SCOPES)
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

    # Query for existing folder
    results = service.files().list(q=query, spaces="drive", fields="files(id)", pageSize=1).execute()
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


def _get_subfolder(subfolder: str) -> str:
    """Get ID for video-uploads/<subfolder>, creating both root and sub if needed.

    Args:
        subfolder: Subfolder name (e.g., "pending", "uploaded", "failed")

    Returns:
        str: Subfolder ID
    """
    root_id = _get_or_create_folder(_ROOT_FOLDER_NAME)
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
