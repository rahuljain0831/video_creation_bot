"""Test the post-approval scheduling hook in telegram_bot.py."""
import sqlite3
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_schedule_approved_video_exists_and_callable():
    """_schedule_approved_video function should exist and be callable."""
    from review.telegram_bot import _schedule_approved_video
    assert callable(_schedule_approved_video)


def test_schedule_approved_video_returns_none_for_missing_video():
    """Should return None when video has no file_path."""
    from review.telegram_bot import _schedule_approved_video
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE videos (
        id INTEGER PRIMARY KEY, niche_id TEXT, file_path TEXT, status TEXT, retry_count INTEGER DEFAULT 0
    )""")
    conn.execute("INSERT INTO videos (id, niche_id, status) VALUES (1, 'mythology', 'approved')")
    conn.commit()
    result = _schedule_approved_video(1, conn)
    assert result is None
    conn.close()


def test_schedule_approved_video_returns_none_for_missing_file():
    """Should return None when video file doesn't exist on disk."""
    from review.telegram_bot import _schedule_approved_video
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE videos (
        id INTEGER PRIMARY KEY, niche_id TEXT, file_path TEXT, status TEXT, retry_count INTEGER DEFAULT 0
    )""")
    conn.execute("INSERT INTO videos (id, niche_id, file_path, status) VALUES (1, 'mythology', '/nonexistent/video.mp4', 'approved')")
    conn.commit()
    result = _schedule_approved_video(1, conn)
    assert result is None
    conn.close()
