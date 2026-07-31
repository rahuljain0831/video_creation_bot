# Video Creation Agent

Prompt-driven pipeline: pick a niche + optional story seed → produces a 45-90 second vertical (9:16) story video, end to end, with no manual steps.

**Niches:** Mythology · Scary Stories · Heists (add more in `settings.json`, no code changes)

---

## How it works (plain English)

**Step 1 — You pick a topic**
You run the script and choose a category (niche) like "Mythology". Optionally give a story seed like "The story of Shiva and Parvati". If you skip the seed, the AI picks a story.

**Step 2 — AI writes the script**
An LLM (Groq → Cerebras → Google Gemini → local Ollama) writes a structured story broken into 8-15 scenes. Each scene gets: narration text (what the narrator says) and an image description (what picture should appear). All decisions are saved to the database *before* any images are looked up.

**Step 3 — Best matching image is selected per scene**
For each scene, the system identifies the deity from the image description, queries your image library, and picks the best match using tag overlap. If no deity is detected, it falls back to full-text search, then tradition filter, then random. No AI image generation — images come from your curated library.

**Step 4 — Text is spoken aloud**
All narration text is joined and converted to speech (Edge TTS → Piper → Kokoro).

**Step 5 (optional) — Background music is added**
If enabled, a chanting/meditation background track is mixed in at low volume.

**Step 6 — Video is assembled**
ffmpeg applies a slow Ken Burns pan/zoom to each image, concatenates them in scene order, burns captions at the bottom, and mixes in audio. Output: 9:16 portrait MP4 for TikTok/Reels/Shorts.

**Step 7 — Video goes to Telegram for review**
The finished video is sent to a Telegram bot. You watch it, tap good/bad. The verdict is saved alongside all generation decisions for later analysis.

---

## Output file naming

```
output/images/mythology_story-of-shiva_42/
    scene_00.png
    scene_01.png
    ...
output/audio/mythology_story-of-shiva_42/
    voice.mp3
output/video/
    mythology_story-of-shiva_42.mp4
output/scripts/
    mythology_story-of-shiva_42.json
```

Slug format: `{niche_id}_{story-title-kebab}_{db_id}`. Title capped at 40 chars.

---

## Setup

```bash
git clone <repo>
cd video-creation-agent

pip install -r requirements.txt
cp .env.example .env       # fill in your API keys
python db/init_db.py       # create SQLite DB at output/db/agent.db

# Ingest your deity images (REQUIRED before running the pipeline)
python ingest_library.py --folder /path/to/your/images --tradition hindu
```

**ffmpeg required** — install separately and ensure it's on PATH.

### API keys (`.env`)

| Key | Used for |
|---|---|
| `GROQ_API_KEY` | LLM script generation (primary) |
| `CEREBRAS_API_KEY` | LLM (fallback) |
| `GOOGLE_AI_STUDIO_API_KEY` | LLM fallback + Gemini Vision (image analysis during ingest) |
| `TELEGRAM_BOT_TOKEN` | Review bot |
| `TELEGRAM_CHAT_ID` | Review bot |

**Local LLM fallback:** if all online LLM providers fail, Ollama is used. Requires Ollama running at `http://localhost:11434`.

---

## Image Library Setup

The pipeline uses **your provided images** — no AI image generation. You supply the images; the system identifies and retrieves them intelligently.

### Ingest images

```bash
# Analyze and store all images in a folder
python ingest_library.py --folder /path/to/deity_images

# With tradition hint (speeds up Gemini Vision analysis)
python ingest_library.py --folder /path/to/hindu_images --tradition hindu

# Dry-run: show what would be ingested without writing to DB
python ingest_library.py --folder /path/to/images --dry-run
```

Each image is analyzed by Gemini Vision, which extracts:
- Deity name (e.g. "Shiva", "Ganesha", "Durga")
- Tradition (hindu / greek / norse / egyptian / buddhist / other)
- Full description (pose, setting, mood)
- Tags (e.g. trident, blue-skin, seated, temple, four-armed)

### Check coverage

```bash
python list_library.py                    # all indexed deities + image count
python list_library.py --missing          # deities in deity_prompts.json with no images
python list_library.py --deity Shiva      # all images for Shiva
python list_library.py --report           # full coverage report
```

### Deity prompts

