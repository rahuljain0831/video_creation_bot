# Social Media Scheduler & Upload Optimizer

**Date:** 2026-08-11
**Status:** Approved

## Overview

Automated scheduling system that uploads approved videos to social media platforms at optimal times, rotates platforms per niche, adapts upload timing based on engagement data, and enhances captions/hashtags with trending context.

## Requirements

- 6 niches: mythology, scary_stories, heists, space_science, ai_tech_tools, finance_facts
- 1 video per niche per day = 6 videos/day
- 3 platforms: YouTube, Instagram, Facebook (TikTok/X later)
- Platform rotation per niche (youtube → instagram → facebook → youtube...)
- No local machine dependency — runs on GitHub Actions (private repo, ~450 min/month)
- Google Drive for video storage between approval and upload
- Adaptive scheduling: learns best upload times from engagement data
- Event-driven: one cron-job.org trigger per approved video (no polling)

## 1. Database Schema

Three new tables in `agent.db`:

### `upload_schedule`

| Column | Type | Purpose |
|--------|------|---------|
| id | INTEGER PK | Auto-increment |
| video_id | INTEGER FK | Links to `videos` table |
| platform | TEXT | youtube / instagram / facebook |
| niche_id | TEXT | Niche identifier |
| scheduled_at | DATETIME | Exact upload time (UTC) |
| status | TEXT | pending / uploading / done / failed |
| cronjob_id | TEXT | cron-job.org job ID for cleanup |
| drive_file_id | TEXT | Google Drive file ID |
| engagement_views | INTEGER | Fetched post-upload |
| engagement_likes | INTEGER | Fetched post-upload |
| platform_post_id | TEXT | YouTube video ID / IG media ID / FB post ID |
| caption_variant | TEXT | "A" or "B" for A/B tracking |
| created_at | DATETIME | Row creation time |

### `time_performance`

| Column | Type | Purpose |
|--------|------|---------|
| id | INTEGER PK | Auto-increment |
| niche_id | TEXT | Niche identifier |
| platform | TEXT | Platform name |
| hour_utc | INTEGER | 0-23 |
| day_of_week | INTEGER | 0-6 (Mon-Sun) |
| avg_views | REAL | Rolling average |
| avg_likes | REAL | Rolling average |
| sample_count | INTEGER | Number of uploads in this slot |
| updated_at | DATETIME | Last recalculation |

### `platform_rotation`

| Column | Type | Purpose |
|--------|------|---------|
| niche_id | TEXT PK | Niche identifier |
| last_platform | TEXT | Last used platform |
| updated_at | DATETIME | Last rotation |

## 2. Post-Approval Flow

Triggered by Telegram "Approve" callback:

1. Upload video mp4 + script JSON to Google Drive `video-uploads/pending/`
2. Check `platform_rotation` table, pick next platform (round-robin)
3. Query `time_performance` for best hour (niche + platform). If insufficient data (<3 samples), use research-backed defaults. Ensure minimum 30-minute gap between any two scheduled uploads.
4. Create one-time job via cron-job.org API targeting GitHub `repository_dispatch`
5. Insert `upload_schedule` row with status=pending
6. Send Telegram confirmation: scheduled platform, time, niche

### Default Time Slots (IST, used until adaptive data kicks in)

| Platform | Best Hours |
|----------|-----------|
| YouTube | 5:00 PM, 8:00 PM, 12:00 PM |
| Instagram | 7:00 PM, 9:00 PM, 11:00 AM |
| Facebook | 1:00 PM, 6:00 PM, 9:00 PM |

Each niche assigned a different hour from the pool to avoid crowding.

## 3. GitHub Actions Upload Workflow

Triggered by `repository_dispatch` event with `schedule_id` in payload.

1. Checkout repo
2. Download schedule manifest from Drive: `pending/{slug}_schedule.json` contains platform, caption, hashtags, drive_file_id, schedule_id (no DB access needed from Actions — all metadata travels with the video)
3. Download video from Google Drive (drive_file_id)
4. Load captions + hashtags from script JSON sidecar
5. Upload to target platform using existing uploaders (`youtube_upload.py`, `instagram_upload.py`, `facebook_upload.py`)
6. Update `upload_schedule`: status=done, store platform_post_id
7. Send Telegram notification with upload result + URL

### GitHub Secrets Required

- `GOOGLE_DRIVE_CREDENTIALS` — service account JSON (base64 encoded)
- `YOUTUBE_CREDENTIALS` — OAuth token JSON
- `INSTAGRAM_CREDENTIALS` — access token
- `FACEBOOK_CREDENTIALS` — page access token
- `TELEGRAM_BOT_TOKEN` — for notifications
- `CRONJOB_API_KEY` — cron-job.org API key

