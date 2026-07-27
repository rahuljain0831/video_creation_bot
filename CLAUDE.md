# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# One-time setup
cp .env.example .env          # fill in API keys
python db/init_db.py          # create data/db/agent.db from schema.sql

# Run a video end-to-end
python run_niche.py                              # interactive niche menu
python run_niche.py mythology                    # niche id direct
python run_niche.py mythology "story of Medusa" # niche + story seed
python run_niche.py mythology --dry-run          # script only, no image gen / no quota spend
python run_niche.py mythology --no-telegram      # assemble but skip Telegram send

# Verify LLM router fallback chain
python llm_router.py

# Verify API keys
python verify_keys.py

# Tests
pytest                   # all tests
pytest -m "not slow"     # skip video-writing / network tests
```

## Architecture

**Entry point:** `run_niche.py` — user selects a niche (Mythology / Scary Stories / Heists),
optionally provides a story seed, and the pipeline runs end-to-end.

**Pipeline (in order):**
1. `pipeline/script_gen.py` — LLM generates a structured story script for the chosen niche.
   Tone is NOT auto-detected — it comes from the niche config in `settings.json`.
   Each scene gets `narration` (for TTS) + `image_prompt` (for image gen).
   All decisions are written to the `decisions` DB table **before** any image API is called
   (the quota guardrail pattern).
2. `pipeline/image_gen.py` — generates one AI image per scene.
   Fallback chain: HuggingFace (FLUX.1-schnell, fixed seed) → Google AI Studio (Gemini,
   reference-image conditioning) → Pollinations (fixed seed). A single seed is picked at
   pipeline start and reused for all scenes to maintain visual consistency.
3. `pipeline/tts.py` — local TTS only (Edge TTS → Piper → Kokoro per `settings.json` priority).
4. `pipeline/ffmpeg_assembler.py` — applies Ken Burns (pan/zoom via `zoompan` filter) to each
   image, concatenates clips, burns captions, mixes audio into 9:16 mp4.
   Entry point: `assemble_from_images()`.
5. `review/telegram_bot.py` — sends video + story metadata for manual review.

**LLM routing (`llm_router.py`):** `call_llm()` tries Groq → Cerebras → Google AI Studio →
Ollama local in order. Provider order is configured in `settings.json` under `llm_router`.
Returns `(text, model_used)`.

**Niches:** defined in `settings.json` under `niches[]`. Each entry has `id`, `label`, `tone`,
and `art_style_prompt_suffix`. Adding a new niche = adding a config entry, no code changes.

**Config:** `config.py` merges `.env` (API keys) and `settings.json` (all other tunables)
into a single `cfg` singleton. `settings.json` controls video dimensions, TTS voices,
LLM fallback order, image provider priority, and niche definitions.

**Database (`data/db/agent.db`):** SQLite, initialized by `db/init_db.py`. Key tables:
- `videos` — one row per video, tracks full status lifecycle (`queued → assembled → sent → approved/rejected`)
- `decisions` — all script and routing decisions logged with reasoning before quota is spent
- `feedback` — manual verdicts from Telegram review (good/bad + tags)
- `quota_usage` — daily quota tracking per provider

**Image generation constraint:** Local image generation is not a concern (images are small).
But **local video generation is not possible** (laptop has 4GB VRAM; video models need 8GB+).
The pipeline uses static AI images + Ken Burns, so no video-gen API calls are made at all.

## Environment

All API keys go in `.env` (see `.env.example`). `GOOGLE_AI_STUDIO_API_KEY` is reused for
both Gemini (LLM fallback) and Google AI Studio image generation.
Ollama must be running locally (`http://localhost:11434`) for the local LLM fallback.

Output artifacts: images → `data/images/`, audio → `data/audio/`, video → `data/output/`.
Logs → `logs/`.
