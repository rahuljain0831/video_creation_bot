-- Quote Video Agent — SQLite Schema
-- Phase 0: Full schema defined upfront per design doc

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS quotes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_text  TEXT NOT NULL UNIQUE,
    source      TEXT NOT NULL CHECK(source IN ('ai', 'manual')),
    model_used  TEXT,
    embedding   BLOB,           -- nomic-embed-text vector, stored as JSON float array
    created_at  DATETIME DEFAULT (datetime('now')),
    used        INTEGER DEFAULT 0 CHECK(used IN (0, 1)),
    video_id    INTEGER REFERENCES videos(id)
);

CREATE TABLE IF NOT EXISTS background_clips (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path   TEXT NOT NULL,
    provider    TEXT NOT NULL CHECK(provider IN ('veo', 'kling', 'hf', 'static')),
    mood_tag    TEXT,
    color_tag   TEXT,
    duration    REAL,
    times_used  INTEGER DEFAULT 0,
    created_at  DATETIME DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS videos (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id          TEXT,
    quote_id            INTEGER REFERENCES quotes(id),
    background_clip_id  INTEGER REFERENCES background_clips(id),
    variation_params    TEXT,   -- JSON: crop, speed, tint, overlay, start_offset
    voice_provider      TEXT,
    status              TEXT NOT NULL DEFAULT 'queued' CHECK(status IN (
                            'queued', 'quote_selected', 'bg_ready', 'voice_ready',
                            'assembled', 'screened', 'sent', 'approved', 'rejected', 'posted'
                        )),
    file_path           TEXT,
    created_at          DATETIME DEFAULT (datetime('now')),
    -- design-v3: prompt-driven pipeline fields
    prompt              TEXT,           -- original user prompt
    tone                TEXT,           -- warm | funny | educational | inspirational | dramatic | calming
    ending_type         TEXT,           -- punchline | tip | reveal | other
    topic               TEXT,           -- loose 2-4 word topic label
    scene_count         INTEGER         -- 1 or 2
);

CREATE TABLE IF NOT EXISTS feedback (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id        INTEGER NOT NULL REFERENCES videos(id),
    rating          TEXT NOT NULL CHECK(rating IN ('good', 'bad')),
    feedback_text   TEXT,
    parsed_tags     TEXT,   -- JSON array of tags
    source          TEXT NOT NULL CHECK(source IN ('manual', 'prescreen', 'engagement')),
    rated_at        DATETIME DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id        INTEGER REFERENCES videos(id),
    decision_point  TEXT NOT NULL,  -- quote_selection | background_selection | voice_selection | style_selection
    chosen_option   TEXT NOT NULL,
    reasoning       TEXT,
    decided_at      DATETIME DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS quota_usage (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    provider    TEXT NOT NULL,
    date        DATE NOT NULL,
    units_used  INTEGER DEFAULT 0,
    unit_limit  INTEGER,
    UNIQUE(provider, date)
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_quotes_used ON quotes(used);
CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status);
CREATE INDEX IF NOT EXISTS idx_videos_account ON videos(account_id);
CREATE INDEX IF NOT EXISTS idx_feedback_video ON feedback(video_id);
CREATE INDEX IF NOT EXISTS idx_decisions_video ON decisions(video_id);
CREATE INDEX IF NOT EXISTS idx_quota_provider_date ON quota_usage(provider, date);