`deity_prompts.json` contains 133 canonical Hindu deity entries with visual descriptions, iconography, aliases, and scene variants. Used to identify deities in LLM-generated scene prompts and match them to your library images.

---

## Usage

```bash
# Interactive niche menu
python run_niche.py

# Niche by id
python run_niche.py mythology

# Niche + story seed
python run_niche.py mythology "The birth of Ganesha"
python run_niche.py scary_stories "the house that watches back"
python run_niche.py heists "the museum job that almost worked"

# Script only — no image lookup, no video assembly
python run_niche.py mythology --dry-run

# Assemble video but skip Telegram
python run_niche.py mythology --no-telegram

# Mythology sub-type (uses tradition-specific art style tone)
python run_niche.py mythology --myth-type hindu
python run_niche.py mythology --myth-type greek
```

---

## Configuration

All tunables are in `settings.json`:

| Setting | What it controls |
|---|---|
| `niches[]` | Available niches, tone, art style suffix |
| `video.resolution` | Output resolution (default `[1080, 1920]` for 1080p 9:16) |
| `video.min_scenes` / `max_scenes` | Scene count range (default 8-15) |
| `video.min_duration_sec` / `max_duration_sec` | Target video length (default 45-90s) |
| `video.caption_style` | Subtitle font size, alpha, bottom margin |
| `llm_router` | LLM provider fallback order |
| `tts.provider_priority` | TTS provider fallback order |
| `background_audio.enabled` | Toggle background chanting music |
| `image_library.vision_model` | Gemini model for image analysis (default: gemini-1.5-flash) |
| `image_library.store_dir` | Where analyzed images are stored |

### Adding a niche

```json
{
  "id": "space_exploration",
  "label": "Space Exploration",
  "tone": "awe-inspiring, curious",
  "art_style_prompt_suffix": "cinematic space photography, deep black void, vivid nebula colors, photorealistic"
}
```

Add to `niches[]` in `settings.json`. Available immediately.

---

## Quota tracking

LLM providers (Groq, Cerebras) have daily call limits tracked in SQLite:

- **Pre-call check:** if today's usage is at the limit, skip that provider and try the next
- **Post-call log:** every attempt is recorded with timestamp and error code
- **Daily reset:** at startup, if a provider's reset interval has passed, its counter is cleared

Limits are configured in `quota.json`. Edit `daily_limit` to adjust.

---

## Project structure

```
run_niche.py              — main entry point, orchestrates the full pipeline
config.py                 — merges .env + settings.json into cfg singleton
settings.json             — all tunables (niches, video config, LLM order, library settings)
quota.json                — LLM provider daily limits and reset schedules
llm_router.py             — LLM fallback chain: Groq → Cerebras → Gemini → Ollama
ingest_library.py         — CLI to analyze and store deity images into the library
list_library.py           — CLI to inspect library coverage by deity

pipeline/
  script_gen.py           — LLM story script generation (narration + image prompt per scene)
  image_library.py        — image ingestion, FTS search, deity-name search, retrieval
  deity_map.py            — central deity registry: maps prompts to best library images
  deity_prompts.py        — 133-entry deity lookup: aliases, visual identity, tag matching
  tts.py                  — text-to-speech: Edge TTS → Piper → Kokoro
  audio_bg.py             — background audio fetch and mixing (chanting/meditation)
  ffmpeg_assembler.py     — Ken Burns effect + concat + captions + audio mix → mp4
  quota_tracker.py        — pre/post LLM API call quota checks and daily reset logic

review/
  telegram_bot.py         — send finished video for human review via Telegram

db/
  schema.sql              — SQLite schema (videos, decisions, quota_usage, image_library, etc.)
  init_db.py              — DB init + safe migrations + FTS5 virtual table setup

tests/                    — pytest test suite
verify_keys.py            — check all API keys are valid
deity_prompts.json        — 133 Hindu deity entries with visual descriptions and scene prompts
```

---

## Video status lifecycle

```
queued
  → (LLM runs) → script logged to decisions table
  → bg_ready        images selected from library
  → voice_ready     TTS audio generated
  → assembled       ffmpeg assembled mp4
  → sent            sent to Telegram
  → approved / rejected    human verdict via Telegram
```

---

## Feedback loop

Manual review via Telegram (good/bad verdict). Verdicts stored in SQLite alongside all generation decisions — niche, story seed, LLM model used, image matched. After a batch, query `output/db/agent.db` to find which combinations produce the best results.
