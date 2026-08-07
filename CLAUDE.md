# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# One-time setup
cp .env.example .env          # fill in API keys
python db/init_db.py          # create output/db/agent.db from schema.sql

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

# Instagram setup (one-time)
# 1. Create Meta App at https://developers.facebook.com/
# 2. Get short-lived token from Graph API Explorer
# 3. Exchange for long-lived token:
python scripts/meta_token_exchange.py reels_creator <short_token> <app_id> <app_secret> <page_id> <ig_business_id>
# 4. Update social_config.json with account details
```

## Architecture

**Entry point:** `run_niche.py` — user selects a niche (Mythology / Scary Stories / Heists),
optionally provides a story seed, and the pipeline runs end-to-end.

**Pipeline (in order):**
1. `pipeline/script_gen.py` — LLM generates a structured story script for the chosen niche.
   Tone comes from niche config in `settings.json`. Each scene gets `narration` (for TTS)
   and `image_prompt` (for library lookup). All decisions are written to the `decisions` DB
   table before any image lookup runs.
2. `pipeline/image_library.py` + `pipeline/deity_map.py` — selects best matching image from
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
6. `review/telegram_bot.py` — sends video + story metadata for manual review. After approval,
   users can select platforms to post to (Instagram Reels, TikTok coming soon).

**LLM routing (`llm_router.py`):** `call_llm()` tries Groq → Cerebras → Google AI Studio →
Ollama local in order. Provider order is configured in `settings.json` under `llm_router`.
Returns `(text, model_used)`.

**Quota tracking (`pipeline/quota_tracker.py`):** Tracks LLM provider calls (groq, cerebras).
Pre-call: checks `quota_usage` table against `quota.json` daily_limit. Post-call: logs result.
Daily reset runs at startup. Provider config in `quota.json`.

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
  (`queued → bg_ready → voice_ready → assembled → sent → approved/rejected → posted`)
- `instagram_posts` — Instagram Reels upload tracking (media_id, post_url, retry_count, status)
- `decisions` — all script and routing decisions logged with reasoning
- `feedback` — manual verdicts from Telegram review (good/bad + tags)
- `quota_usage` — daily LLM quota tracking per provider (groq, cerebras)
- `quota_reset_log` — tracks when quota was last reset per (provider, interval)
- `image_library` — user-provided images with Gemini Vision metadata
- `image_library_fts` — FTS5 virtual table synced to image_library via triggers

**Deity prompts file (`deity_prompts.json`):** 133 entries covering major Hindu deities and
their avatars/scenes. Each entry has: deity_name, aliases, tradition, visual_details,
iconography_and_symbols, color_palette, clothing_and_jewelry, scene_title, scene_description,
tags, negative_prompts.

**Image library:** Images must be user-provided. Ingest with `ingest_library.py` before running
the pipeline. Gemini Vision analyzes each image and stores structured metadata (deity name,
tradition, tags, description) for retrieval during video generation.

**Social Media Integrations:**
- `pipeline/instagram_auth.py` — loads credentials from `credentials/{account_id}.json`
- `pipeline/instagram_quota.py` — enforces daily upload limits (25/day for Instagram)
- `pipeline/instagram_upload.py` — async Meta Graph API v21.0 integration for Reels upload
- `social_config.json` — platform registry with accounts, limits, and API settings
- Integration: After Telegram approval, users can tap "📸 Post to Instagram" to auto-upload

**Platform captions (`pipeline/social_captions.py`):** Single LLM call generates captions +
hashtags for all 6 platforms (YouTube, Instagram, Facebook, TikTok, Pinterest, LinkedIn).
Instagram gets 30 hashtags, casual 100-150 char caption, optimized for Reels discovery.

## Environment

All API keys go in `.env` (see `.env.example`). `GOOGLE_AI_STUDIO_API_KEY` is used for
Gemini (LLM fallback) and for Gemini Vision during image library ingestion.
Ollama must be running locally (`http://localhost:11434`) for the local LLM fallback.

Output artifacts: `output/images/`, `output/audio/`, `output/video/`, `output/scripts/`.
Database: `output/db/agent.db`. Logs: `output/logs/`.
Provider quota config: `quota.json` (root, LLM providers only).
