# Pivot Notes — Read Before Touching Code

This project pivoted twice. This doc is the source of truth for what's current.
`design-v3.md` and parts of the existing code reflect an OLDER plan — do not trust
them where they conflict with this file.

## What changed

**Old plan:** free-text prompt in → any topic → 15-20s video, 1-2 scenes,
AI video-gen per scene (Veo/Kling), quote-style motivational content with
dedup via embeddings.

**Current plan:** menu-driven niche selection (no free-text prompt) → 45-90s
story-style video, 8-15 scenes, static AI-generated images with pan/zoom
(Ken Burns), NOT video-gen per scene.

## Why

- Free-text "any topic" was too broad to keep quality/tone consistent.
- Modeled instead on facelessreels.com: fixed niche menu, narrative story format.
- Static images are far cheaper than video-gen per scene at 8-15 scenes/video,
  and easier to hold a consistent art style across.
- Quote/motivational content dropped entirely — no dedup, no quote logic needed.

## Locked decisions

- **Niches (start with 3):** Mythology, Scary Stories, Heists. Defined in
  `settings.json` under `niches[]` — each has an id, tone, and
  `art_style_prompt_suffix`. Adding a niche = adding a config entry, not new code.
- **Input:** menu selection only. No free-text prompt parsing needed.
- **Duration:** 45-90s per video, 8-15 scenes.
- **Visuals:** static images generated per scene, then pan/zoom (Ken Burns)
  assembled via ffmpeg/MoviePy. No video-gen API calls anywhere in this pipeline.
- **Image consistency:** style held via same art-style-suffix per niche +
  fixed seed reuse (HF, Pollinations) or reference-image conditioning
  (Google AI Studio/Gemini, since it has no seed param). See
  `image_provider` block in `settings.json`.
- **Image provider fallback chain:** Hugging Face (FLUX.1-schnell, seed-based)
  → Google AI Studio (Gemini/Nano Banana, reference-based) → Pollinations
  (seed-based, zero-friction last resort).
- **Volume:** start at 1/day, architecture should not block scaling to 10/day.
  NOT the 100/day target from the old plan — that was for the quote-video
  concept and no longer applies.
- **Series (recurring generation):** not built yet. Single video per run for
  now, but niche/style config must stay data-driven (already is, in
  `settings.json`) so a scheduler can be added later without a rewrite.
- **What carries over unchanged:** Phase 0 setup, SQLite storage, Telegram
  review bot pattern, local TTS (Edge/Piper/Kokoro), LLM routing via
  Groq/Cerebras/Gemini/Ollama fallback, agentic per-batch decision logging.

## Dead code / remove

The following exist from the old plan and should be deleted, not adapted,
since they solve problems that no longer exist:

- Any **quote deduplication** logic (embedding similarity checks,
  `nomic-embed-text` usage, quote dedup threshold config) — no quotes
  anymore, nothing to dedup.
- Any **clip library builder** (`library_builder/` directory and its
  contents) — this built/tagged a library of stock video clips for the
  Ken-Burns-on-video-clips approach. Replaced entirely by per-scene
  AI image generation. Delete the directory.
- Any **Ken Burns on stock/library video clips** fallback logic tied to the
  old `background` config block (already removed from `settings.json`).
  Ken Burns itself stays, but now applies to AI-generated static images,
  not library video clips — so the clip-selection logic goes, the
  pan/zoom ffmpeg logic can likely be reused/adapted.
- Any **free-text prompt parsing / tone auto-detection from arbitrary text**
  — tone is now fixed per niche in config, not inferred from a prompt.
- Check `agent/`, `pipeline/`, `worker.py`, `run_prompt.py` for references
  to any of the above and flag before deleting if unsure whether something
  is still load-bearing.

## What to do first

1. Read this file, `settings.json` (current), and `design-v3.md` (outdated —
   note where it conflicts with this file).
2. Do NOT start editing code yet. First produce a written file-by-file
   update plan: which files get deleted, which get modified, which are new.
3. Present that plan for review before making changes.
