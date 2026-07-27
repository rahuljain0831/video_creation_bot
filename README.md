# Video Creation Agent

Prompt-driven pipeline that takes a niche selection and optional story seed → produces a 45-90 second vertical (9:16) story video, end to end.

**Niches:** Mythology · Scary Stories · Heists (add more in `settings.json`, no code changes)

## How it works

```
niche selection + story seed
  → LLM writes 8-15 scene story script (narration + image prompt per scene)
  → AI image generated per scene (HF FLUX → Google AI Studio → Pollinations)
  → TTS narration (Edge TTS → Piper → Kokoro)
  → ffmpeg: Ken Burns effect per image → concat → captions → audio mix → 9:16 mp4
  → Telegram: send for human review
```

Every LLM decision (story title, model used) is logged to SQLite **before** any image API is called — lets you catch a bad generation before spending quota.

## Setup

```bash
git clone https://github.com/rahuljain0831/video_creation_bot
cd video_creation_bot

pip install -r requirements.txt

cp .env.example .env       # fill in your API keys
python db/init_db.py       # create SQLite DB
```

**ffmpeg required** — install separately and ensure it's on PATH.

### API keys (`.env`)

| Key | Used for |
|---|---|
| `GROQ_API_KEY` | LLM (primary) |
| `CEREBRAS_API_KEY` | LLM (fallback) |
| `GOOGLE_AI_STUDIO_API_KEY` | LLM fallback + image generation |
| `HF_API_TOKEN` | Image gen — FLUX.1-schnell (primary) |
| `TELEGRAM_BOT_TOKEN` | Review bot |
| `TELEGRAM_CHAT_ID` | Review bot |

All keys are optional — the pipeline falls back gracefully. Minimum viable setup: one LLM key + one image provider key.

**Local LLM fallback:** if all online LLM providers are unavailable, Ollama is used (`ollama/llama3.1:8b`). Requires Ollama running at `http://localhost:11434`.

## Usage

```bash
# Interactive niche menu
python run_niche.py

# Niche by id
python run_niche.py mythology

# Niche + story seed
python run_niche.py mythology "Perseus and the Gorgon"
python run_niche.py scary_stories "the house that watches back"
python run_niche.py heists "the museum job that almost worked"

# Script only — no image gen, no quota spent
python run_niche.py mythology --dry-run

# Assemble video but skip Telegram
python run_niche.py mythology --no-telegram
```

## Configuration

All tunables are in `settings.json` — no code changes needed for common adjustments:

| Setting | What it controls |
|---|---|
| `niches[]` | Available niches, tone, art style suffix per niche |
| `video.min_scenes` / `max_scenes` | Scene count range (default 8-15) |
| `video.min_duration_sec` / `max_duration_sec` | Target video length (default 45-90s) |
| `llm_router` | LLM provider fallback order |
| `image_provider.priority` | Image gen provider fallback order |
| `image_provider.seed_pool_size_per_niche` | Seed pool for visual consistency |
| `tts.provider_priority` | TTS provider fallback order |

### Adding a niche

```json
{
  "id": "space_exploration",
  "label": "Space Exploration",
  "tone": "awe-inspiring, curious",
  "art_style_prompt_suffix": "cinematic space photography, deep black void, vivid nebula colors, photorealistic"
}
```

Add to `niches[]` in `settings.json`. Available immediately, no code changes.

## Project structure

```
run_niche.py              — main entry point
pipeline/
  script_gen.py           — LLM story script generation
  image_gen.py            — per-scene AI image generation
  tts.py                  — text-to-speech synthesis
  ffmpeg_assembler.py     — Ken Burns + concat + audio mix
review/
  telegram_bot.py         — send video for human review
db/
  schema.sql              — SQLite schema
  init_db.py              — DB init + migrations
llm_router.py             — LLM fallback chain (Groq → Cerebras → Gemini → Ollama)
config.py                 — merges .env + settings.json into cfg singleton
settings.json             — all tunables
```

## Hardware note

Local video generation is not possible on this machine (4GB VRAM; video models need 8GB+). The pipeline uses static AI images + Ken Burns effect instead — cheaper, faster, and visually consistent within a niche.

## Feedback loop

Manual review via Telegram (good/bad verdict). Verdicts stored in SQLite alongside all generation decisions — niche, story seed, LLM model used, image provider used. After a batch of videos, query the DB to find which combinations produce the best results and update `settings.json` routing accordingly.
