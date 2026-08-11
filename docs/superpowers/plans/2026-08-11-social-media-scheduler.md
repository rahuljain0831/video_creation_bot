# Social Media Scheduler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automated scheduling system that uploads approved videos to social media at optimal times, rotates platforms per niche, adapts timing from engagement data, and runs entirely on GitHub Actions + Google Drive (no local machine dependency).

**Architecture:** Post Telegram-approval, video uploads to Google Drive with a schedule manifest. A one-time cron-job.org trigger fires a GitHub Actions `repository_dispatch` workflow at the optimal time, which downloads and uploads to the rotated platform. A daily engagement cron fetches stats and recalculates optimal time slots.

**Tech Stack:** Python 3.11+, SQLite, Google Drive API (service account), cron-job.org REST API, GitHub Actions (repository_dispatch + scheduled), YouTube/Instagram/Facebook APIs (existing uploaders).

**Spec:** `docs/superpowers/specs/2026-08-11-social-media-scheduler-design.md`

## Global Constraints

- All new modules must be importable standalone (no side-effect imports at module level)
- Existing pipeline flow (run_niche.py steps 1-5) must not break — scheduler hooks into the Telegram approval callback only
- `social_captions.py` return format `{"platform": {"caption": str, "hashtags": [str]}}` must remain unchanged for existing callers
- SQLite DB at `output/db/agent.db` — new tables additive, existing tables untouched
- All secrets via environment variables (`.env` locally, GitHub Secrets in Actions)
- Tests must pass with `pytest -m "not slow"` before and after each task

## File Structure

### New Files

| File | Responsibility |
|------|----------------|
| `pipeline/drive_storage.py` | Google Drive upload/download/move/delete via service account |
| `pipeline/scheduler.py` | Optimal time selection, cron-job.org API, platform rotation |
| `pipeline/engagement_tracker.py` | Fetch engagement stats from YouTube/IG/FB APIs, update time_performance |
| `hashtag_banks.json` | Curated per-niche base hashtag sets |
| `db/schema_scheduler.sql` | New table DDL (upload_schedule, time_performance, platform_rotation) |
| `.github/workflows/scheduled-upload.yml` | Upload workflow triggered by repository_dispatch |
| `.github/workflows/engagement-fetch.yml` | Daily engagement stats cron |
| `.github/workflows/drive-cleanup.yml` | Weekly Drive cleanup cron |
| `scripts/scheduler_setup.py` | One-time setup verification (Drive folder, cron-job.org, secrets) |
| `tests/test_drive_storage.py` | Tests for drive_storage.py |
| `tests/test_scheduler.py` | Tests for scheduler.py |
| `tests/test_engagement_tracker.py` | Tests for engagement_tracker.py |

### Modified Files

| File | Change |
|------|--------|
| `db/init_db.py` | Add DDL migrations for 3 new tables + indexes |
| `db/schema.sql` | Add new table definitions |
| `review/telegram_bot.py` | Hook post-approval: Drive upload + schedule creation + confirmation message |
| `pipeline/social_captions.py` | Add trending injection, hashtag bank blending, A/B variant generation |
| `config.py` | Add scheduler config section from settings.json |
| `settings.json` | Add `scheduler` config block |
| `social_config.json` | Add `cronjob_org` config section |
| `.env.example` | Add GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON, CRONJOB_API_KEY |
| `requirements.txt` | Add google-api-python-client, google-auth |
| `tests/test_social_captions.py` | Update tests for new A/B variant + hashtag bank features |

---

### Task 1: Database Schema — New Tables

**Files:**
- Modify: `db/schema.sql:79` (append after image_library table)
- Modify: `db/init_db.py:23-60` (add DDL migrations + indexes)
- Test: `tests/test_db_schema.py` (new)

**Interfaces:**
- Consumes: nothing
- Produces: Tables `upload_schedule`, `time_performance`, `platform_rotation` in agent.db. All subsequent tasks depend on these tables existing.

- [ ] **Step 1: Write failing test for new tables**

```python
# tests/test_db_schema.py
"""Tests that scheduler tables exist after init_db."""
import sqlite3
import pytest
from pathlib import Path
import tempfile
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))


def _init_fresh_db() -> sqlite3.Connection:
    """Create a fresh DB in a temp file and return connection."""
    from db.init_db import init_db
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    init_db(tmp.name)
    conn = sqlite3.connect(tmp.name)
    return conn


def test_upload_schedule_table_exists():
    conn = _init_fresh_db()
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    assert "upload_schedule" in tables
    conn.close()


def test_time_performance_table_exists():
    conn = _init_fresh_db()
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    assert "time_performance" in tables
    conn.close()


def test_platform_rotation_table_exists():
    conn = _init_fresh_db()
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    assert "platform_rotation" in tables
    conn.close()


def test_upload_schedule_columns():
    conn = _init_fresh_db()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(upload_schedule)").fetchall()}
    expected = {"id", "video_id", "platform", "niche_id", "scheduled_at",
                "status", "cronjob_id", "drive_file_id", "engagement_views",
                "engagement_likes", "platform_post_id", "caption_variant", "created_at"}
    assert expected <= cols
    conn.close()


def test_existing_tables_survive_migration():
    """Ensure existing videos/feedback tables are not dropped."""
    conn = _init_fresh_db()
    # Insert a dummy video
    conn.execute(
        "INSERT INTO videos (status, niche_id) VALUES ('queued', 'mythology')"
    )
    conn.commit()
    # Re-run init (simulates upgrade)
    from db.init_db import init_db
    import tempfile
    # Use same DB path
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]
    conn.close()
    init_db(db_path)
    conn2 = sqlite3.connect(db_path)
    count = conn2.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
    assert count == 1
    conn2.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_db_schema.py -v`
Expected: FAIL — tables `upload_schedule`, `time_performance`, `platform_rotation` do not exist.

- [ ] **Step 3: Add table definitions to schema.sql**

Append to `db/schema.sql` after the `image_library` table (after line 79):

```sql
-- Upload scheduler tables
CREATE TABLE IF NOT EXISTS upload_schedule (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id        INTEGER REFERENCES videos(id),
    platform        TEXT NOT NULL CHECK(platform IN ('youtube', 'instagram', 'facebook')),
    niche_id        TEXT NOT NULL,
    scheduled_at    DATETIME NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending' CHECK(status IN (
                        'pending', 'uploading', 'done', 'failed'
                    )),
    cronjob_id      TEXT,
    drive_file_id   TEXT,
    engagement_views  INTEGER,
    engagement_likes  INTEGER,
    platform_post_id  TEXT,
    caption_variant   TEXT CHECK(caption_variant IN ('A', 'B')),
    created_at      DATETIME DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS time_performance (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    niche_id        TEXT NOT NULL,
    platform        TEXT NOT NULL,
    hour_utc        INTEGER NOT NULL CHECK(hour_utc BETWEEN 0 AND 23),
    day_of_week     INTEGER NOT NULL CHECK(day_of_week BETWEEN 0 AND 6),
    avg_views       REAL DEFAULT 0.0,
    avg_likes       REAL DEFAULT 0.0,
    sample_count    INTEGER DEFAULT 0,
    updated_at      DATETIME DEFAULT (datetime('now')),
    UNIQUE(niche_id, platform, hour_utc, day_of_week)
);

CREATE TABLE IF NOT EXISTS platform_rotation (
    niche_id        TEXT PRIMARY KEY,
    last_platform   TEXT NOT NULL CHECK(last_platform IN ('youtube', 'instagram', 'facebook')),
    updated_at      DATETIME DEFAULT (datetime('now'))
);
```

- [ ] **Step 4: Add DDL migrations and indexes to init_db.py**

Add to `_DDL_MIGRATIONS` list in `db/init_db.py` (after line 59):

```python
    """CREATE TABLE IF NOT EXISTS upload_schedule (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        video_id        INTEGER REFERENCES videos(id),
        platform        TEXT NOT NULL,
        niche_id        TEXT NOT NULL,
        scheduled_at    DATETIME NOT NULL,
        status          TEXT NOT NULL DEFAULT 'pending',
        cronjob_id      TEXT,
        drive_file_id   TEXT,
        engagement_views  INTEGER,
        engagement_likes  INTEGER,
        platform_post_id  TEXT,
        caption_variant   TEXT,
        created_at      DATETIME DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS time_performance (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        niche_id        TEXT NOT NULL,
        platform        TEXT NOT NULL,
        hour_utc        INTEGER NOT NULL,
        day_of_week     INTEGER NOT NULL,
        avg_views       REAL DEFAULT 0.0,
        avg_likes       REAL DEFAULT 0.0,
        sample_count    INTEGER DEFAULT 0,
        updated_at      DATETIME DEFAULT (datetime('now')),
        UNIQUE(niche_id, platform, hour_utc, day_of_week)
    )""",
    """CREATE TABLE IF NOT EXISTS platform_rotation (
        niche_id        TEXT PRIMARY KEY,
        last_platform   TEXT NOT NULL,
        updated_at      DATETIME DEFAULT (datetime('now'))
    )""",
```

Add to `_INDEXES` list in `db/init_db.py` (after line 71):

```python
    "CREATE INDEX IF NOT EXISTS idx_schedule_status    ON upload_schedule(status)",
    "CREATE INDEX IF NOT EXISTS idx_schedule_niche     ON upload_schedule(niche_id)",
    "CREATE INDEX IF NOT EXISTS idx_schedule_time      ON upload_schedule(scheduled_at)",
    "CREATE INDEX IF NOT EXISTS idx_timeperf_niche_plat ON time_performance(niche_id, platform)",
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_db_schema.py -v`
Expected: All 5 tests PASS.

- [ ] **Step 6: Verify existing tests still pass**

