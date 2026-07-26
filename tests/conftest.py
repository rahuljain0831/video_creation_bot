"""Shared fixtures for all tests."""
import sqlite3
import pytest
from pathlib import Path


@pytest.fixture
def db(tmp_path):
    """In-memory SQLite DB initialized from schema."""
    schema = (Path(__file__).parent.parent / "db" / "schema.sql").read_text()
    conn = sqlite3.connect(":memory:")
    conn.executescript(schema)
    yield conn
    conn.close()


@pytest.fixture
def db_with_quotes(db):
    """DB pre-loaded with 3 distinct quotes + embeddings."""
    import json
    quotes = [
        ("Believe in yourself every single day.", [0.1, 0.2, 0.3] + [0.0] * 765),
        ("Your past does not define your future.", [0.9, 0.1, 0.0] + [0.0] * 765),
        ("Courage is the first step to greatness.", [0.5, 0.5, 0.5] + [0.0] * 765),
    ]
    for text, emb in quotes:
        db.execute(
            "INSERT INTO quotes (quote_text, source, model_used, embedding) VALUES (?, 'ai', 'test', ?)",
            (text, json.dumps(emb)),
        )
    db.commit()
    return db


@pytest.fixture
def db_with_clips(db):
    """DB pre-loaded with 3 background clips."""
    clips = [
        ("/fake/clip1.mp4", "veo", "calm", "blue", 6.0),
        ("/fake/clip2.mp4", "kling", "energetic", "warm", 5.0),
        ("/fake/clip3.mp4", "veo", "calm", "golden", 6.0),
    ]
    for path, provider, mood, color, dur in clips:
        db.execute(
            "INSERT INTO background_clips (file_path, provider, mood_tag, color_tag, duration) VALUES (?,?,?,?,?)",
            (path, provider, mood, color, dur),
        )
    db.commit()
    return db
