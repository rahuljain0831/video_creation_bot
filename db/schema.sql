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
                        'assembled', 'screened', 'sent', 'approved', 'rejected', 'posted',
                        'permanently_rejected', 'waiting_quota'
                    )),
    file_path       TEXT,
    retry_count     INTEGER DEFAULT 0,
    parent_video_id INTEGER REFERENCES videos(id),
    rejection_feedback TEXT,
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

-- Upload scheduler: tracks video publishing to multiple platforms with timing.
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

-- Time performance metrics: per-niche, per-platform, per-hour performance data.
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

-- Platform rotation: track last platform used for each niche to enable round-robin.
CREATE TABLE IF NOT EXISTS platform_rotation (
    niche_id        TEXT PRIMARY KEY,
    last_platform   TEXT NOT NULL CHECK(last_platform IN ('youtube', 'instagram', 'facebook')),
    updated_at      DATETIME DEFAULT (datetime('now'))
);