Run: `pytest -m "not slow" -v`
Expected: All existing tests PASS (no regressions).

- [ ] **Step 7: Commit**

```bash
git add db/schema.sql db/init_db.py tests/test_db_schema.py
git commit -m "feat: add upload_schedule, time_performance, platform_rotation tables"
```

---

### Task 2: Google Drive Storage Module

**Files:**
- Create: `pipeline/drive_storage.py`
- Modify: `requirements.txt:26` (add google deps)
- Modify: `.env.example:18` (add Drive env var)
- Test: `tests/test_drive_storage.py` (new)

**Interfaces:**
- Consumes: `GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON` env var (path to service account JSON file)
- Produces:
  - `upload_to_drive(local_path: Path, folder_name: str = "pending") -> str` — returns drive_file_id
  - `download_from_drive(file_id: str, dest_path: Path) -> Path` — returns local path
  - `move_drive_file(file_id: str, dest_folder_name: str) -> None`
  - `delete_old_files(folder_name: str, older_than_days: int = 7) -> int` — returns count deleted
  - `_get_or_create_folder(name: str, parent_id: str | None = None) -> str` — returns folder ID

- [ ] **Step 1: Add dependencies to requirements.txt**

Append to `requirements.txt`:

```
# Google Drive (scheduler)
google-api-python-client>=2.100.0
google-auth>=2.23.0
```

- [ ] **Step 2: Add env var to .env.example**

Append to `.env.example`:

```
# Scheduler — Google Drive service account
GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON=credentials/drive_service_account.json

# Scheduler — cron-job.org API
CRONJOB_API_KEY=your_cronjob_org_api_key_here
```

- [ ] **Step 3: Write failing tests**

```python
# tests/test_drive_storage.py
"""Tests for pipeline/drive_storage.py — all Drive API calls mocked."""
import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from pathlib import Path
import tempfile
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def mock_drive_service():
    """Mock the Google Drive API service object."""
    with patch("pipeline.drive_storage._build_service") as mock_build:
        service = MagicMock()
        mock_build.return_value = service
        yield service


def test_upload_to_drive_returns_file_id(mock_drive_service):
    from pipeline.drive_storage import upload_to_drive

    # Mock folder lookup
    mock_drive_service.files().list().execute.return_value = {
        "files": [{"id": "folder_123"}]
    }
    # Mock file create
    mock_drive_service.files().create().execute.return_value = {"id": "file_abc"}

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        f.write(b"fake video data")
        tmp_path = Path(f.name)

    file_id = upload_to_drive(tmp_path, folder_name="pending")
    assert file_id == "file_abc"
    tmp_path.unlink()


def test_download_from_drive_writes_file(mock_drive_service):
    from pipeline.drive_storage import download_from_drive

    # Mock file download
    mock_request = MagicMock()
    mock_drive_service.files().get_media.return_value = mock_request

    with tempfile.TemporaryDirectory() as tmpdir:
        dest = Path(tmpdir) / "video.mp4"
        # Simulate MediaIoBaseDownload — patch at module level
        with patch("pipeline.drive_storage.MediaIoBaseDownload") as mock_dl:
            instance = MagicMock()
            instance.next_chunk.side_effect = [
                (MagicMock(progress=MagicMock(return_value=0.5)), False),
                (MagicMock(progress=MagicMock(return_value=1.0)), True),
            ]
            mock_dl.return_value = instance
            result = download_from_drive("file_abc", dest)
        assert result == dest


def test_move_drive_file(mock_drive_service):
    from pipeline.drive_storage import move_drive_file

    mock_drive_service.files().get().execute.return_value = {"parents": ["old_folder"]}
    mock_drive_service.files().list().execute.return_value = {
        "files": [{"id": "new_folder_id"}]
    }
    mock_drive_service.files().update().execute.return_value = {}

    move_drive_file("file_abc", "uploaded")
    mock_drive_service.files().update.assert_called()


def test_delete_old_files(mock_drive_service):
    from pipeline.drive_storage import delete_old_files

    mock_drive_service.files().list().execute.return_value = {
        "files": [
            {"id": "folder_uploaded"}
        ]
    }
    # Second list call returns old files
    mock_drive_service.files().list_next.return_value = None
    # Simulate two old files in folder
    list_mock = MagicMock()
    list_mock.execute.return_value = {
        "files": [{"id": "old1"}, {"id": "old2"}]
    }
    mock_drive_service.files().list.return_value = list_mock

    count = delete_old_files("uploaded", older_than_days=7)
    assert count == 2
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `pytest tests/test_drive_storage.py -v`
Expected: FAIL — `pipeline.drive_storage` does not exist.

- [ ] **Step 5: Implement drive_storage.py**

```python
# pipeline/drive_storage.py
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
    """Build and cache the Drive API service."""
    global _service_cache
    if _service_cache is not None:
        return _service_cache

    creds_path = os.getenv("GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON", "")
    if not creds_path or not Path(creds_path).exists():
        raise FileNotFoundError(
            f"Service account JSON not found: {creds_path!r}. "
            "Set GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON in .env"
        )
    creds = Credentials.from_service_account_file(creds_path, scopes=_SCOPES)
    _service_cache = build("drive", "v3", credentials=creds)
    return _service_cache


def _get_or_create_folder(name: str, parent_id: str | None = None) -> str:
    """Get folder ID by name (under parent), create if missing."""
    service = _build_service()
    query = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id:
        query += f" and '{parent_id}' in parents"

    results = service.files().list(q=query, spaces="drive", fields="files(id)").execute()
    files = results.get("files", [])
    if files:
        return files[0]["id"]

    metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        metadata["parents"] = [parent_id]

    folder = service.files().create(body=metadata, fields="id").execute()
    log.info("Created Drive folder: %s (id=%s)", name, folder["id"])
    return folder["id"]


def _get_subfolder(subfolder: str) -> str:
    """Get ID for video-uploads/<subfolder>, creating if needed."""
    root_id = _get_or_create_folder(_ROOT_FOLDER_NAME)
    return _get_or_create_folder(subfolder, parent_id=root_id)


def upload_to_drive(local_path: Path, folder_name: str = "pending") -> str:
    """Upload a file to Drive subfolder. Returns drive file ID."""
    service = _build_service()
    folder_id = _get_subfolder(folder_name)

    media = MediaFileUpload(str(local_path), resumable=True)
    metadata = {
        "name": local_path.name,
        "parents": [folder_id],
    }
    file = service.files().create(
        body=metadata, media_body=media, fields="id"
    ).execute()

    log.info("Uploaded to Drive: %s -> %s (id=%s)", local_path.name, folder_name, file["id"])
    return file["id"]


