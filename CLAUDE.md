# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# One-time setup
cp .env.example .env          # fill in API keys
python db/init_db.py          # create output/db/agent.db from schema.sql

# Scheduler setup
python scripts/scheduler_setup.py        # verify all scheduler prerequisites
python db/init_db.py                     # creates scheduler tables (additive migration)

# Ingest your deity images (required before running the pipeline)
python ingest_library.py --folder /path/to/images            # analyze and store all images
python ingest_library.py --folder /path/to/images --tradition hindu  # with tradition hint

# Run a video end-to-end
python run_niche.py                              # interactive niche menu
python run_niche.py mythology                    # niche id direct
python run_niche.py mythology "story of Shiva"  # niche + story seed
python run_niche.py mythology --dry-run          # script only, no images/video
python run_niche.py mythology --no-telegram      # assemble but skip Telegram send

# Inspect image library coverage
python list_library.py                    # all indexed deities + image count
python list_library.py --missing          # deities in deity_prompts.json with no images
python list_library.py --deity Shiva      # all images for a specific deity
python list_library.py --report           # full coverage report

# Verify LLM router fallback chain
python llm_router.py

# Verify API keys
python verify_keys.py

# Tests
pytest                   # all tests
pytest -m "not slow"     # skip video-writing / network tests
```

## Architecture

**Entry point:** `run_niche.py` — user selects a niche (Mythology / Scary Stories / Heists / Space & Science / AI & Tech Tools / Finance Facts),
optionally provides a story seed, and the pipeline runs end-to-end.

**Pipeline (in order):**
1. `pipeline/script_gen.py` — LLM generates a structured story script for the chosen niche.
   Tone comes from niche config in `settings.json`. Each scene gets `narration` (for TTS)
   and `image_prompt` (for library lookup). All decisions are written to the `decisions` DB
   table before any image lookup runs.
2. Scene images — source per niche via `image_source` in `settings.json`:
   `library` (curated deity images), `pexels` (stock retrieval), `generate`
   (cloud image APIs → local ComfyUI fallback), `comfyui` (local only).

   **Image policy (`pipeline/image_policy.py`), enforced in code, not prompts:**
   - Niches with `allow_local_generation: false` (mythology) never run local
     generation. `resolve_image_source()` downgrades a local-only source to
     `library`; `comfyui_gen` and `image_gen` raise `LocalGenerationBlocked`.
   - Generated images contain no human figures. Human-referring comma chunks are
     stripped from the positive prompt and pushed into the negative prompt
     (`apply_no_human_policy`). Kills distorted hands/faces. Toggle:
     `image_gen.no_humans` in `settings.json`.

   **`pipeline/image_gen.py`** — provider chain read from `image_keys.json`
   (top-to-bottom = priority, same shape as `llm_keys.json`): gemini →
   together_ai → huggingface → pollinations (keyless), then local ComfyUI.
   Verify with `python scripts/test_image_gen.py [--live]`.

   `pipeline/image_library.py` + `pipeline/deity_map.py` — selects best matching image from
   the user-provided library for each scene.
   Strategy per scene:
     a. Detect deity name in image_prompt via `deity_prompts.find_deity()`
     b. If found: exact DB match on `deity_name` column
     c. Rank multiple matches by tag overlap with scene prompt
     d. Fallback: FTS5 full-text search on prompt keywords
     e. Fallback: tradition filter → random image
3. `pipeline/tts.py` — local TTS only (Edge TTS → Piper → Kokoro per `settings.json` priority).
4. `pipeline/audio_bg.py` — optional background audio (chanting/meditation). Fetched and mixed
   at low volume under narration. Toggle via `background_audio.enabled` in `settings.json`.
5. `pipeline/ffmpeg_assembler.py` — applies Ken Burns (pan/zoom via `zoompan` filter) to each
   image, concatenates clips, burns captions, mixes audio into 9:16 mp4.
   Entry point: `assemble_from_images()`.
6. `review/telegram_bot.py` — sends video + story metadata for manual review.
7. After Telegram approval, `pipeline/scheduler.py` handles Google Drive upload, platform selection
   (round-robin per niche via `platform_rotation` table), optimal time scheduling (adaptive from
   `time_performance` data), and cron-job.org triggers for GitHub Actions `repository_dispatch`.

**LLM routing (`llm_router.py`):** `call_llm()` tries Groq → Cerebras → Google AI Studio →
Ollama local in order. Provider order is configured in `settings.json` under `llm_router`.
Returns `(text, model_used)`.

**Quota tracking (`pipeline/quota_tracker.py`):** Tracks LLM provider calls (groq, cerebras).
Pre-call: checks `quota_usage` table against `quota.json` daily_limit. Post-call: logs result.
Daily reset runs at startup. Provider config in `quota.json`.

**Upload Scheduler (`pipeline/scheduler.py`):** After Telegram approval, uploads video to
Google Drive, picks next platform (round-robin per niche), selects optimal upload time
(adaptive based on engagement data, falls back to research-backed defaults), and creates
a one-time cron-job.org trigger that fires a GitHub Actions `repository_dispatch` workflow
at the scheduled time.

**Engagement Tracker (`pipeline/engagement_tracker.py`):** Daily GitHub Actions cron fetches
view/like counts from YouTube/Instagram/Facebook APIs for recent uploads, updates
`upload_schedule` rows, and recalculates `time_performance` rolling averages. Adaptive
scheduling kicks in after 3+ samples per slot.

**Google Drive Storage (`pipeline/drive_storage.py`):** Service account auth. Videos go to
`video-uploads/pending/` after approval, moved to `uploaded/` or `failed/` after platform
upload. Weekly cleanup deletes files older than 7 days.

**Deity identification (`pipeline/deity_prompts.py`):** Lazy-loads `deity_prompts.json` (133
entries) into a lookup dict. `find_deity(text, lookup)` uses word-boundary regex, checks longer
keys first to avoid substring collisions. `find_all_variants()` returns all entries for a deity
including avatars and scene variants. `get_avatar_list()` and `find_by_tradition()` support
coverage reports.

**Deity mapping (`pipeline/deity_map.py`):** Central registry bridging `deity_prompts.json` and
`image_library` DB. `find_best_image_for_scene()` is the main entry point — detects deity from
prompt, finds matching library images, ranks by tag overlap, falls back to FTS/tradition/random.
`deity_coverage_report()` shows which deities have images and which are missing.

**Output file naming:** After script generation, a human-readable slug is built:
`{niche_id}_{story-title-kebab}_{db_id}` (title capped 40 chars). Used for all output paths:
- Images: `output/images/{slug}/scene_00.png`, `scene_01.png`, ...
- Audio:  `output/audio/{slug}/voice.mp3`
- Video:  `output/video/{slug}.mp4`
- Script: `output/scripts/{slug}.json`

**Niches:** defined in `settings.json` under `niches[]`. Each entry has `id`, `label`, `tone`,
and `art_style_prompt_suffix`. Adding a new niche = adding a config entry, no code changes.
Mythology has sub-types: `hindu`, `norse`, `egypt`, `greek` (pass via `--myth-type`).

**Config:** `config.py` merges `.env` (API keys) and `settings.json` (all other tunables)
into a single `cfg` singleton. `settings.json` controls video dimensions, TTS voices,
LLM fallback order, niche definitions, and image library settings.

**Database (`output/db/agent.db`):** SQLite, initialized by `db/init_db.py`. Key tables:
- `videos` — one row per video, tracks full status lifecycle
  (`queued → bg_ready → voice_ready → assembled → screened → sent → approved → posted / rejected / permanently_rejected`; also: `waiting_quota`)
- `decisions` — all script and routing decisions logged with reasoning
- `feedback` — manual verdicts from Telegram review (good/bad + tags)
- `quota_usage` — daily LLM quota tracking per provider (groq, cerebras)
- `quota_reset_log` — tracks when quota was last reset per (provider, interval)
- `image_library` — user-provided images with Gemini Vision metadata
- `image_library_fts` — FTS5 virtual table synced to image_library via triggers
- `upload_schedule` — tracks video publishing to platforms (youtube/instagram/facebook) with scheduling, engagement metrics, and A/B caption testing
- `time_performance` — per-niche, per-platform, per-hour engagement averages for adaptive scheduling
- `platform_rotation` — tracks last platform used per niche for round-robin distribution

**Deity prompts file (`deity_prompts.json`):** 133 entries covering major Hindu deities and
their avatars/scenes. Each entry has: deity_name, aliases, tradition, visual_details,
iconography_and_symbols, color_palette, clothing_and_jewelry, scene_title, scene_description,
tags, negative_prompts.

**Image library:** Images must be user-provided. Ingest with `ingest_library.py` before running
the pipeline. Gemini Vision analyzes each image and stores structured metadata (deity name,
tradition, tags, description) for retrieval during video generation.

## Environment

All API keys go in `.env` (see `.env.example`). `GOOGLE_AI_STUDIO_API_KEY` is used for
Gemini (LLM fallback) and for Gemini Vision during image library ingestion.
Ollama must be running locally (`http://localhost:11434`) for the local LLM fallback.

