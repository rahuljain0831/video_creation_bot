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

# Remotion (scary_stories niche)
cd remotion-scary && npm ci              # one-time; needed before the first scary render
npx remotion browser ensure              # one-time; pre-downloads Chrome Headless Shell
npm run dev                              # Remotion Studio
npm run render:sample                    # render src/sample-props.json, no Python involved
cd .. && python scripts/test_remotion_render.py --preflight      # check node/deps wiring
python scripts/test_remotion_render.py --write-sample            # regen sample from the real builder
python scripts/test_remotion_render.py --slug <run_slug>         # re-render a run, no LLM/TTS

# Compare narration voices (writes .scratch_img/voices/, changes nothing)
python scripts/compare_voices.py                     # the niche's whole pool
python scripts/compare_voices.py --prosody           # with per-beat delivery

# Audit generated images with the local vision model (needs: ollama pull qwen2.5vl:3b)
python scripts/critique_run.py --latest              # audit the most recent run
python scripts/critique_run.py --latest --regenerate # redo scenes that missed their subject
python scripts/critique_run.py --notes               # what the critic has learned so far

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
   - Niches with `allow_local_generation: false` never run local generation.
     `resolve_image_source()` downgrades a local-only source to `library`;
     `comfyui_gen` and `image_gen` raise `LocalGenerationBlocked`. No niche sets
     this today — mythology used to, until the 8-image library proved too thin
     for a 12–25 scene video.
   - **Human policy is graded per niche** via `human_policy`, resolved by
     `resolve_human_policy()` and applied by `apply_human_policy()`:
     - `"never"` (default) — no people at all. Human-referring comma chunks are
       stripped from the positive and pushed into the negative.
     - `"obscured"` (**scary_stories**) — figures are welcome, faces and hands
       are not. Only face/hand chunks are stripped; the positive gains "seen
       from behind, face not visible, hands out of frame".
     - `"none"` (**mythology**) — no restriction. `_HUMAN_WORDS` covers
       `god`/`goddess`/`deity`/`face`/`arms`, so any stripping here empties the
       temple of the deity the scene is about.

     What betrays a generated image is distorted **faces and hands**, not the
     presence of a person — so the blanket ban was aimed at the wrong target and
     cost every scary story its most useful subject. The legacy `no_humans`
     boolean (global `image_gen.no_humans`, or per-niche) still maps to
     `"never"`/`"none"`, so nothing predating this changed.

   **`pipeline/image_gen.py`** — provider chain read from `image_keys.json`
   (top-to-bottom = priority, same shape as `llm_keys.json`): gemini →
   together_ai → huggingface → pollinations (keyless), then local ComfyUI.
   An empty `api_key` falls back to that provider's env vars before it is
   skipped (`_PROVIDER_ENV_MAP`, several names each).
   Verify with `python scripts/test_image_gen.py [--live]`.

   **One intent, serialized per provider.** `build_positive_prompt()` renders
   the same scene differently depending on who is answering, so that when a free
   tier runs out mid-video the next provider is asked for the same picture in
   the dialect it listens to:
   - *FLUX family* (pollinations / huggingface / together_ai) — **look first,
     then short camera phrase, then the subject at full length**, capped near 60
     words. They truncate around 77 CLIP tokens.
   - *Gemini* — reads the whole prompt, so it also gets `art_style_prompt_suffix`
     and the long-form camera phrases.
   - *Pollinations* has **no negative-prompt channel at all**; its constraints
     are folded into the positive as short affirmative phrases
     (`FLUX_CONSTRAINTS`). Passing the full negative list there is what turned a
     55-word prompt into 111 words and lost the subject entirely.

   These shapes are measured, not guessed — six prompt structures at one seed.
   The finding: **length is the variable, not order.** A ~30-word look block
   wins the frame and destroys the subject; an ~11-word block holds both, and
   moving it before or after the subject barely matters. Keep `_LOOKS` short —
   `tests/test_prompt_assembly.py` enforces the ceiling.

   `build_style_token(video_id)` pins one look per video so twelve stills read
   as one piece of footage; `seed` is per-scene and derived from the run, so
   reruns reproduce.

   **`pipeline/image_critic.py` + `pipeline/prompt_notes.py` — the learning loop.**
   Runs **after** a video is built, never during it: a generated image is
   accepted and the pipeline moves on, and the critic audits the results
   afterwards. Findings go to `prompt_notes.json`, and `build_positive_prompt()`
   appends the recurring ones (2+ hits) to the next run's prompt for that
   `(niche, shot)`. So prompts improve across runs without anyone tuning them,
   and a bad image never stalls a video.

   Uses a **local** Ollama vision model (`qwen2.5vl:3b`) — the cloud vision
   quota is the same quota image generation needs. Two rules learned the hard
   way, both pinned in comments because both silently produce false passes:
   - The image is **downscaled to 768px** first. At full 1080×1920 a critique
     takes ~83s on CPU; at 768px it takes ~15s with identical answers.
   - The model is **never asked whether the image matches the brief**, and is
     never shown the brief. It is good at describing and bad at comparing —
     shown a corridor that should have been a tablet displaying a porch feed, it
     described the corridor correctly and then still said the subject was
     present. So it only reports what it sees, and `subject_overlap()` does the
     comparison in Python where it is deterministic.

   Scaffolding for a young pipeline. Set `image_critic.enabled: false` once the
   prompts stabilise; nothing else depends on it.

   **`pipeline/image_post.py`** — `ensure_render_size()` forces every generated
   image to the render resolution with ffmpeg lanczos + unsharp before Remotion
   sees it. Providers ignore the requested size (Pollinations returns 576×1024
   for a 1080×1920 request), and Remotion then overscans by ~1.18× on top, so
   without this the picture is shown at roughly 2.2× and looks soft.

   `pipeline/image_library.py` + `pipeline/deity_map.py` — selects best matching image from
   the user-provided library for each scene.
   Strategy per scene:
     a. Detect deity name in image_prompt via `deity_prompts.find_deity()`
     b. If found: exact DB match on `deity_name` column
     c. Rank multiple matches by tag overlap with scene prompt
     d. Fallback: FTS5 full-text search on prompt keywords
     e. Fallback: tradition filter → random image
   **Procedural niches (`image_source: "procedural"`)** skip step 2 entirely —
   the video renderer draws every frame. `pipeline/image_policy.is_procedural()`.
