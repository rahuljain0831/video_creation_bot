-- Video Creation Agent — SQLite Schema
-- Niche-driven story videos using user-provided image library.

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS videos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id      TEXT,
    niche_id        TEXT,           -- matches niches[].id in settings.json
    prompt          TEXT,           -- story seed (may be empty for random)
    scene_count     INTEGER,
    variation_params TEXT,          -- JSON: image seed, provider used, etc.
    voice_provider  TEXT,
    status          TEXT NOT NULL DEFAULT 'queued' CHECK(status IN (
                        'queued', 'bg_ready', 'voice_ready',
                        'assembled', 'screened', 'sent', 'approved', 'rejected', 'posted'
                    )),
    file_path       TEXT,
    created_at      DATETIME DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS feedback (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id        INTEGER NOT NULL REFERENCES videos(id),
    rating          TEXT NOT NULL CHECK(rating IN ('good', 'bad')),
    feedback_text   TEXT,
    parsed_tags     TEXT,           -- JSON array of tags
    source          TEXT NOT NULL CHECK(source IN ('manual', 'prescreen', 'engagement')),
    rated_at        DATETIME DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id        INTEGER REFERENCES videos(id),
    decision_point  TEXT NOT NULL,  -- script_generation | image_provider | voice_selection
    chosen_option   TEXT NOT NULL,
    reasoning       TEXT,
    decided_at      DATETIME DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS quota_usage (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    provider        TEXT NOT NULL,      -- groq | cerebras | ollama | etc.
    date            DATE NOT NULL,
    units_used      INTEGER DEFAULT 0,
    unit_limit      INTEGER,
    last_error_code INTEGER,            -- HTTP status of last failed call (429, 500, etc.)
    UNIQUE(provider, date)
);

CREATE TABLE IF NOT EXISTS quota_reset_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    provider        TEXT NOT NULL,
    reset_interval  TEXT NOT NULL,      -- "daily" | "weekly" | "monthly"
    last_reset_date DATE NOT NULL,
    reset_at        DATETIME DEFAULT (datetime('now')),
    UNIQUE(provider, reset_interval)
);

-- Indexes are created by db/init_db.py after migrations run,
-- so all columns are guaranteed to exist before index creation.

-- Image library: user-provided deity images analyzed with Gemini Vision.
-- Ingest with: python ingest_library.py --folder <path>
CREATE TABLE IF NOT EXISTS image_library (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path        TEXT NOT NULL UNIQUE,
    original_path    TEXT,
    deity_name       TEXT,
    tradition        TEXT,
    full_description TEXT,
    tags             TEXT,
    ingested_at      DATETIME DEFAULT (datetime('now'))
);
