-- Video Creation Agent — SQLite Schema (design-v3 pivot)
-- Niche-driven story videos, AI-generated images, no quote logic.

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
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    provider    TEXT NOT NULL,      -- huggingface | google_ai_studio | pollinations | groq | etc.
    date        DATE NOT NULL,
    units_used  INTEGER DEFAULT 0,
    unit_limit  INTEGER,
    UNIQUE(provider, date)
);

-- Indexes are created by db/init_db.py after migrations run,
-- so all columns are guaranteed to exist before index creation.
