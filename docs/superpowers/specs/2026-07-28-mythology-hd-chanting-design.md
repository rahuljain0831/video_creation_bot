# Mythology HD Images + Chanting Background Audio

**Date:** 2026-07-28
**Status:** Approved

## Goal

Improve video quality for the mythology niche (any tradition — Hindu, Norse, Greek, Roman, Egyptian) via:
1. Higher-resolution source images with more inference steps
2. Light background chanting/meditation audio mixed under narration

## Scope

6 touch-points — no new niches, no schema changes, no new CLI flags.

---

## Section 1: Image Quality

### Config (`settings.json`)

Add new top-level block:

```json
"image_gen": {
  "huggingface": { "width": 1024, "height": 1792, "num_inference_steps": 28 },
  "pollinations": { "width": 1080, "height": 1920 }
}
```

### `pipeline/image_gen.py`

- Read `cfg.image_gen.huggingface.width/height/num_inference_steps` instead of module-level constants `_HF_WIDTH=768`, `_HF_HEIGHT=1344`
- Fall back to hard-coded values if config keys absent (backwards compat)
- Read `cfg.image_gen.pollinations.width/height` for Pollinations URL params
- Model stays `black-forest-labs/FLUX.1-schnell` (ungated); quality gain comes from higher res + more steps (4 → 28)

Result: HF images 768×1344@4steps → 1024×1792@28steps. Pollinations full 1080×1920.

---

## Section 2: Background Audio Module

### New file: `pipeline/audio_bg.py`

Single public function:

```python
def fetch_bg_audio(query: str, duration_secs: float) -> str | None:
    ...
```

Flow:
1. Derive cache key: `slugify(query)` → `data/audio/bg/{slug}.mp3`
2. Return cached path if file exists (permanent cache, no TTL)
3. Call Pixabay Music API: `GET https://pixabay.com/api/music/?key=KEY&q={query}&category=meditation`
4. Download first result MP3 to cache path
5. Return path, or `None` on any error (network failure, missing key, empty results)

Failure is always silent — caller degrades gracefully, video assembles without bg audio.

### Config (`settings.json`)

```json
"background_audio": {
  "enabled": true,
  "provider": "pixabay",
  "query": "chanting meditation",
  "volume": 0.12
}
```

### `.env`

```
PIXABAY_API_KEY=your_key_here
```

Free registration at pixabay.com. 1000 API requests/day limit.

---

## Section 3: FFmpeg Assembler

### `pipeline/ffmpeg_assembler.py`

`assemble_from_images()` gains new optional param:

```python
def assemble_from_images(..., bg_audio_path: str | None = None) -> str:
```

When `bg_audio_path` is not None, replace simple audio mux with `filter_complex`:

```
[2:a]volume=0.12,aloop=loop=-1:size=2e+09,atrim=duration=VIDEO_DURATION[bg];
[1:a][bg]amix=inputs=2:duration=first:dropout_transition=2[aout]
```

- Chanting loops to exact video duration via `aloop` + `atrim`
- `amix duration=first` — mix stops when narration ends
- `dropout_transition=2` — 2-second fade on mix end
- Volume read from `cfg.background_audio.volume` (default `0.12`)
- `bg_audio_path=None` → existing code path, no change

---

## Section 4: Orchestration (`run_niche.py`)

New step 3.5 inserted between TTS and assembly:

```python
# Step 3.5 — background audio fetch
bg_audio_path = None
if not args.dry_run and cfg.background_audio.get("enabled", False):
    bg_audio_path = fetch_bg_audio(
        query=cfg.background_audio.query,
        duration_secs=total_narration_duration,
    )

# Step 4 — assembly
assemble_from_images(..., bg_audio_path=bg_audio_path)
```

`--dry-run` naturally skips bg fetch (no quota spend, no network calls).

---

## Files Changed

| File | Change |
|------|--------|
| `settings.json` | Add `image_gen` + `background_audio` blocks |
| `.env.example` | Add `PIXABAY_API_KEY` |
| `pipeline/image_gen.py` | Read dims/steps from config |
| `pipeline/audio_bg.py` | New — Pixabay fetch + cache |
| `pipeline/ffmpeg_assembler.py` | `bg_audio_path` param + filter_complex |
| `run_niche.py` | Step 3.5 bg audio fetch |

## Out of Scope

- Per-niche background audio query override (can add later via niche config)
- Audio normalization / EQ
- Multiple bg track rotation
- New CLI flags
- DB schema changes