3. `pipeline/tts.py` — chain is elevenlabs → Edge TTS → Piper → Kokoro per
   `settings.json` priority. **ElevenLabs is dormant**: it skips itself unless
   `ELEVENLABS_API_KEY` is set, which needs a paid plan (the free 10k characters
   are web-UI only), so the effective default is Edge TTS.

   **Two entry points, and the niche's `script_schema` picks between them:**
   - `synthesize(text, ...)` — one call for the whole video. Everything except
     `cinematic_scary` uses it. Writes `voice.mp3`.
   - `synthesize_scenes(scenes, ...)` — one call *per beat*, used by
     `cinematic_scary`. Each beat gets its own prosody from `_PROSODY`, its own
     `_LEAD_SILENCE`, and its own voice treatment. Writes `voice.wav`.

   Why per-beat: one call for the whole script gives every line the same
   delivery, which is why the narration sounded flat regardless of voice. The
   prosody deltas are small on purpose — they compound with the niche's own
   `rate`, and scary_stories already narrates at -15%.

   **What makes a read sound told rather than recited is contrast, not
   slowness.** `line` is nudged *faster* than the niche baseline so the beats
   that slow down have something to be slower than; every beat at the same -15%
   reads flat no matter how low the pitch goes. `_LEAD_SILENCE` covers every
   template, not just `scare` and `end` — a storyteller stops between sentences
   and TTS does not, and beats concatenated with no gap were half of why the
   delivery sounded like recitation. Roughly 3s across a script, which the
   60-75s window absorbs.

   **Voice treatment (`horror_audio.apply_voice_fx()`, niche `tts.voice_fx`).**
   Edge TTS neural voices are clean, close-mic'd and undamaged — right for a
   tutorial, wrong for a ghost story — and prosody knobs cannot fix that: rate
   and pitch change how fast and how low, never *where the voice is*. Three
   layers, per beat, keyed on the template so `scare` is treated harder than
   `line`: body EQ (170 Hz lift, 3 kHz presence cut), a **shadow** (the voice
   pitched down three semitones, low-passed, mixed far underneath — this is the
   whole effect; deeper stops being a shadow and becomes a demon impression),
   and a short dark room.

   **The treatment must not move a word's onset.** `word_timings.json` is
   measured from Edge TTS boundaries *before* it runs, so: no time-stretch on
   the main path, and the output is trimmed to the input's exact duration.
   Verify any change to `_VOICE_PROFILES` with a drift check — it must be
   `0.000000s`. Two ffmpeg traps, both of which produced silent-looking bugs:
   `aecho` is `in_gain:out_gain:delays:decays` and the room level is the LAST
   value (putting it in `out_gain` attenuated the dry voice by 18 dB), and the
   shadow's `asetrate`+`atempo` pair is the only pitch shift ffmpeg has without
   rubberband — fine for one quiet layer, never for the main path.

   Output is **WAV, not MP3**, because the beats are concatenated in the sample
   domain; re-encoding to MP3 would prepend ~46ms of encoder delay that drags
   the narration late against captions and stings. Consumers should accept
   either (see `scripts/test_remotion_render.py`).

   Both paths write `word_timings.json` (100-nanosecond word offsets) — the
   contract captions, shot cuts and the riser all read. The per-scene path
   re-bases each beat's offsets by the running clock, and returns **measured
   per-scene durations**, so `scene_timing` does not have to infer boundaries at
   all on that path.

   `python scripts/compare_voices.py` renders the same three lines through every
   voice in the niche's pool so the choice is made by listening.