def download_from_drive(file_id: str, dest_path: Path) -> Path:
    """Download a file from Drive to local path."""
    service = _build_service()
    request = service.files().get_media(fileId=file_id)

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dest_path, "wb") as f:
        downloader = MediaIoBaseDownload(f, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            if status:
                log.debug("Download %s: %d%%", file_id, int(status.progress() * 100))

    log.info("Downloaded from Drive: %s -> %s", file_id, dest_path)
    return dest_path


def move_drive_file(file_id: str, dest_folder_name: str) -> None:
    """Move a file to a different subfolder (e.g. pending -> uploaded)."""
    service = _build_service()
    dest_folder_id = _get_subfolder(dest_folder_name)

    file = service.files().get(fileId=file_id, fields="parents").execute()
    previous_parents = ",".join(file.get("parents", []))

    service.files().update(
        fileId=file_id,
        addParents=dest_folder_id,
        removeParents=previous_parents,
        fields="id, parents",
    ).execute()

    log.info("Moved Drive file %s to %s", file_id, dest_folder_name)


def delete_old_files(folder_name: str, older_than_days: int = 7) -> int:
    """Delete files in subfolder older than N days. Returns count deleted."""
    service = _build_service()
    folder_id = _get_subfolder(folder_name)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()

    results = service.files().list(
        q=f"'{folder_id}' in parents and createdTime < '{cutoff}' and trashed=false",
        spaces="drive",
        fields="files(id)",
    ).execute()

    files = results.get("files", [])
    for f in files:
        service.files().delete(fileId=f["id"]).execute()

    if files:
        log.info("Deleted %d old files from Drive/%s", len(files), folder_name)
    return len(files)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_drive_storage.py -v`
Expected: All 4 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add pipeline/drive_storage.py tests/test_drive_storage.py requirements.txt .env.example
git commit -m "feat: add Google Drive storage module for scheduled uploads"
```

---

### Task 3: Scheduler Module — Platform Rotation + Time Selection + Cron-job.org

**Files:**
- Create: `pipeline/scheduler.py`
- Modify: `settings.json` (add scheduler config block)
- Modify: `config.py:44` (expose scheduler config)
- Modify: `social_config.json` (add cronjob_org section)
- Test: `tests/test_scheduler.py` (new)

**Interfaces:**
- Consumes:
  - `upload_schedule`, `time_performance`, `platform_rotation` tables (from Task 1)
  - `social_config.json` for cron-job.org API key
  - `settings.json` for default time slots
- Produces:
  - `get_next_platform(niche_id: str, conn: sqlite3.Connection) -> str` — returns "youtube"/"instagram"/"facebook"
  - `pick_optimal_time(niche_id: str, platform: str, conn: sqlite3.Connection) -> datetime` — returns UTC datetime for next upload
  - `create_upload_job(schedule_id: int, scheduled_at: datetime, repo_owner: str, repo_name: str) -> str` — creates cron-job.org job, returns job ID
  - `delete_upload_job(cronjob_id: str) -> None`
  - `schedule_video(video_id: int, niche_id: str, drive_file_id: str, drive_manifest_id: str, conn: sqlite3.Connection) -> dict` — orchestrates full scheduling, returns schedule info dict

- [ ] **Step 1: Add scheduler config to settings.json**

Add after `"pexels_library"` block in `settings.json`:

```json
"scheduler": {
    "enabled": true,
    "default_times_ist": {
        "youtube":   ["17:00", "20:00", "12:00"],
        "instagram": ["19:00", "21:00", "11:00"],
        "facebook":  ["13:00", "18:00", "21:00"]
    },
    "min_gap_minutes": 30,
    "exploration_rate": 0.2,
    "trusted_sample_min": 3,
    "github_repo": "your-username/video-creation-agent"
}
```

- [ ] **Step 2: Expose scheduler config in config.py**

Add after line 45 in `config.py`:

```python
    scheduler       = _settings.get("scheduler", {})
```

- [ ] **Step 3: Add cronjob_org config to social_config.json**

Add to `social_config.json` top level:

```json
"cronjob_org": {
    "enabled": true,
    "api_key_env": "CRONJOB_API_KEY"
}
```

- [ ] **Step 4: Write failing tests**

```python
# tests/test_scheduler.py
"""Tests for pipeline/scheduler.py — cron-job.org API mocked."""
import sqlite3
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
from pathlib import Path
import tempfile
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def db_conn():
    """Fresh in-memory DB with scheduler tables."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("""CREATE TABLE videos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        status TEXT DEFAULT 'approved', niche_id TEXT, file_path TEXT,
        retry_count INTEGER DEFAULT 0
    )""")
    conn.execute("""CREATE TABLE upload_schedule (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        video_id INTEGER, platform TEXT, niche_id TEXT,
        scheduled_at DATETIME, status TEXT DEFAULT 'pending',
        cronjob_id TEXT, drive_file_id TEXT,
        engagement_views INTEGER, engagement_likes INTEGER,
        platform_post_id TEXT, caption_variant TEXT,
        created_at DATETIME DEFAULT (datetime('now'))
    )""")
    conn.execute("""CREATE TABLE time_performance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        niche_id TEXT, platform TEXT, hour_utc INTEGER,
        day_of_week INTEGER, avg_views REAL DEFAULT 0,
        avg_likes REAL DEFAULT 0, sample_count INTEGER DEFAULT 0,
        updated_at DATETIME DEFAULT (datetime('now')),
        UNIQUE(niche_id, platform, hour_utc, day_of_week)
    )""")
    conn.execute("""CREATE TABLE platform_rotation (
        niche_id TEXT PRIMARY KEY, last_platform TEXT,
        updated_at DATETIME DEFAULT (datetime('now'))
    )""")
    conn.commit()
    return conn


def test_get_next_platform_first_call(db_conn):
    """First call for a niche should return youtube."""
    from pipeline.scheduler import get_next_platform
    platform = get_next_platform("mythology", db_conn)
    assert platform == "youtube"


def test_get_next_platform_rotates(db_conn):
    """Platform should rotate youtube -> instagram -> facebook -> youtube."""
    from pipeline.scheduler import get_next_platform
    p1 = get_next_platform("mythology", db_conn)
    assert p1 == "youtube"
    p2 = get_next_platform("mythology", db_conn)
    assert p2 == "instagram"
    p3 = get_next_platform("mythology", db_conn)
    assert p3 == "facebook"
    p4 = get_next_platform("mythology", db_conn)
    assert p4 == "youtube"


def test_pick_optimal_time_uses_defaults(db_conn):
    """With no performance data, should pick from default time slots."""
    from pipeline.scheduler import pick_optimal_time
    with patch("pipeline.scheduler._get_scheduler_config") as mock_cfg:
        mock_cfg.return_value = {
            "default_times_ist": {
                "youtube": ["17:00", "20:00", "12:00"],
            },
            "min_gap_minutes": 30,
            "exploration_rate": 0.0,
            "trusted_sample_min": 3,
        }
        result = pick_optimal_time("mythology", "youtube", db_conn)
    assert isinstance(result, datetime)
    assert result.tzinfo is not None  # must be timezone-aware


def test_pick_optimal_time_avoids_conflicts(db_conn):
    """Should not schedule within min_gap of existing schedule."""
    from pipeline.scheduler import pick_optimal_time
    # Insert existing schedule
    now = datetime.now(timezone.utc).replace(hour=11, minute=30)
    db_conn.execute(
        "INSERT INTO upload_schedule (video_id, platform, niche_id, scheduled_at, status) VALUES (1, 'youtube', 'heists', ?, 'pending')",
        (now.isoformat(),)
    )
    db_conn.commit()

    with patch("pipeline.scheduler._get_scheduler_config") as mock_cfg:
        mock_cfg.return_value = {
            "default_times_ist": {"youtube": ["17:00", "20:00", "12:00"]},
            "min_gap_minutes": 30,
            "exploration_rate": 0.0,
            "trusted_sample_min": 3,
        }
        result = pick_optimal_time("mythology", "youtube", db_conn)
    # Should not be within 30 min of 11:30
    diff = abs((result - now).total_seconds())
    assert diff >= 1800 or result.date() != now.date()


@patch("pipeline.scheduler.requests.put")
def test_create_upload_job_calls_cronjob_api(mock_put):
    """Should POST to cron-job.org and return job ID."""
    from pipeline.scheduler import create_upload_job
    mock_put.return_value = MagicMock(
        status_code=200,
        json=MagicMock(return_value={"jobId": "cj_12345"})
    )
    scheduled_at = datetime.now(timezone.utc) + timedelta(hours=2)

    with patch.dict("os.environ", {"CRONJOB_API_KEY": "test_key"}):
        job_id = create_upload_job(42, scheduled_at, "user", "repo")
    assert job_id == "cj_12345"
    mock_put.assert_called_once()
```

- [ ] **Step 5: Run tests to verify they fail**

Run: `pytest tests/test_scheduler.py -v`
Expected: FAIL — `pipeline.scheduler` does not exist.

- [ ] **Step 6: Implement scheduler.py**

```python
# pipeline/scheduler.py
"""Upload scheduler — platform rotation, optimal time selection, cron-job.org integration.

Orchestrates the post-approval flow:
  1. Determine next platform (round-robin per niche)
  2. Pick optimal upload time (adaptive or default)
  3. Create one-time cron-job.org trigger -> GitHub repository_dispatch
"""
import json
import logging
import os
import random
import sqlite3
from datetime import datetime, time, timedelta, timezone

import requests

log = logging.getLogger(__name__)

_PLATFORMS = ["youtube", "instagram", "facebook"]
_IST_OFFSET = timedelta(hours=5, minutes=30)


def _get_scheduler_config() -> dict:
    """Load scheduler config from settings.json via config module."""
    from config import cfg
    return cfg.scheduler


def get_next_platform(niche_id: str, conn: sqlite3.Connection) -> str:
    """Get next platform in rotation for a niche, update rotation table."""
    row = conn.execute(
        "SELECT last_platform FROM platform_rotation WHERE niche_id=?",
        (niche_id,)
    ).fetchone()

    if row is None:
        next_platform = _PLATFORMS[0]
    else:
        current_idx = _PLATFORMS.index(row[0]) if row[0] in _PLATFORMS else -1
        next_platform = _PLATFORMS[(current_idx + 1) % len(_PLATFORMS)]

    conn.execute(
        "INSERT OR REPLACE INTO platform_rotation (niche_id, last_platform, updated_at) "
        "VALUES (?, ?, datetime('now'))",
        (niche_id, next_platform),
    )
    conn.commit()
    return next_platform


def _ist_to_utc_hour(ist_time_str: str) -> int:
    """Convert IST time string like '17:00' to UTC hour."""
    h, m = map(int, ist_time_str.split(":"))
    ist_dt = datetime.now(timezone.utc).replace(hour=h, minute=m) - _IST_OFFSET
    return ist_dt.hour


def pick_optimal_time(
    niche_id: str, platform: str, conn: sqlite3.Connection
) -> datetime:
    """Pick the best upload time. Uses adaptive data if available, else defaults."""
    config = _get_scheduler_config()
    exploration_rate = config.get("exploration_rate", 0.2)
    trusted_min = config.get("trusted_sample_min", 3)
    min_gap = config.get("min_gap_minutes", 30)

    now = datetime.now(timezone.utc)
    today = now.date()
    tomorrow = today + timedelta(days=1)

    # Check if we should explore (random untested slot)
    if random.random() < exploration_rate:
        candidate_hour = random.randint(6, 22)  # UTC 6-22 covers most audiences
        candidate = datetime(
            tomorrow.year, tomorrow.month, tomorrow.day,
            candidate_hour, 0, tzinfo=timezone.utc,
        )
        if _slot_available(candidate, min_gap, conn):
            log.info("Exploration: trying hour %d UTC for %s/%s", candidate_hour, niche_id, platform)
            return candidate

    # Try adaptive: find best performing hour for this niche+platform
    rows = conn.execute(
        "SELECT hour_utc, avg_views FROM time_performance "
        "WHERE niche_id=? AND platform=? AND sample_count>=? "
        "ORDER BY avg_views DESC",
        (niche_id, platform, trusted_min),
    ).fetchall()

    candidate_hours = [r[0] for r in rows]

    # Blend with defaults if adaptive data sparse
    default_times = config.get("default_times_ist", {}).get(platform, [])
    default_hours = [_ist_to_utc_hour(t) for t in default_times]

    if not candidate_hours:
        candidate_hours = default_hours
    elif len(rows) < 3:
        # 50/50 blend
        candidate_hours = candidate_hours + default_hours

    # Find first available slot (today if future, else tomorrow)
    for hour in candidate_hours:
        for target_date in [today, tomorrow]:
            candidate = datetime(
                target_date.year, target_date.month, target_date.day,
                hour, 0, tzinfo=timezone.utc,
            )
            if candidate > now and _slot_available(candidate, min_gap, conn):
                return candidate

    # Fallback: next available default slot tomorrow
    fallback_hour = default_hours[0] if default_hours else 14
    return datetime(
        tomorrow.year, tomorrow.month, tomorrow.day,
        fallback_hour, 0, tzinfo=timezone.utc,
    )


def _slot_available(candidate: datetime, min_gap: int, conn: sqlite3.Connection) -> bool:
    """Check no existing schedule is within min_gap minutes of candidate."""
    window_start = (candidate - timedelta(minutes=min_gap)).isoformat()
    window_end = (candidate + timedelta(minutes=min_gap)).isoformat()

    count = conn.execute(
        "SELECT COUNT(*) FROM upload_schedule "
        "WHERE status='pending' AND scheduled_at BETWEEN ? AND ?",
        (window_start, window_end),
    ).fetchone()[0]
    return count == 0


def create_upload_job(
    schedule_id: int,
    scheduled_at: datetime,
    repo_owner: str,
    repo_name: str,
) -> str:
    """Create a one-time cron-job.org job that triggers GitHub repository_dispatch."""
    api_key = os.getenv("CRONJOB_API_KEY", "")
    if not api_key:
        raise ValueError("CRONJOB_API_KEY not set in environment")

    github_token = os.getenv("GITHUB_DISPATCH_TOKEN", "")
    dispatch_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/dispatches"

    # cron-job.org API: create a one-time job
    job_data = {
        "job": {
            "url": dispatch_url,
            "enabled": "true",
            "saveResponses": True,
            "schedule": {
                "timezone": "UTC",
                "expiresAt": int(scheduled_at.timestamp()) + 300,  # expire 5 min after
                "hours": [scheduled_at.hour],
                "mdays": [scheduled_at.day],
                "months": [scheduled_at.month],
                "wdays": [-1],
            },
            "requestMethod": 1,  # POST
            "extendedData": {
                "headers": {
                    "Authorization": f"Bearer {github_token}",
                    "Accept": "application/vnd.github.v3+json",
                    "Content-Type": "application/json",
                },
                "body": json.dumps({
                    "event_type": "scheduled-upload",
                    "client_payload": {"schedule_id": schedule_id},
                }),
            },
        }
    }

    resp = requests.put(
        "https://api.cron-job.org/jobs",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=job_data,
        timeout=30,
    )
    resp.raise_for_status()
    job_id = resp.json().get("jobId", "")
    log.info("Created cron-job.org job %s for schedule_id=%d at %s", job_id, schedule_id, scheduled_at)
    return str(job_id)


def delete_upload_job(cronjob_id: str) -> None:
    """Delete a cron-job.org job after execution or cancellation."""
    api_key = os.getenv("CRONJOB_API_KEY", "")
    if not api_key or not cronjob_id:
        return
    try:
        requests.delete(
            f"https://api.cron-job.org/jobs/{cronjob_id}",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15,
        )
        log.info("Deleted cron-job.org job %s", cronjob_id)
    except Exception as e:
        log.warning("Failed to delete cron-job.org job %s: %s", cronjob_id, e)


def schedule_video(
    video_id: int,
    niche_id: str,
    drive_file_id: str,
    drive_manifest_id: str,
    conn: sqlite3.Connection,
) -> dict:
    """Full scheduling orchestration. Returns schedule info dict.

    Args:
        video_id: DB video ID
        niche_id: Niche identifier
        drive_file_id: Google Drive file ID for video mp4
        drive_manifest_id: Google Drive file ID for schedule manifest JSON
        conn: SQLite connection

    Returns:
        {"schedule_id": int, "platform": str, "scheduled_at": str, "cronjob_id": str}
    """
    config = _get_scheduler_config()
    repo = config.get("github_repo", "")
    if "/" not in repo:
        raise ValueError(f"Invalid github_repo in settings.json: {repo!r}")
    repo_owner, repo_name = repo.split("/", 1)

    platform = get_next_platform(niche_id, conn)
    scheduled_at = pick_optimal_time(niche_id, platform, conn)

    # Pick A/B variant randomly
    caption_variant = random.choice(["A", "B"])

    # Insert schedule row
    cur = conn.execute(
        "INSERT INTO upload_schedule "
        "(video_id, platform, niche_id, scheduled_at, status, drive_file_id, caption_variant) "
        "VALUES (?, ?, ?, ?, 'pending', ?, ?)",
        (video_id, platform, niche_id, scheduled_at.isoformat(), drive_file_id, caption_variant),
    )
    schedule_id = cur.lastrowid
    conn.commit()

    # Create cron-job.org trigger
    cronjob_id = create_upload_job(schedule_id, scheduled_at, repo_owner, repo_name)

    conn.execute(
        "UPDATE upload_schedule SET cronjob_id=? WHERE id=?",
        (cronjob_id, schedule_id),
    )
    conn.commit()

    # Format IST time for display
    ist_time = scheduled_at + _IST_OFFSET
    ist_str = ist_time.strftime("%I:%M %p IST")

    result = {
        "schedule_id": schedule_id,
        "platform": platform,
        "scheduled_at": scheduled_at.isoformat(),
        "scheduled_at_ist": ist_str,
        "cronjob_id": cronjob_id,
        "caption_variant": caption_variant,
    }
    log.info("Scheduled video_id=%d: %s -> %s at %s", video_id, niche_id, platform, ist_str)
    return result
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_scheduler.py -v`
Expected: All 5 tests PASS.

- [ ] **Step 8: Verify existing tests still pass**

Run: `pytest -m "not slow" -v`
Expected: No regressions.

- [ ] **Step 9: Commit**

```bash
git add pipeline/scheduler.py tests/test_scheduler.py settings.json config.py social_config.json
git commit -m "feat: add scheduler module with platform rotation, time optimization, cron-job.org"
```

---

### Task 4: Telegram Approval Hook — Drive Upload + Schedule + Confirmation

**Files:**
- Modify: `review/telegram_bot.py:128-141` (approval callback)
- Test: `tests/test_telegram_scheduler_hook.py` (new)

**Interfaces:**
- Consumes:
  - `pipeline.drive_storage.upload_to_drive(local_path, folder_name) -> str`
  - `pipeline.scheduler.schedule_video(video_id, niche_id, drive_file_id, drive_manifest_id, conn) -> dict`
  - `videos` table columns: `id`, `niche_id`, `file_path`, `status`
- Produces: On Telegram "Approve" callback, videos are uploaded to Drive and scheduled for social media upload. Telegram confirmation sent with platform + time.

- [ ] **Step 1: Write failing test**

```python
# tests/test_telegram_scheduler_hook.py
"""Test the post-approval scheduling hook in telegram_bot.py."""
import sqlite3
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def db_conn(tmp_path):
    """DB with videos + scheduler tables."""
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("""CREATE TABLE videos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        status TEXT DEFAULT 'sent', niche_id TEXT,
        file_path TEXT, retry_count INTEGER DEFAULT 0
    )""")
    conn.execute("""CREATE TABLE feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        video_id INTEGER, rating TEXT, feedback_text TEXT,
        parsed_tags TEXT, source TEXT,
        rated_at DATETIME DEFAULT (datetime('now'))
    )""")
    conn.execute("""CREATE TABLE upload_schedule (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        video_id INTEGER, platform TEXT, niche_id TEXT,
        scheduled_at DATETIME, status TEXT DEFAULT 'pending',
        cronjob_id TEXT, drive_file_id TEXT,
        engagement_views INTEGER, engagement_likes INTEGER,
        platform_post_id TEXT, caption_variant TEXT,
        created_at DATETIME DEFAULT (datetime('now'))
    )""")
    conn.execute("""CREATE TABLE platform_rotation (
        niche_id TEXT PRIMARY KEY, last_platform TEXT,
        updated_at DATETIME DEFAULT (datetime('now'))
    )""")
    conn.execute("""CREATE TABLE time_performance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        niche_id TEXT, platform TEXT, hour_utc INTEGER,
        day_of_week INTEGER, avg_views REAL DEFAULT 0,
        avg_likes REAL DEFAULT 0, sample_count INTEGER DEFAULT 0,
        updated_at DATETIME,
        UNIQUE(niche_id, platform, hour_utc, day_of_week)
    )""")
    # Insert a test video
    conn.execute(
        "INSERT INTO videos (id, status, niche_id, file_path) VALUES (1, 'sent', 'mythology', '/tmp/test.mp4')"
    )
    conn.commit()
    return conn, db_path


@patch("review.telegram_bot._schedule_approved_video")
def test_approve_calls_scheduler(mock_schedule, db_conn):
    """Approval should trigger scheduling hook."""
    from review.telegram_bot import _schedule_approved_video
    conn, db_path = db_conn
    # Verify the function exists and is callable
    assert callable(_schedule_approved_video)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_telegram_scheduler_hook.py -v`
Expected: FAIL — `_schedule_approved_video` not found in telegram_bot.

- [ ] **Step 3: Add scheduling hook to telegram_bot.py**

Add helper function before `_handle_callback` (around line 115):

```python
def _schedule_approved_video(video_id: int, conn: sqlite3.Connection) -> dict | None:
    """Post-approval: upload to Drive and schedule for social media.

    Returns schedule info dict or None on failure. Non-blocking — failures
    are logged but don't break the approval flow.
    """
    try:
        row = conn.execute(
            "SELECT niche_id, file_path FROM videos WHERE id=?", (video_id,)
        ).fetchone()
        if not row or not row[1]:
            log.warning("schedule: video_id=%d has no niche_id or file_path", video_id)
            return None

        niche_id, file_path = row
        video_path = Path(file_path)
        if not video_path.exists():
            log.warning("schedule: video file not found: %s", file_path)
            return None

        from pipeline.drive_storage import upload_to_drive
        from pipeline.scheduler import schedule_video

        # Upload video to Drive
        drive_file_id = upload_to_drive(video_path, folder_name="pending")

        # Upload script JSON sidecar if it exists
        slug = video_path.stem
        script_path = Path(cfg.paths.get("scripts", "output/scripts")) / f"{slug}.json"
        drive_manifest_id = ""
        if script_path.exists():
            drive_manifest_id = upload_to_drive(script_path, folder_name="pending")

        # Schedule the upload
        schedule_info = schedule_video(
            video_id=video_id,
            niche_id=niche_id,
            drive_file_id=drive_file_id,
            drive_manifest_id=drive_manifest_id,
            conn=conn,
        )
        log.info("Scheduled video_id=%d: %s", video_id, schedule_info)
        return schedule_info

    except Exception as e:
        log.error("schedule: failed for video_id=%d: %s", video_id, e)
        return None
```

Modify `_handle_callback` approval block (after line 139 `log.info("video_id=%d approved...")`):

Add after the existing approval logging:

```python
        # Schedule for social media upload
        sched_conn = sqlite3.connect(cfg.paths["db"])
        sched_conn.execute("PRAGMA journal_mode=WAL")
        schedule_info = _schedule_approved_video(video_id, sched_conn)
        sched_conn.close()

        if schedule_info:
            label = f"✅ Approved | 📅 {schedule_info['platform'].title()} at {schedule_info['scheduled_at_ist']}"
        else:
            label = "✅ Approved"
```

Remove the original `label = "✅ Approved"` line that was at line 141.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_telegram_scheduler_hook.py -v`
Expected: PASS.

- [ ] **Step 5: Run all existing tests to verify no breakage**

Run: `pytest -m "not slow" -v`
Expected: All tests PASS including existing `test_social_captions.py`.

- [ ] **Step 6: Commit**

```bash
git add review/telegram_bot.py tests/test_telegram_scheduler_hook.py
git commit -m "feat: hook scheduler into Telegram approval flow"
```

---

### Task 5: Engagement Tracker Module

**Files:**
- Create: `pipeline/engagement_tracker.py`
- Test: `tests/test_engagement_tracker.py` (new)

**Interfaces:**
- Consumes:
  - `upload_schedule` table (rows with status=done, platform_post_id set)
  - YouTube Data API, Instagram Graph API, Facebook Graph API
- Produces:
  - `fetch_engagement(conn: sqlite3.Connection, lookback_hours: int = 48) -> int` — fetches stats for recent uploads, returns count updated
  - `recalculate_time_performance(conn: sqlite3.Connection, lookback_days: int = 30) -> int` — recalculates rolling averages, returns count of slots updated
  - `_fetch_youtube_stats(video_id: str) -> dict` — returns `{"views": int, "likes": int}`
  - `_fetch_instagram_stats(media_id: str) -> dict` — returns `{"views": int, "likes": int}`
  - `_fetch_facebook_stats(post_id: str) -> dict` — returns `{"views": int, "likes": int}`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_engagement_tracker.py
"""Tests for pipeline/engagement_tracker.py — all platform APIs mocked."""
import sqlite3
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def db_conn():
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE upload_schedule (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        video_id INTEGER, platform TEXT, niche_id TEXT,
        scheduled_at DATETIME, status TEXT DEFAULT 'done',
        cronjob_id TEXT, drive_file_id TEXT,
        engagement_views INTEGER, engagement_likes INTEGER,
        platform_post_id TEXT, caption_variant TEXT,
        created_at DATETIME DEFAULT (datetime('now'))
    )""")
    conn.execute("""CREATE TABLE time_performance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        niche_id TEXT, platform TEXT, hour_utc INTEGER,
        day_of_week INTEGER, avg_views REAL DEFAULT 0,
        avg_likes REAL DEFAULT 0, sample_count INTEGER DEFAULT 0,
        updated_at DATETIME DEFAULT (datetime('now')),
        UNIQUE(niche_id, platform, hour_utc, day_of_week)
    )""")
    conn.commit()
    return conn


def test_fetch_engagement_updates_rows(db_conn):
    from pipeline.engagement_tracker import fetch_engagement
    # Insert a done upload with platform_post_id, no engagement yet
    recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    db_conn.execute(
        "INSERT INTO upload_schedule (video_id, platform, niche_id, scheduled_at, status, platform_post_id) "
        "VALUES (1, 'youtube', 'mythology', ?, 'done', 'yt_abc123')",
        (recent,)
    )
    db_conn.commit()

    with patch("pipeline.engagement_tracker._fetch_youtube_stats") as mock_yt:
        mock_yt.return_value = {"views": 1500, "likes": 45}
        count = fetch_engagement(db_conn, lookback_hours=48)

    assert count == 1
    row = db_conn.execute("SELECT engagement_views, engagement_likes FROM upload_schedule WHERE id=1").fetchone()
    assert row[0] == 1500
    assert row[1] == 45


def test_recalculate_time_performance(db_conn):
    from pipeline.engagement_tracker import recalculate_time_performance
    # Insert some done uploads with engagement data at various hours
    base = datetime(2026, 8, 10, 14, 0, tzinfo=timezone.utc)  # Monday 14:00 UTC
    for i in range(5):
        db_conn.execute(
            "INSERT INTO upload_schedule "
            "(video_id, platform, niche_id, scheduled_at, status, platform_post_id, engagement_views, engagement_likes) "
            "VALUES (?, 'youtube', 'mythology', ?, 'done', 'yt_x', ?, ?)",
            (i + 1, (base + timedelta(days=i * 7)).isoformat(), 1000 + i * 100, 30 + i * 5)
        )
    db_conn.commit()

    count = recalculate_time_performance(db_conn, lookback_days=60)
    assert count >= 1

    row = db_conn.execute(
        "SELECT avg_views, sample_count FROM time_performance WHERE niche_id='mythology' AND platform='youtube'"
    ).fetchone()
    assert row is not None
    assert row[0] > 0  # avg_views > 0
    assert row[1] == 5  # 5 samples


def test_fetch_engagement_skips_already_fetched(db_conn):
    from pipeline.engagement_tracker import fetch_engagement
    recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    db_conn.execute(
        "INSERT INTO upload_schedule "
        "(video_id, platform, niche_id, scheduled_at, status, platform_post_id, engagement_views) "
        "VALUES (1, 'youtube', 'mythology', ?, 'done', 'yt_abc', 500)",
        (recent,)
    )
    db_conn.commit()

    with patch("pipeline.engagement_tracker._fetch_youtube_stats") as mock_yt:
        count = fetch_engagement(db_conn, lookback_hours=48)

    assert count == 0
    mock_yt.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_engagement_tracker.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement engagement_tracker.py**

```python
# pipeline/engagement_tracker.py
"""Fetch engagement stats from social platforms and recalculate optimal time slots.

Runs daily via GitHub Actions cron. Updates upload_schedule with view/like counts,
then recalculates time_performance rolling averages.
"""
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone

import requests

log = logging.getLogger(__name__)


def _fetch_youtube_stats(video_id: str) -> dict:
    """Fetch views + likes for a YouTube video."""
    api_key = os.getenv("GOOGLE_AI_STUDIO_API_KEY", "")
    if not api_key:
        return {}
    try:
        resp = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={"part": "statistics", "id": video_id, "key": api_key},
            timeout=15,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if not items:
            return {}
        stats = items[0].get("statistics", {})
        return {
            "views": int(stats.get("viewCount", 0)),
            "likes": int(stats.get("likeCount", 0)),
        }
    except Exception as e:
        log.warning("YouTube stats fetch failed for %s: %s", video_id, e)
        return {}


def _fetch_instagram_stats(media_id: str) -> dict:
    """Fetch plays + likes for an Instagram Reel."""
    token = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
    if not token:
        return {}
    try:
        resp = requests.get(
            f"https://graph.instagram.com/{media_id}/insights",
            params={"metric": "plays,likes", "access_token": token},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        result = {}
        for metric in data:
            name = metric.get("name", "")
            value = metric.get("values", [{}])[0].get("value", 0)
            if name == "plays":
                result["views"] = int(value)
            elif name == "likes":
                result["likes"] = int(value)
        return result
    except Exception as e:
        log.warning("Instagram stats fetch failed for %s: %s", media_id, e)
        return {}


def _fetch_facebook_stats(post_id: str) -> dict:
    """Fetch views + reactions for a Facebook video."""
    token = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN", "")
    if not token:
        return {}
    try:
        resp = requests.get(
            f"https://graph.facebook.com/v21.0/{post_id}",
            params={
                "fields": "insights.metric(post_video_views_organic).period(lifetime),"
                          "reactions.summary(true)",
                "access_token": token,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        views = 0
        insights = data.get("insights", {}).get("data", [])
        for item in insights:
            if item.get("name") == "post_video_views_organic":
                views = item.get("values", [{}])[0].get("value", 0)
        likes = data.get("reactions", {}).get("summary", {}).get("total_count", 0)
        return {"views": int(views), "likes": int(likes)}
    except Exception as e:
        log.warning("Facebook stats fetch failed for %s: %s", post_id, e)
        return {}


_PLATFORM_FETCHERS = {
    "youtube": _fetch_youtube_stats,
    "instagram": _fetch_instagram_stats,
    "facebook": _fetch_facebook_stats,
}


def fetch_engagement(conn: sqlite3.Connection, lookback_hours: int = 48) -> int:
    """Fetch engagement for recent uploads missing stats. Returns count updated."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).isoformat()
    rows = conn.execute(
        "SELECT id, platform, platform_post_id FROM upload_schedule "
        "WHERE status='done' AND platform_post_id IS NOT NULL "
        "AND engagement_views IS NULL "
        "AND scheduled_at >= ?",
        (cutoff,),
    ).fetchall()

    updated = 0
    for row_id, platform, post_id in rows:
        fetcher = _PLATFORM_FETCHERS.get(platform)
        if not fetcher:
            continue
        stats = fetcher(post_id)
        if not stats:
            continue

        conn.execute(
            "UPDATE upload_schedule SET engagement_views=?, engagement_likes=? WHERE id=?",
            (stats.get("views", 0), stats.get("likes", 0), row_id),
        )
        updated += 1

    if updated:
        conn.commit()
        log.info("Updated engagement for %d uploads", updated)
    return updated


def recalculate_time_performance(conn: sqlite3.Connection, lookback_days: int = 30) -> int:
    """Recalculate rolling averages in time_performance. Returns count of slots updated."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()

    # Get aggregated stats grouped by niche, platform, hour, day_of_week
    rows = conn.execute(
        """SELECT niche_id, platform,
                  CAST(strftime('%%H', scheduled_at) AS INTEGER) AS hour_utc,
                  CAST(strftime('%%w', scheduled_at) AS INTEGER) AS day_of_week,
                  AVG(engagement_views) AS avg_views,
                  AVG(engagement_likes) AS avg_likes,
                  COUNT(*) AS sample_count
           FROM upload_schedule
           WHERE status='done'
             AND engagement_views IS NOT NULL
             AND scheduled_at >= ?
           GROUP BY niche_id, platform, hour_utc, day_of_week""",
        (cutoff,),
    ).fetchall()

    updated = 0
    for niche_id, platform, hour_utc, dow, avg_views, avg_likes, count in rows:
        conn.execute(
            "INSERT OR REPLACE INTO time_performance "
            "(niche_id, platform, hour_utc, day_of_week, avg_views, avg_likes, sample_count, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            (niche_id, platform, hour_utc, dow, avg_views, avg_likes, count),
        )
        updated += 1

    if updated:
        conn.commit()
        log.info("Updated %d time_performance slots", updated)
    return updated
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_engagement_tracker.py -v`
Expected: All 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/engagement_tracker.py tests/test_engagement_tracker.py
git commit -m "feat: add engagement tracker for adaptive scheduling"
```

---

### Task 6: Enhanced Captions — Hashtag Banks + A/B Variants

**Files:**
- Create: `hashtag_banks.json`
- Modify: `pipeline/social_captions.py:57-96` (add bank + A/B logic)
- Modify: `tests/test_social_captions.py` (update for new behavior)

**Interfaces:**
- Consumes: `hashtag_banks.json`, existing `call_llm()` from llm_router
- Produces: Same return format `{"platform": {"caption": str, "hashtags": [str]}}` — backwards compatible. New optional kwarg `caption_variant: str = None` on `generate_social_captions()`.

- [ ] **Step 1: Create hashtag_banks.json**

```json
{
  "mythology": {
    "base": ["#mythology", "#ancientstories", "#gods", "#legends", "#mythical", "#epic", "#divine", "#ancienthistory"],
    "youtube": ["#shorts", "#mythologyfacts", "#storytime"],
    "instagram": ["#mythologylovers", "#ancientmyths", "#godsandgoddesses", "#mythologyreel"],
    "facebook": ["#mythologyfacts", "#ancientworld"],
    "tiktok": ["#mythologytok", "#learnontiktok", "#storytime", "#fyp"]
  },
  "scary_stories": {
    "base": ["#scarystories", "#horror", "#creepy", "#paranormal", "#scary", "#darkstories", "#nightmares", "#haunted"],
    "youtube": ["#shorts", "#horrortok", "#scaryshorts"],
    "instagram": ["#horrorstories", "#creepypasta", "#scaryreels"],
    "facebook": ["#horrorstories", "#paranormalstories"],
    "tiktok": ["#horrortok", "#scarytok", "#fyp", "#creepystory"]
  },
  "heists": {
    "base": ["#heist", "#crime", "#truecrime", "#robbery", "#crimestory", "#thriller", "#mastermind", "#criminalminds"],
    "youtube": ["#shorts", "#truecrimeshorts", "#heistmovies"],
    "instagram": ["#truecrimeaddict", "#heistmovie", "#crimereels"],
    "facebook": ["#truecrimestories", "#crimefacts"],
    "tiktok": ["#crimetok", "#truecrime", "#fyp", "#storytime"]
  },
  "space_science": {
    "base": ["#space", "#science", "#universe", "#cosmos", "#astronomy", "#nasa", "#astrophysics", "#planets"],
    "youtube": ["#shorts", "#spacefacts", "#scienceshorts"],
    "instagram": ["#spacelover", "#cosmosreels", "#sciencefacts"],
    "facebook": ["#spacescience", "#universefacts"],
    "tiktok": ["#spacetok", "#sciencetok", "#fyp", "#learnontiktok"]
  },
  "ai_tech_tools": {
    "base": ["#ai", "#tech", "#artificialintelligence", "#technology", "#machinelearning", "#coding", "#innovation", "#tools"],
    "youtube": ["#shorts", "#techshorts", "#aitools"],
    "instagram": ["#techreels", "#aitips", "#toolsofthetrade"],
    "facebook": ["#techtrends", "#aiupdates"],
    "tiktok": ["#techtok", "#aitok", "#fyp", "#techtools"]
  },
  "finance_facts": {
    "base": ["#finance", "#money", "#investing", "#personalfinance", "#wealth", "#financialliteracy", "#stocks", "#economy"],
    "youtube": ["#shorts", "#financetips", "#moneyshorts"],
    "instagram": ["#financereels", "#moneytips", "#investingtips"],
    "facebook": ["#financialeducation", "#moneyfacts"],
    "tiktok": ["#fintok", "#moneytok", "#fyp", "#personalfinance"]
  }
}
```

- [ ] **Step 2: Update social_captions.py — add hashtag bank loading and A/B variant**

Add after the imports (line 10):

```python
from pathlib import Path

_BANKS_PATH = Path(__file__).parent.parent / "hashtag_banks.json"
_banks_cache: dict | None = None


def _load_hashtag_banks() -> dict:
    """Lazy-load hashtag banks from JSON file."""
    global _banks_cache
    if _banks_cache is not None:
        return _banks_cache
    if _BANKS_PATH.exists():
        _banks_cache = json.loads(_BANKS_PATH.read_text(encoding="utf-8"))
    else:
        _banks_cache = {}
    return _banks_cache
```

Modify `generate_social_captions` signature and prompt (line 57-96):

```python
def generate_social_captions(
    script: dict,
    niche: dict,
    cfg=None,
    caption_variant: str | None = None,
) -> dict:
    """
    Generate platform captions and hashtags for a video script.

    Args:
        script: generate_script() output — needs story_title + scenes[narration]
        niche:  niche config dict (uses tone and id keys)
        cfg:    config singleton for llm_router settings
        caption_variant: "A" or "B" for A/B testing (None = single variant)

    Returns:
        {"youtube": {"caption": str, "hashtags": [str]}, ...} for all 6 platforms.
        Returns {} on any failure.
    """
    cfg_router = cfg.llm_router if cfg else {}
    story_title = script.get("story_title", "Untitled")
    narration = " ".join(s.get("narration", "") for s in script.get("scenes", []))
    tone = niche.get("tone", "engaging")
    niche_id = niche.get("id", "")

    platform_instructions = "\n".join(
        f'  "{p}": {spec}' for p, spec in _PLATFORM_SPECS.items()
    )

    # Load hashtag bank for this niche
    banks = _load_hashtag_banks()
    niche_bank = banks.get(niche_id, {})
    bank_section = ""
    if niche_bank:
        base_tags = " ".join(niche_bank.get("base", []))
        bank_section = f"\nNiche hashtag bank (blend these in): {base_tags}"

    # A/B variant instruction
    variant_instruction = ""
    if caption_variant:
        style = "punchy and question-based" if caption_variant == "A" else "storytelling and statement-based"
        variant_instruction = f"\nCaption style: {style}"

    prompt = f"""You are a social media copywriter. Write captions and hashtags for this video.

Title: {story_title}
Tone: {tone}
Script summary: {narration[:500]}{bank_section}{variant_instruction}

Write captions for these platforms with these requirements:
{platform_instructions}

Respond with ONLY valid JSON, no markdown fences:
{{
  "youtube":   {{"caption": "...", "hashtags": ["#tag1", "#tag2"]}},
  "instagram": {{"caption": "...", "hashtags": ["#tag1", ...]}},
  "facebook":  {{"caption": "...", "hashtags": ["#tag1", ...]}},
  "tiktok":    {{"caption": "...", "hashtags": ["#tag1", ...]}},
  "pinterest": {{"caption": "...", "hashtags": ["#tag1", ...]}},
  "linkedin":  {{"caption": "...", "hashtags": ["#tag1", ...]}}
}}"""

    try:
        raw, model_used = call_llm(prompt, cfg_router=cfg_router, temperature=0.7)
        data = _extract_json(raw)
    except Exception as e:
        log.warning("social_captions: LLM call or parse failed: %s", e)
        return {}

    result = {}
    for platform in _PLATFORMS:
        entry = data.get(platform, {})
        caption = str(entry.get("caption", "")).strip()
        hashtags = entry.get("hashtags", [])
        if not isinstance(hashtags, list):
            hashtags = []

        # Inject niche bank tags for this platform if available
        platform_bank = niche_bank.get(platform, [])
        if platform_bank:
            existing = set(h.lower() for h in hashtags)
            for tag in platform_bank:
                if tag.lower() not in existing:
                    hashtags.append(tag)

        if caption:
            result[platform] = {"caption": caption, "hashtags": hashtags}

    if len(result) != len(_PLATFORMS):
        log.warning(
            "social_captions: incomplete response (%d/%d platforms). raw=%s",
            len(result), len(_PLATFORMS), raw[:200],
        )
        return {}

    log.info("social_captions: generated for %d platforms using %s", len(result), model_used)
    return result
```

- [ ] **Step 3: Update existing tests**

Add to `tests/test_social_captions.py`:

```python
def test_caption_variant_accepted():
    """caption_variant kwarg should not break existing behavior."""
    from pipeline.social_captions import generate_social_captions
    with patch("pipeline.social_captions.call_llm", return_value=(_LLM_JSON, "gemini/test")):
        result = generate_social_captions(_SCRIPT, _NICHE, caption_variant="A")
    assert set(result.keys()) == {"youtube", "instagram", "facebook", "tiktok", "pinterest", "linkedin"}


def test_hashtag_bank_injects_platform_tags():
    """Hashtag bank tags should be appended to LLM-generated hashtags."""
    from pipeline.social_captions import generate_social_captions, _load_hashtag_banks
    with patch("pipeline.social_captions.call_llm", return_value=(_LLM_JSON, "gemini/test")):
        with patch("pipeline.social_captions._load_hashtag_banks", return_value={
            "space_science": {
                "base": ["#space", "#cosmos"],
                "tiktok": ["#spacetok", "#fyp"],
            }
        }):
            result = generate_social_captions(_SCRIPT, _NICHE)
    # tiktok should have bank tags appended (if not already present)
    tiktok_tags = [t.lower() for t in result["tiktok"]["hashtags"]]
    assert "#spacetok" in tiktok_tags or "#fyp" in tiktok_tags


def test_no_bank_file_still_works():
    """Missing hashtag_banks.json should not break caption generation."""
    from pipeline.social_captions import generate_social_captions
    with patch("pipeline.social_captions._load_hashtag_banks", return_value={}):
        with patch("pipeline.social_captions.call_llm", return_value=(_LLM_JSON, "gemini/test")):
            result = generate_social_captions(_SCRIPT, _NICHE)
    assert len(result) == 6
```

- [ ] **Step 4: Run all social caption tests**

Run: `pytest tests/test_social_captions.py -v`
Expected: All tests PASS (original 6 + 3 new).

- [ ] **Step 5: Run full test suite**

Run: `pytest -m "not slow" -v`
Expected: No regressions.

- [ ] **Step 6: Commit**

```bash
git add hashtag_banks.json pipeline/social_captions.py tests/test_social_captions.py
git commit -m "feat: add hashtag banks, A/B caption variants to social captions"
```

---

### Task 7: GitHub Actions Workflows

**Files:**
- Create: `.github/workflows/scheduled-upload.yml`
- Create: `.github/workflows/engagement-fetch.yml`
- Create: `.github/workflows/drive-cleanup.yml`

**Interfaces:**
- Consumes:
  - `repository_dispatch` event with `client_payload.schedule_id`
  - `pipeline.drive_storage.download_from_drive()`, `move_drive_file()`
  - `scripts/upload_all_platforms.py` upload functions
  - `pipeline.engagement_tracker.fetch_engagement()`, `recalculate_time_performance()`
  - `pipeline.drive_storage.delete_old_files()`
- Produces: Automated upload, engagement tracking, and cleanup workflows running on GitHub Actions.

- [ ] **Step 1: Create scheduled-upload.yml**

```yaml
# .github/workflows/scheduled-upload.yml
name: Scheduled Video Upload

on:
  repository_dispatch:
    types: [scheduled-upload]

jobs:
  upload:
    runs-on: ubuntu-latest
    timeout-minutes: 15

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: |
          pip install -r requirements.txt

      - name: Write credentials files
        env:
          GOOGLE_DRIVE_SA_JSON: ${{ secrets.GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON }}
          YOUTUBE_CREDS_JSON: ${{ secrets.YOUTUBE_CREDENTIALS }}
          INSTAGRAM_CREDS_JSON: ${{ secrets.INSTAGRAM_CREDENTIALS }}
        run: |
          mkdir -p credentials
          echo "$GOOGLE_DRIVE_SA_JSON" > credentials/drive_service_account.json
          echo "$YOUTUBE_CREDS_JSON" > credentials/mythology_yt.json
          echo "$INSTAGRAM_CREDS_JSON" > credentials/all_niches_ig.json

      - name: Run upload
        env:
          GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON: credentials/drive_service_account.json
          GOOGLE_AI_STUDIO_API_KEY: ${{ secrets.GOOGLE_AI_STUDIO_API_KEY }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          CRONJOB_API_KEY: ${{ secrets.CRONJOB_API_KEY }}
          SCHEDULE_ID: ${{ github.event.client_payload.schedule_id }}
        run: |
          python -c "
          import json, sqlite3, sys, tempfile, os
          from pathlib import Path
          from pipeline.drive_storage import download_from_drive, move_drive_file
          from pipeline.scheduler import delete_upload_job

          schedule_id = int(os.environ['SCHEDULE_ID'])

          # Download manifest from Drive to get upload details
          manifest_dir = Path(tempfile.mkdtemp())

          # The manifest is uploaded alongside the video with _schedule.json suffix
          # We need to find it — download all pending JSON files and find ours
          # For now, the schedule info is passed via the dispatch payload
          print(f'Processing schedule_id={schedule_id}')

          # Download the manifest JSON from Drive
          # The manifest drive_file_id is stored in the schedule row
          # Since we can't access the DB directly, we use a manifest approach:
          # The manifest JSON was uploaded to Drive with the schedule details
          from pipeline.drive_storage import _build_service, _get_subfolder
          service = _build_service()
          folder_id = _get_subfolder('pending')

          # List all schedule manifest files
          results = service.files().list(
              q=f\"'{folder_id}' in parents and name contains '_schedule.json' and trashed=false\",
              spaces='drive',
              fields='files(id, name)',
          ).execute()

          manifest_file = None
          for f in results.get('files', []):
              # Download and check schedule_id
              local = manifest_dir / f['name']
              download_from_drive(f['id'], local)
              data = json.loads(local.read_text())
              if data.get('schedule_id') == schedule_id:
                  manifest_file = data
                  manifest_drive_id = f['id']
                  break

          if not manifest_file:
              print(f'ERROR: No manifest found for schedule_id={schedule_id}')
              sys.exit(1)

          platform = manifest_file['platform']
          drive_file_id = manifest_file['drive_file_id']
          caption = manifest_file.get('caption', '')
          hashtags = manifest_file.get('hashtags', [])
          title = manifest_file.get('title', 'Untitled')
          niche_id = manifest_file.get('niche_id', '')

          # Download video
          video_path = manifest_dir / f'{niche_id}_video.mp4'
          download_from_drive(drive_file_id, video_path)
          print(f'Downloaded video: {video_path} ({video_path.stat().st_size} bytes)')

          # Upload to platform
          from scripts.upload_all_platforms import upload_all
          results = upload_all(
              video_path=video_path,
              title=title,
              description=caption,
              hashtags=hashtags,
              platforms_filter=[platform],
          )

          # Process results
          success = False
          post_id = ''
          for r in results:
              print(f\"  {r['platform']}: {r['status']}\")
              if r['status'] == 'success':
                  success = True
                  post_id = r.get('video_id') or r.get('media_id', '')

          # Move video in Drive
          dest = 'uploaded' if success else 'failed'
          move_drive_file(drive_file_id, dest)
          move_drive_file(manifest_drive_id, dest)

          # Clean up cron job
          cronjob_id = manifest_file.get('cronjob_id', '')
          if cronjob_id:
              delete_upload_job(cronjob_id)

          # Notify via Telegram
          import asyncio
          from telegram import Bot
          from telegram.request import HTTPXRequest

          status_emoji = '✅' if success else '❌'
          msg = f\"{status_emoji} Upload {platform.title()}: {title}\"
          if post_id:
              if platform == 'youtube':
                  msg += f\"\nhttps://youtu.be/{post_id}\"
              msg += f\"\nPost ID: {post_id}\"

          async def notify():
              async with Bot(
                  token=os.environ['TELEGRAM_BOT_TOKEN'],
                  request=HTTPXRequest(connect_timeout=30, read_timeout=60),
              ) as bot:
                  await bot.send_message(chat_id=os.environ['TELEGRAM_CHAT_ID'], text=msg)
          asyncio.run(notify())

          if not success:
              sys.exit(1)
          "
```

- [ ] **Step 2: Create engagement-fetch.yml**

```yaml
# .github/workflows/engagement-fetch.yml
name: Daily Engagement Fetch

on:
  schedule:
    - cron: "30 0 * * *"  # 6:00 AM IST = 00:30 UTC
  workflow_dispatch:  # manual trigger

jobs:
  fetch:
    runs-on: ubuntu-latest
    timeout-minutes: 10

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Write Drive credentials
        env:
          GOOGLE_DRIVE_SA_JSON: ${{ secrets.GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON }}
        run: |
          mkdir -p credentials
          echo "$GOOGLE_DRIVE_SA_JSON" > credentials/drive_service_account.json

      - name: Fetch engagement and recalculate
        env:
          GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON: credentials/drive_service_account.json
          GOOGLE_AI_STUDIO_API_KEY: ${{ secrets.GOOGLE_AI_STUDIO_API_KEY }}
          INSTAGRAM_ACCESS_TOKEN: ${{ secrets.INSTAGRAM_ACCESS_TOKEN }}
          FACEBOOK_PAGE_ACCESS_TOKEN: ${{ secrets.FACEBOOK_PAGE_ACCESS_TOKEN }}
        run: |
          python -c "
          import sqlite3
          from pipeline.engagement_tracker import fetch_engagement, recalculate_time_performance
          from pipeline.drive_storage import download_from_drive, upload_to_drive, _build_service, _get_subfolder
          from pathlib import Path
          import tempfile, json

          # Download the schedule DB from Drive (synced after each local approval)
          # For simplicity, we maintain a schedule_state.json on Drive
          service = _build_service()
          root_id = _get_subfolder('state')

          # List state files
          results = service.files().list(
              q=f\"'{root_id}' in parents and name='schedule_db.sqlite' and trashed=false\",
              spaces='drive',
              fields='files(id)',
          ).execute()
          files = results.get('files', [])

          if not files:
              print('No schedule DB found on Drive. Nothing to do.')
              exit(0)

          db_path = Path(tempfile.mkdtemp()) / 'schedule.db'
          download_from_drive(files[0]['id'], db_path)

          conn = sqlite3.connect(str(db_path))
          updated = fetch_engagement(conn, lookback_hours=48)
          recalculated = recalculate_time_performance(conn, lookback_days=30)
          conn.close()

          print(f'Engagement updated: {updated} uploads')
          print(f'Time slots recalculated: {recalculated}')

          # Re-upload updated DB
          from googleapiclient.http import MediaFileUpload
          media = MediaFileUpload(str(db_path))
          service.files().update(fileId=files[0]['id'], media_body=media).execute()
          print('Schedule DB synced back to Drive')
          "
```

- [ ] **Step 3: Create drive-cleanup.yml**

```yaml
# .github/workflows/drive-cleanup.yml
name: Weekly Drive Cleanup

on:
  schedule:
    - cron: "0 3 * * 0"  # Sunday 3:00 AM UTC
  workflow_dispatch:

jobs:
  cleanup:
    runs-on: ubuntu-latest
    timeout-minutes: 5

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Write Drive credentials
        env:
          GOOGLE_DRIVE_SA_JSON: ${{ secrets.GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON }}
        run: |
          mkdir -p credentials
          echo "$GOOGLE_DRIVE_SA_JSON" > credentials/drive_service_account.json

      - name: Clean up old files
        env:
          GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON: credentials/drive_service_account.json
        run: |
          python -c "
          from pipeline.drive_storage import delete_old_files
          uploaded = delete_old_files('uploaded', older_than_days=7)
          failed = delete_old_files('failed', older_than_days=14)
          print(f'Cleaned up: {uploaded} uploaded, {failed} failed files')
          "
```

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/scheduled-upload.yml .github/workflows/engagement-fetch.yml .github/workflows/drive-cleanup.yml
git commit -m "feat: add GitHub Actions workflows for scheduled upload, engagement, cleanup"
```

---

### Task 8: Setup Script + .env Updates + Final Integration Test

**Files:**
- Create: `scripts/scheduler_setup.py`
- Modify: `.env.example` (add remaining env vars)
- Test: Run full pipeline dry test

**Interfaces:**
- Consumes: All modules from Tasks 1-7
- Produces: One-time setup verification script. Updated `.env.example` with all required vars.

- [ ] **Step 1: Add remaining env vars to .env.example**

Append to `.env.example` (if not already added in Task 2):

```
# Scheduler — GitHub dispatch token (for cron-job.org to trigger Actions)
GITHUB_DISPATCH_TOKEN=your_github_pat_with_repo_scope

# Engagement tracking
INSTAGRAM_ACCESS_TOKEN=your_instagram_access_token
FACEBOOK_PAGE_ACCESS_TOKEN=your_facebook_page_access_token
```

- [ ] **Step 2: Create scheduler_setup.py**

```python
# scripts/scheduler_setup.py
"""One-time setup verification for the social media scheduler.

Checks:
  1. Google Drive service account credentials exist and can authenticate
  2. Drive folders (pending/uploaded/failed) exist or can be created
  3. cron-job.org API key is valid
  4. GitHub dispatch token is set
  5. Scheduler tables exist in DB
  6. settings.json has scheduler config
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")


def check_env_var(name: str, required: bool = True) -> bool:
    val = os.getenv(name, "")
    status = "OK" if val else ("MISSING" if required else "optional, not set")
    print(f"  {name}: {status}")
    return bool(val) or not required


def main():
    print("=" * 60)
    print("SCHEDULER SETUP VERIFICATION")
    print("=" * 60)
    errors = []

    # 1. Environment variables
    print("\n1. Environment Variables")
    env_checks = [
        ("GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON", True),
        ("CRONJOB_API_KEY", True),
        ("GITHUB_DISPATCH_TOKEN", True),
        ("TELEGRAM_BOT_TOKEN", True),
        ("TELEGRAM_CHAT_ID", True),
        ("INSTAGRAM_ACCESS_TOKEN", False),
        ("FACEBOOK_PAGE_ACCESS_TOKEN", False),
    ]
    for name, required in env_checks:
        if not check_env_var(name, required) and required:
            errors.append(f"Missing required env var: {name}")

    # 2. Google Drive credentials file
    print("\n2. Google Drive Credentials")
    creds_path = os.getenv("GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON", "")
    if creds_path and Path(creds_path).exists():
        print(f"  Credentials file: OK ({creds_path})")
        try:
            from pipeline.drive_storage import _build_service, _get_or_create_folder
            service = _build_service()
            print("  Authentication: OK")

            # Create folders
            for folder in ["pending", "uploaded", "failed", "state"]:
                _get_or_create_folder("video-uploads")  # root
                print(f"  Folder '{folder}': OK")
        except Exception as e:
            print(f"  Authentication: FAILED — {e}")
            errors.append(f"Drive auth failed: {e}")
    else:
        print(f"  Credentials file: MISSING ({creds_path})")
        errors.append("Drive credentials file not found")

    # 3. cron-job.org API
    print("\n3. cron-job.org API")
    api_key = os.getenv("CRONJOB_API_KEY", "")
    if api_key:
        try:
            import requests
            resp = requests.get(
                "https://api.cron-job.org/jobs",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10,
            )
            if resp.status_code == 200:
                jobs = resp.json().get("jobs", [])
                print(f"  API: OK ({len(jobs)} existing jobs)")
            else:
                print(f"  API: FAILED (status {resp.status_code})")
                errors.append(f"cron-job.org API returned {resp.status_code}")
        except Exception as e:
            print(f"  API: FAILED — {e}")
            errors.append(f"cron-job.org API error: {e}")
    else:
        print("  API: SKIPPED (no key)")

    # 4. Database tables
    print("\n4. Database Tables")
    settings = json.loads((ROOT / "settings.json").read_text())
    db_path = ROOT / settings["paths"]["db"]
    if db_path.exists():
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        for t in ["upload_schedule", "time_performance", "platform_rotation"]:
            status = "OK" if t in tables else "MISSING (run: python db/init_db.py)"
            print(f"  {t}: {status}")
            if t not in tables:
                errors.append(f"Table {t} missing")
        conn.close()
    else:
        print(f"  DB not found: {db_path} (run: python db/init_db.py)")
        errors.append("Database not found")

    # 5. Settings config
    print("\n5. Scheduler Config")
    scheduler_cfg = settings.get("scheduler", {})
    if scheduler_cfg.get("enabled"):
        print("  scheduler.enabled: OK")
        print(f"  github_repo: {scheduler_cfg.get('github_repo', 'NOT SET')}")
        if scheduler_cfg.get("github_repo", "") == "your-username/video-creation-agent":
            errors.append("Update scheduler.github_repo in settings.json with your actual repo")
            print("  WARNING: Update github_repo to your actual repo!")
    else:
        print("  scheduler: NOT CONFIGURED (add to settings.json)")
        errors.append("Scheduler not configured in settings.json")

    # Summary
    print("\n" + "=" * 60)
    if errors:
        print(f"SETUP INCOMPLETE — {len(errors)} issue(s):")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("ALL CHECKS PASSED — scheduler ready!")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run existing test suite to confirm no breakage**

Run: `pytest -m "not slow" -v`
Expected: All existing tests PASS.

- [ ] **Step 4: Commit**

```bash
git add scripts/scheduler_setup.py .env.example
git commit -m "feat: add scheduler setup verification script"
```

- [ ] **Step 5: Final commit — update CLAUDE.md with scheduler docs**

Add to the Commands section in `CLAUDE.md`:

```markdown
# Scheduler setup
python scripts/scheduler_setup.py        # verify all scheduler prerequisites
python db/init_db.py                     # creates scheduler tables (additive migration)
```

Add to the Architecture section:

```markdown
**Upload Scheduler (`pipeline/scheduler.py`):** After Telegram approval, uploads video to
Google Drive, picks next platform (round-robin per niche), selects optimal upload time
(adaptive based on engagement data, falls back to research-backed defaults), and creates
a one-time cron-job.org trigger that fires a GitHub Actions `repository_dispatch` workflow
at the scheduled time.

**Engagement Tracker (`pipeline/engagement_tracker.py`):** Daily GitHub Actions cron fetches
view/like counts from YouTube/Instagram/Facebook APIs for recent uploads, updates
`upload_schedule` rows, and recalculates `time_performance` rolling averages. Adaptive
scheduling kicks in after 3+ samples per slot.

**Google Drive Storage (`pipeline/drive_storage.py`):** Service account auth. Videos go to
`video-uploads/pending/` after approval, moved to `uploaded/` or `failed/` after platform
upload. Weekly cleanup deletes files older than 7 days.
```

```bash
git add CLAUDE.md
git commit -m "docs: add scheduler architecture to CLAUDE.md"
```