Scheduler keys: `GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON`, `CRONJOB_API_KEY`, `GITHUB_DISPATCH_TOKEN`.
Engagement keys: `INSTAGRAM_ACCESS_TOKEN`, `FACEBOOK_PAGE_ACCESS_TOKEN`.
Image gen: `PEXELS_API_KEY`, `HF_API_TOKEN`.

Output artifacts: `output/images/`, `output/audio/`, `output/video/`, `output/scripts/`.
Database: `output/db/agent.db`. Logs: `output/logs/`.
Provider quota config: `quota.json` (root, LLM providers only).

Key `settings.json` defaults: `video.min_duration_sec` / `max_duration_sec` = 90–180s;
`video.min_scenes` / `max_scenes` = 12–25; `image_library.vision_model` = `gemini-2.0-flash`.

## Project Structure

```
pipeline/
  script_gen.py         — LLM script generation
  image_policy.py       — image source resolution and human-figure blocking
  image_library.py      — DB-backed image retrieval
  image_gen.py          — cloud image generation provider chain
  comfyui_gen.py        — local ComfyUI image generation client
  deity_prompts.py      — deity name lookup from deity_prompts.json
  deity_map.py          — deity-to-image-library bridge
  pexels_library.py     — Pexels stock photo search for non-mythology niches
  tts.py                — local TTS (Edge TTS / Piper / Kokoro)
  audio_bg.py           — background audio fetch and mix
  ffmpeg_assembler.py   — Ken Burns + caption burn + audio mix → mp4
  scene_timing.py       — per-scene duration calculation from audio
  quota_tracker.py      — LLM provider quota tracking
  scheduler.py          — upload scheduling: Drive upload + cron-job.org + GitHub Actions
  engagement_tracker.py — fetch engagement metrics and update time_performance
  drive_storage.py      — Google Drive service account upload/cleanup
  social_accounts.py    — social media account configuration
  social_captions.py    — A/B caption generation for social uploads
  youtube_upload.py     — YouTube Data API v3 video upload
  instagram_upload.py   — Instagram Graph API reel publishing
  facebook_upload.py    — Facebook Graph API video publishing
  prompt_refiner.py     — LLM-based image prompt refinement (optional)
  retry.py              — exponential backoff retry decorator

scripts/
  scheduler_setup.py          — verify scheduler prerequisites
  run_scheduled_upload.py     — execute a scheduled platform upload (called by GitHub Actions)
  run_drive_cleanup.py        — delete old Drive files (called by GitHub Actions)
  run_engagement_fetch.py     — fetch engagement data (called by GitHub Actions)
  upload_youtube.py           — manual YouTube upload CLI
  upload_all_platforms.py     — upload to all platforms at once
  schedule_all_platforms.py   — schedule uploads for all platforms
  youtube_auth_setup.py       — OAuth2 setup for YouTube
  instagram_auth_setup.py     — Instagram auth token setup
  meta_token_exchange.py      — exchange short-lived Meta token for long-lived
  batch_generate.py           — generate multiple videos in batch
  test_image_gen.py           — test image generation providers
  test_prompt_refiner.py      — test prompt refiner
  test_comfyui.py             — test ComfyUI connection

.github/workflows/
  scheduled-upload.yml        — GitHub Actions: execute scheduled platform upload
  engagement-fetch.yml        — GitHub Actions: daily engagement data collection
  drive-cleanup.yml           — GitHub Actions: weekly Drive file cleanup

agent/
  decisions.py                — decision logging helpers
  prescreener.py              — automated video quality prescreening

feedback/
  parser.py                   — parse Telegram feedback into structured tags

worker.py                     — background worker for pipeline execution
wait_for_review.py            — poll for Telegram review verdict
```