4. Video render — `pipeline/renderer_dispatch.get_assembler()` picks per niche on the
   `renderer` key (`"ffmpeg"` default | `"remotion"`). Both entry points take identical kwargs.
   - `pipeline/ffmpeg_assembler.py` — applies Ken Burns (pan/zoom via `zoompan` filter) to each
     image, concatenates clips, burns captions, mixes audio into 9:16 mp4.
     Entry point: `assemble_from_images()`.
   - `pipeline/remotion_renderer.py` — drives the `remotion-scary/` Remotion project as a
     subprocess. See "Remotion renderer" below.
5. `review/telegram_bot.py` — sends video + story metadata for manual review.
6. After Telegram approval, `pipeline/scheduler.py` handles Google Drive upload, platform selection
   (round-robin per niche via `platform_rotation` table), optimal time scheduling (adaptive from
   `time_performance` data), and cron-job.org triggers for GitHub Actions `repository_dispatch`.

**LLM routing (`llm_router.py`):** `call_llm()` walks the cloud providers in `llm_keys.json`
**top-to-bottom** (that file, not `settings.json`, sets the order — `settings.json.llm_router`
only holds `timeout_seconds`), then falls back to local Ollama. Providers with an empty
`api_key` are skipped. Returns `(text, model_used)`.

Ollama model selection: `settings.json.ollama.model` set to `"auto"` queries `/api/tags` and
picks the best installed model — `exclude_patterns` drops embedding/coder/vision models, then
`prefer` ordering wins, then parameter count. Set `model` to an explicit tag to pin it.
Falls back to `fallback_model` when the daemon is unreachable.

**Quota tracking (`pipeline/quota_tracker.py`):** Tracks LLM provider calls.
Pre-call: checks `quota_usage` table against `quota.json` daily_limit. Post-call: logs result.
Daily reset runs at startup. Provider config in `quota.json`.

Every provider in `llm_keys.json` must also appear in `quota.json` — `check_and_log_quota()`
returns early on an unknown provider, so a missing entry means that provider's usage is never
counted. `format_quota_report()` prints a per-provider used/limit/% block, emitted in the
`finally` block of every `run_niche.py` and `retry.py` run (success or failure).

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

**Remotion renderer (`pipeline/remotion_renderer.py` + `remotion-scary/`):**
Used by `scary_stories`. Generated photographic backdrops (`image_source: "generate"`) with a
procedural grade over them — film grain, vignette, flicker — and horror typography on top.
`Atmosphere.tsx` drops its fog and red bleed when an image is present (`transparent` mode);
without that it covers the picture it is supposed to be grading.

*Division of labour: Python owns every number.* Tick→frame conversion, scene boundaries,
transition padding, shot cuts and caption chunking all happen in `build_props()`; the TSX renders
frames it is handed and does no arithmetic. Keeps one source of truth for timing and puts errors
in a Python traceback instead of headless-Chrome stdout.

**Beats and shots (props `schemaVersion: 2`).** A narration line is a *beat* — the unit of
meaning, however long the sentence takes. A beat is split into 1–3 *shots*, the unit of picture.
`plan_shots()` decides: beats under 2.4s stay one shot, under 5.5s cut once, longer cut twice.
Cuts snap to the nearest word start (from the caption frames) so they never land mid-word, and no
shot is under 0.9s. A beat with a second image (`reveal_prompt`, see below) hard-cuts to it;
otherwise the cut is a scale change on the same image (`move: "punch"`), which still reads as a
cut. Net effect: ~2s per shot instead of ~5.3s per scene.

- Composition `ScaryStory` (`remotion-scary/src/scary/ScaryStory.tsx`), zod schema in
  `schema.ts`, dynamic duration via `calculateMetadata` — which **throws** if the scene frames
  don't sum to the narration length, or if a beat's shots don't sum to the beat.