### Failure Handling

- Upload failure: status=failed, Telegram alert, no auto-retry
- Drive download failure: same treatment
- Credentials expired: Telegram alert specifying which platform needs re-auth

## 4. Adaptive Scheduling (Engagement Feedback Loop)

Daily GitHub Actions cron at 6:00 AM IST:

1. Fetch engagement stats for uploads from last 48 hours
   - YouTube Data API: views, likes
   - Instagram Graph API: plays, likes
   - Facebook Graph API: views, reactions
2. Update `upload_schedule` rows with engagement data
3. Recalculate `time_performance` table
   - Group by (niche, platform, hour_utc, day_of_week)
   - Rolling average over last 30 days
   - Minimum 3 samples before slot is "trusted"

### Evolution Timeline

| Stage | Behavior |
|-------|----------|
| Week 1-2 | Research-backed defaults. Data collection only. |
| Week 3+ | Slots with 3+ samples blend 50/50 with defaults. |
| Week 6+ | Fully data-driven. Best performing hour wins. |
| Ongoing | 20% exploration — 1 in 5 uploads tries untested slot. |

Edge case: zero data for a niche+platform combo falls back to that platform's global best hour across all niches.

## 5. Google Drive Integration

### Authentication

Service account (not user OAuth). Created in same Google Cloud project as Gemini. Shared Drive folder with service account email.

### Folder Structure

```
video-uploads/
  ├── pending/      ← video lands here after Telegram approval
  ├── uploaded/     ← moved here after successful platform upload
  └── failed/       ← moved here on upload failure
```

### File Convention

- `{slug}.mp4` — video file
- `{slug}.json` — script JSON sidecar (contains captions, hashtags, metadata)
- `{slug}_schedule.json` — schedule manifest (platform, caption for that platform, hashtags, drive_file_id, schedule_id, niche_id). This file is what GitHub Actions reads — avoids needing DB access from cloud.

### Storage Budget

- Videos: ~10-50MB each, avg ~30MB
- 6 videos/day = ~180MB/day
- 7-day cleanup (weekly GitHub Actions cron deletes files in `uploaded/` older than 7 days)
- Max usage: ~1.3GB, well within 15GB free tier

### Dependencies

- `google-api-python-client`
- `google-auth`

## 6. Enhanced Captions & Hashtags

### Changes to `social_captions.py`

1. **Trending hashtag injection**: Before LLM call, fetch trending context per platform. YouTube uses Trending API. Instagram/Facebook use curated evergreen niche sets.

2. **Niche hashtag banks**: New file `hashtag_banks.json` with curated per-niche base hashtags. LLM blends bank + trending + video-specific tags.

3. **Caption A/B tracking**: LLM generates 2 caption variants. System alternates A/B. Engagement loop tracks performance. After 10+ samples, auto-favor winning style.

4. **Updated LLM prompt** includes: trending tags, niche bank tags, target platform, winning caption style (if data exists).

### Backwards Compatibility

No changes to `format_telegram_message()`, `_PLATFORM_SPECS` structure, or return format. Existing callers unaffected.

## New Files

| File | Purpose |
|------|---------|
| `pipeline/drive_storage.py` | Google Drive upload/download/move/cleanup |
| `pipeline/scheduler.py` | Optimal time selection, cron-job.org API, rotation logic |
| `pipeline/engagement_tracker.py` | Fetch engagement stats, update time_performance |
| `hashtag_banks.json` | Curated per-niche hashtag sets |
| `.github/workflows/scheduled-upload.yml` | Upload workflow (repository_dispatch trigger) |
| `.github/workflows/engagement-fetch.yml` | Daily engagement stats cron |
| `.github/workflows/drive-cleanup.yml` | Weekly Drive cleanup cron |
| `db/schema_scheduler.sql` | New table definitions |
| `scripts/scheduler_setup.py` | One-time setup: Drive folder, cron-job.org account verify |

## Modified Files

| File | Change |
|------|--------|
| `pipeline/social_captions.py` | Trending injection, A/B variants, hashtag bank integration |
| `review/telegram_bot.py` | Post-approval hook: Drive upload + schedule creation + confirmation msg |
| `db/schema.sql` | Add new tables |
| `db/init_db.py` | Create new tables |
| `social_config.json` | Add cron-job.org config section |
| `requirements.txt` | Add google-api-python-client, google-auth |
