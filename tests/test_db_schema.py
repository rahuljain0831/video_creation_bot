"""Tests for database schema and migrations."""
import sqlite3
import tempfile
from pathlib import Path

import pytest

from db.init_db import init_db


@pytest.fixture
def temp_db():
    """Create a temporary database file for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        yield db_path


def test_upload_schedule_table_exists(temp_db):
    """Verify upload_schedule table exists after init_db."""
    init_db(temp_db)
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='upload_schedule'"
    )
    assert cursor.fetchone() is not None, "upload_schedule table not found"
    conn.close()


def test_time_performance_table_exists(temp_db):
    """Verify time_performance table exists after init_db."""
    init_db(temp_db)
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='time_performance'"
    )
    assert cursor.fetchone() is not None, "time_performance table not found"
    conn.close()


def test_platform_rotation_table_exists(temp_db):
    """Verify platform_rotation table exists after init_db."""
    init_db(temp_db)
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='platform_rotation'"
    )
    assert cursor.fetchone() is not None, "platform_rotation table not found"
    conn.close()


def test_upload_schedule_columns(temp_db):
    """Verify upload_schedule has all expected columns."""
    init_db(temp_db)
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(upload_schedule)")
    columns = {row[1] for row in cursor.fetchall()}

    expected_columns = {
        "id",
        "video_id",
        "platform",
        "niche_id",
        "scheduled_at",
        "status",
        "cronjob_id",
        "drive_file_id",
        "engagement_views",
        "engagement_likes",
        "platform_post_id",
        "caption_variant",
        "created_at",
    }
    assert expected_columns.issubset(
        columns
    ), f"Missing columns: {expected_columns - columns}"
    conn.close()


def test_time_performance_columns(temp_db):
    """Verify time_performance has all expected columns."""
    init_db(temp_db)
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(time_performance)")
    columns = {row[1] for row in cursor.fetchall()}

    expected_columns = {
        "id",
        "niche_id",
        "platform",
        "hour_utc",
        "day_of_week",
        "avg_views",
        "avg_likes",
        "sample_count",
        "updated_at",
    }
    assert expected_columns.issubset(
        columns
    ), f"Missing columns: {expected_columns - columns}"
    conn.close()


def test_platform_rotation_columns(temp_db):
    """Verify platform_rotation has all expected columns."""
    init_db(temp_db)
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(platform_rotation)")
    columns = {row[1] for row in cursor.fetchall()}

    expected_columns = {
        "niche_id",
        "last_platform",
        "updated_at",
    }
    assert expected_columns.issubset(
        columns
    ), f"Missing columns: {expected_columns - columns}"
    conn.close()


def test_existing_tables_survive_migration(temp_db):
    """Verify existing tables (videos, feedback) survive migration."""
    init_db(temp_db)
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()

    # Check videos table
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='videos'")
    assert cursor.fetchone() is not None, "videos table not found after migration"

    # Check feedback table
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='feedback'")
    assert cursor.fetchone() is not None, "feedback table not found after migration"

    conn.close()


def test_upload_schedule_unique_constraint(temp_db):
    """Verify upload_schedule table can be created and accepts data."""
    init_db(temp_db)
    conn = sqlite3.connect(temp_db)
    conn.execute("PRAGMA foreign_keys=ON")
    cursor = conn.cursor()

    # Insert a test video first
    cursor.execute(
        "INSERT INTO videos (niche_id, status) VALUES (?, ?)",
        ("mythology", "approved")
    )
    video_id = cursor.lastrowid

    # Insert a schedule entry
    cursor.execute(
        """INSERT INTO upload_schedule
           (video_id, platform, niche_id, scheduled_at, status)
           VALUES (?, ?, ?, datetime('now'), ?)""",
        (video_id, "youtube", "mythology", "pending")
    )
    conn.commit()

    # Verify it was inserted
    cursor.execute("SELECT COUNT(*) FROM upload_schedule WHERE video_id = ?", (video_id,))
    count = cursor.fetchone()[0]
    assert count == 1, "Failed to insert schedule entry"

    conn.close()


def test_time_performance_unique_constraint(temp_db):
    """Verify time_performance unique constraint on (niche_id, platform, hour_utc, day_of_week)."""
    init_db(temp_db)
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()

    # Insert first entry
    cursor.execute(
        """INSERT INTO time_performance
           (niche_id, platform, hour_utc, day_of_week, avg_views, avg_likes, sample_count)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("mythology", "youtube", 10, 1, 100.0, 20.0, 5)
    )
    conn.commit()

    # Try to insert duplicate (should fail)
    with pytest.raises(sqlite3.IntegrityError):
        cursor.execute(
            """INSERT INTO time_performance
               (niche_id, platform, hour_utc, day_of_week, avg_views, avg_likes, sample_count)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("mythology", "youtube", 10, 1, 200.0, 30.0, 3)
        )
        conn.commit()

    conn.close()


def test_platform_rotation_primary_key(temp_db):
    """Verify platform_rotation niche_id is primary key."""
    init_db(temp_db)
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()

    # Insert first entry
    cursor.execute(
        "INSERT INTO platform_rotation (niche_id, last_platform) VALUES (?, ?)",
        ("mythology", "youtube")
    )
    conn.commit()

    # Try to insert duplicate (should fail)
    with pytest.raises(sqlite3.IntegrityError):
        cursor.execute(
            "INSERT INTO platform_rotation (niche_id, last_platform) VALUES (?, ?)",
            ("mythology", "instagram")
        )
        conn.commit()

    conn.close()


def test_indexes_created(temp_db):
    """Verify all new indexes are created."""
    init_db(temp_db)
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()

    expected_indexes = [
        "idx_schedule_status",
        "idx_schedule_niche",
        "idx_schedule_time",
        "idx_timeperf_niche_plat",
    ]

    for idx_name in expected_indexes:
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
            (idx_name,)
        )
        assert cursor.fetchone() is not None, f"Index {idx_name} not found"

    conn.close()