- Six scene templates in `src/scary/templates/`: `hook`, `line`, `impact`, `reveal`, `scare`,
  `end`. The LLM picks one per scene (see `script_schema` below). `line` is the safe default.
- Templates render only the short `accent` as big type; the narration is spoken and shown as
  word-synced captions (`CaptionsLayer.tsx`), so text never appears twice.
- **The two text layers must not collide.** Captions are top-anchored at
  `caption_style.anchor_y_percent` (78%), subtitle-style near the bottom, but no lower: the
  bottom ~15% of a vertical video (below ~1630px of 1920) is covered by the platform's own UI.
  `anchor_y_percent` is the **top** of the caption block, so the real limit is that anchor plus
  the rendered line height — at 78% one line of 72px type ends near 1585px, and a chunk that
  wraps to two lines does not clear the band, which is why `chunk_size` stays at 3.

  `CaptionsLayer.tsx` must position this with `top`, not `padding-top`. Percentage padding —
  vertical included — resolves against the containing block's **width**, so the original
  `paddingTop: "62%"` meant 62% of 1080 = 670px, i.e. 35% of the frame. The captions sat near
  the middle of the picture for as long as that line existed, and the comment above it claimed
  otherwise. `top` percentages resolve against height, which is what was always intended.

  Accents therefore live in the upper third via `AccentStage` in `effects.tsx`;
  use it rather than a per-template flexbox. `accentStyle()` sizes type from its character count
  and gives it a hard stroke, so a long accent cannot overflow and a pale one cannot render
  invisibly over a bright frame.
- Audio, as shipped for scary_stories, is **two** layers: the treated narration and an ambience
  bed under it. `build_soundtrack()` can also place one-shot stings on the impact/reveal/scare/end
  beats, plus a 3s riser before the scare that stops dead with `ambienceGaps` silencing the bed
  into the hit — but `horror_audio.stings` is **false** for this niche and that whole path is
  skipped. The hits are synthesized sweeps and noise bursts, and against a photographic backdrop
  and a treated voice they read as a cartoon sound effect: one landing plays the video as comedy.
  The riser goes with them, since a rise that resolves into nothing is worse than no rise. The
  recipes stay in `horror_audio.py`; flip `stings` back on per niche to use them.
- **The bed has to be audible to count.** It is peak-normalized to -14 dBFS, scaled by
  `ambience_volume`, then ducked again under the voice in `ScaryStory.tsx`. At the original
  0.26 × 0.65 that landed near -29 dBFS — inaudible on a phone, so the bed effectively only
  existed between lines. Now 0.36 × 0.8. `bed_pool` narrows the seeded choice to beds that read
  as *music* (`MUSICAL_BEDS` = `dirge`, `musicbox`) rather than as room tone; `abyss` and
  `heartbeat` are tonal but sit at 40-55 Hz, which a phone speaker does not reproduce. `dirge` is
  a held minor triad — one sustained chord on purpose, since a melody competes with the narration.
- `src/scary/timing.ts` — `beat()` scales with scene length (dramatic structure), `span()` stays
  near wall-clock but clamped (perceptual effects). Scene length is variable, so no literal
  second offsets.
- `TransitionSeries` overlaps transitions, consuming `(n-1)*T` frames. `plan_frames()` pads each
  sequence after the first by `T` so the total lands exactly on the narration length, and passes
  `leadInFrames` so template beats stay locked to the voice rather than to the fade.
- Narration reaches the render via `--public-dir` pointed at `output/audio/{slug}/` — nothing is
  copied, and concurrent runs cannot collide. Props are written next to the mp4 (never in the
  public dir, which gets bundled and served).
- Remotion sets `-movflags faststart` itself for h264; no remux pass is needed.
- The original hand-authored `ScaryVideo` comp is kept under a `Reference` folder in the Studio
  as the visual baseline the templates were lifted from.

**Niches:** defined in `settings.json` under `niches[]`. Each entry has `id`, `label`, `tone`,
and `art_style_prompt_suffix`. Adding a new niche = adding a config entry, no code changes.
Mythology has sub-types: `hindu`, `norse`, `egypt`, `greek` (pass via `--myth-type`).

Optional per-niche keys, all defaulted so existing niches are unaffected:
- `renderer` — `"ffmpeg"` (default) | `"remotion"`
- `image_source` — `library` | `pexels` | `generate` | `comfyui` | `procedural` (no image stage)
- `script_schema` — `"image"` (default, scenes are `{narration, image_prompt}`) |
  `"cinematic_scary"` (scenes are
  `{narration, image_prompt, shot, visual, accent, reveal_prompt, repeat}`)
