"""Initialize SQLite database from schema."""
import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# Additive migrations — safe to run on existing databases.
# New installs get these columns from schema.sql; existing DBs get them here.
_MIGRATIONS = [
    ("videos",      "prompt",           "TEXT"),
    ("videos",      "scene_count",      "INTEGER"),
    ("videos",      "niche_id",         "TEXT"),
    ("videos",      "variation_params", "TEXT"),
    ("videos",      "voice_provider",   "TEXT"),
    ("videos",      "file_path",        "TEXT"),
    ("quota_usage", "last_error_code",  "INTEGER"),
    ("videos",      "retry_count",       "INTEGER DEFAULT 0"),
    ("videos",      "parent_video_id",   "INTEGER"),
    ("videos",      "rejection_feedback","TEXT"),
]

# DDL migrations: full SQL statements run with error swallowed if already applied.
_DDL_MIGRATIONS = [
    """CREATE TABLE IF NOT EXISTS quota_reset_log (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        provider        TEXT NOT NULL,
        reset_interval  TEXT NOT NULL,
        last_reset_date DATE NOT NULL,
        reset_at        DATETIME DEFAULT (datetime('now')),
        UNIQUE(provider, reset_interval)
    )""",
    """CREATE TABLE IF NOT EXISTS image_library (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        file_path        TEXT NOT NULL UNIQUE,
        original_path    TEXT,
        deity_name       TEXT,
        tradition        TEXT,
        full_description TEXT,
        tags             TEXT,
        ingested_at      DATETIME DEFAULT (datetime('now'))
    )""",
    """CREATE VIRTUAL TABLE IF NOT EXISTS image_library_fts USING fts5(
        deity_name, tradition, full_description, tags,
        content='image_library', content_rowid='id'
    )""",
    """CREATE TRIGGER IF NOT EXISTS image_library_ai AFTER INSERT ON image_library BEGIN
        INSERT INTO image_library_fts(rowid, deity_name, tradition, full_description, tags)
        VALUES (new.id, new.deity_name, new.tradition, new.full_description, new.tags);
    END""",
    """CREATE TRIGGER IF NOT EXISTS image_library_ad AFTER DELETE ON image_library BEGIN
        INSERT INTO image_library_fts(image_library_fts, rowid, deity_name, tradition, full_description, tags)
        VALUES ('delete', old.id, old.deity_name, old.tradition, old.full_description, old.tags);
    END""",
    """CREATE TRIGGER IF NOT EXISTS image_library_au AFTER UPDATE ON image_library BEGIN
        INSERT INTO image_library_fts(image_library_fts, rowid, deity_name, tradition, full_description, tags)
        VALUES ('delete', old.id, old.deity_name, old.tradition, old.full_description, old.tags);
        INSERT INTO image_library_fts(rowid, deity_name, tradition, full_description, tags)
        VALUES (new.id, new.deity_name, new.tradition, new.full_description, new.tags);
    END""",
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
]

# Indexes created after migrations so all columns are guaranteed to exist.
_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_videos_status  ON videos(status)",
    "CREATE INDEX IF NOT EXISTS idx_videos_niche   ON videos(niche_id)",
    "CREATE INDEX IF NOT EXISTS idx_feedback_video ON feedback(video_id)",
    "CREATE INDEX IF NOT EXISTS idx_decisions_video ON decisions(video_id)",
    "CREATE INDEX IF NOT EXISTS idx_quota_provider_date ON quota_usage(provider, date)",
    "CREATE INDEX IF NOT EXISTS idx_image_library_deity     ON image_library(deity_name)",
    "CREATE INDEX IF NOT EXISTS idx_image_library_tradition ON image_library(tradition)",
    "CREATE INDEX IF NOT EXISTS idx_schedule_status    ON upload_schedule(status)",
    "CREATE INDEX IF NOT EXISTS idx_schedule_niche     ON upload_schedule(niche_id)",
    "CREATE INDEX IF NOT EXISTS idx_schedule_time      ON upload_schedule(scheduled_at)",
    "CREATE INDEX IF NOT EXISTS idx_timeperf_niche_plat ON time_performance(niche_id, platform)",
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
    """Add missing columns and tables to existing databases without losing data."""
    for table, column, col_type in _MIGRATIONS:
        existing = {
            row[1]
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            print(f"  migrated: {table}.{column}")
    for ddl in _DDL_MIGRATIONS:
        try:
            conn.execute(ddl)
            print(f"  migrated: DDL applied")
        except sqlite3.OperationalError as e:
            if "already exists" not in str(e):
                raise
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
