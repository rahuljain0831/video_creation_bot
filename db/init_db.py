"""Initialize SQLite database from schema."""
import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# Additive migrations — safe to run on existing databases.
# New installs get these columns from schema.sql; existing DBs get them here.
_MIGRATIONS = [
    ("videos", "prompt",          "TEXT"),
    ("videos", "scene_count",     "INTEGER"),
    ("videos", "niche_id",        "TEXT"),
    ("videos", "variation_params","TEXT"),
    ("videos", "voice_provider",  "TEXT"),
    ("videos", "file_path",       "TEXT"),
]

# Indexes created after migrations so all columns are guaranteed to exist.
_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_videos_status  ON videos(status)",
    "CREATE INDEX IF NOT EXISTS idx_videos_niche   ON videos(niche_id)",
    "CREATE INDEX IF NOT EXISTS idx_feedback_video ON feedback(video_id)",
    "CREATE INDEX IF NOT EXISTS idx_decisions_video ON decisions(video_id)",
    "CREATE INDEX IF NOT EXISTS idx_quota_provider_date ON quota_usage(provider, date)",
]


def _create_tables(conn: sqlite3.Connection) -> None:
    """Run only CREATE TABLE statements from schema.sql (skip indexes)."""
    schema = SCHEMA_PATH.read_text()
    for statement in schema.split(";"):
        stmt = statement.strip()
        if not stmt or stmt.upper().startswith("CREATE INDEX"):
            continue
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as e:
            # PRAGMA statements and other non-table commands may vary; skip silently
            if "already exists" not in str(e):
                raise
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    """Add missing columns to existing tables without losing data."""
    for table, column, col_type in _MIGRATIONS:
        existing = {
            row[1]
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            print(f"  migrated: {table}.{column}")
    conn.commit()


def _create_indexes(conn: sqlite3.Connection) -> None:
    for sql in _INDEXES:
        conn.execute(sql)
    conn.commit()


def init_db(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    _create_tables(conn)   # CREATE TABLE IF NOT EXISTS (no indexes)
    _migrate(conn)         # add any missing columns
    _create_indexes(conn)  # create indexes (all columns now guaranteed present)
    conn.close()
    print(f"DB initialized: {db_path}")


if __name__ == "__main__":
    import json
    settings = json.loads((Path(__file__).parent.parent / "settings.json").read_text())
    init_db(settings["paths"]["db"])
