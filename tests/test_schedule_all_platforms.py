"""schedule_all_platforms must not schedule an image post as a video."""
import os
import sqlite3

import pytest


@pytest.fixture(scope="module")
def sched():
    """Import the script without leaking the .env that `config` loads into os.environ."""
    before = dict(os.environ)
    import scripts.schedule_all_platforms as mod
    os.environ.clear()
    os.environ.update(before)
    return mod


def test_only_mp4_is_a_video(sched):
    _is_video_path = sched._is_video_path
    assert _is_video_path("output/video/x.mp4") and _is_video_path("X.MP4")
    assert not _is_video_path("output/skillstotraineyes/images/hidden_7.jpg")


def test_assembled_jpg_row_is_skipped(sched, tmp_path):
    jpg = tmp_path / "hidden_7.jpg"
    jpg.write_bytes(b"x")
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE videos (id INTEGER, niche_id TEXT, file_path TEXT, status TEXT)")
    conn.execute("INSERT INTO videos VALUES (1, 'skillstotraineyes', ?, 'assembled')", (str(jpg),))
    assert sched.process_video(1, conn, dry_run=True) == []