- `min_scenes` / `max_scenes` / `min_duration_sec` / `max_duration_sec` — override `video.*`.
  **Scene count is the duration lever, not the duration setting.** The prompt states the target
  seconds, but the model cannot estimate spoken length, so the window is only ever hit by
  controlling how many beats it writes. This couples to narration style: once the NARRATOR VOICE
  rules pushed `cinematic_scary` toward fragments, seconds-per-beat fell from ~5.7 to ~4.2 and a
  10-scene script landed at 42s against a 60s floor — nothing warned, the video was just short.
  scary_stories runs 14-17 scenes for that reason. Re-check the arithmetic after any change to
  how the narration is written.
- `remotion` — `{composition_id, project_dir, crf, transition_frames, timeout_sec, keep_artifacts}`
- `caption_style` — merged over `video.caption_style`, so a niche can set its own
  `fontsize` / `chunk_size` / `anchor_y_percent` without changing what the ffmpeg ASS builder
  does for every other niche
- `horror_audio` — `{enabled, bed, bed_pool, ambience_volume, stings, sting_volume,
  narration_volume, riser, riser_volume, riser_gap_frames}`. `bed` pins one recipe; `bed_pool`
  narrows the seeded choice while keeping videos different from each other; `stings: false`
  drops the one-shots, the riser and the bed gaps together.
- `tts.voice_fx` — apply the horror voice treatment per beat. Only meaningful on the
  `cinematic_scary` path, where the beat's template picks the profile.

Switching a niche to Remotion is a `settings.json` edit; no code changes.

**Config:** `config.py` merges `.env` (API keys) and `settings.json` (all other tunables)
into a single `cfg` singleton. `settings.json` controls video dimensions, TTS voices,
LLM fallback order, niche definitions, and image library settings.

**Database (`output/db/agent.db`):** SQLite, initialized by `db/init_db.py`. Key tables:
- `videos` — one row per video. Real happy path is
  `queued → bg_ready → voice_ready → assembled → sent → approved`.
  (`screened` is written only on the `--dry-run` path and is a dead end; `posted` is never set
  from Python. Also valid: `rejected`, `permanently_rejected`, `waiting_quota`.)
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
remotion-scary/         — Remotion project (Node). Procedural horror video renderer.
  src/Root.tsx            — registers ScaryStory (+ the original comps under Reference/)
  src/sample-props.json   — generated by scripts/test_remotion_render.py --write-sample
  src/scary/
    ScaryStory.tsx        — prop-driven composition + calculateMetadata
    schema.ts             — zod props contract
    timing.ts             — beat() / span() helpers for variable scene length
    CaptionsLayer.tsx     — word-synced caption band
    Atmosphere.tsx        — fog / grain / vignette / flicker backdrop
    templates/            — Hook, Line, Impact, Reveal, Scare, End

pipeline/
  script_gen.py         — LLM script generation (schema-driven per niche)
  renderer_dispatch.py  — picks ffmpeg vs remotion per niche
  remotion_renderer.py  — props builder + shot planner + Remotion CLI driver
  image_post.py         — force generated images to the render resolution
  image_critic.py       — local-VLM audit of generated images (post-hoc, non-blocking)
  prompt_notes.py       — accumulated prompt corrections, keyed by (niche, shot)
  tts.py                — narration; per-beat for cinematic_scary, one-shot otherwise
  image_policy.py       — image source resolution and human-figure blocking
  image_library.py      — DB-backed image retrieval
  image_gen.py          — cloud image generation provider chain
  comfyui_gen.py        — local ComfyUI image generation client
  deity_prompts.py      — deity name lookup from deity_prompts.json
  deity_map.py          — deity-to-image-library bridge
  pexels_library.py     — Pexels stock photo search for non-mythology niches
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
  schedule_all_platforms.py   — schedule uploads for all platforms (--start/--per-day/
                                --anchor-first lay a batch out over several days; --dry-run)
  youtube_auth_setup.py       — OAuth2 setup for YouTube
  instagram_auth_setup.py     — Instagram auth token setup
  meta_token_exchange.py      — exchange short-lived Meta token for long-lived
  batch_generate.py           — generate multiple videos in batch (+ social captions JSON)
  batch_mixed.py              — batch across the 5 ffmpeg niches; confirms each run against
                                videos.status (run_niche.py exits 0 on image failure)
  batch_scary.py              — batch scary_stories only (Remotion)
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
