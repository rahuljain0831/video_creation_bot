"""Initialize SQLite database from schema."""
import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# New columns added in design-v3 migration
_MIGRATIONS = [
    ("videos", "prompt",      "TEXT"),
    ("videos", "tone",        "TEXT"),
    ("videos", "ending_type", "TEXT"),
    ("videos", "topic",       "TEXT"),
    ("videos", "scene_count", "INTEGER"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    """Add new columns to existing tables without losing data."""
    for table, column, col_type in _MIGRATIONS:
        existing = {
            row[1]
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            print(f"  migrated: {table}.{column}")
    conn.commit()


def init_db(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_PATH.read_text())
    _migrate(conn)
    conn.commit()
    conn.close()
    print(f"DB initialized: {db_path}")


if __name__ == "__main__":
    import json, sys
    settings = json.loads((Path(__file__).parent.parent / "settings.json").read_text())
    init_db(settings["paths"]["db"])
