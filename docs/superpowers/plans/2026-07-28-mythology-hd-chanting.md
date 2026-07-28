# Mythology HD Images + Chanting Background Audio — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade mythology videos with 1024×1792 images at 28 inference steps and Pixabay-sourced chanting audio mixed under narration.

**Architecture:** Config changes expose image dims/steps and audio settings; `pipeline/audio_bg.py` (new) fetches+caches Pixabay music; `ffmpeg_assembler.py` gains a `bg_audio_path` param that switches to `filter_complex` mixing; `run_niche.py` inserts a fetch step between TTS and assembly.

**Tech Stack:** Python 3.11+, requests, ffmpeg/ffprobe, Pixabay Music API (free tier, 1000 req/day), HuggingFace Inference API (FLUX.1-schnell), pytest.

## Global Constraints

- Never break the existing pipeline when `bg_audio_path=None` or `PIXABAY_API_KEY` is unset — degrade silently, video still works.
- All new config keys read via `_settings.get(...)` with safe defaults — no KeyError on existing installs.
- `--dry-run` must skip all network calls including Pixabay fetch.
- HuggingFace model stays `black-forest-labs/FLUX.1-schnell` (ungated).
- FFmpeg filter tested locally — `aloop` + `amix` require ffmpeg ≥ 4.4.

---

### Task 1: Config — add `image_gen`, `background_audio`, `PIXABAY_API_KEY`

**Files:**
- Modify: `settings.json`
- Modify: `config.py:27-37`
- Modify: `.env.example`

**Interfaces:**
- Produces: `cfg.image_gen` dict with keys `huggingface.width`, `huggingface.height`, `huggingface.num_inference_steps`; `cfg.background_audio` dict with keys `enabled`, `query`, `volume`; `cfg.PIXABAY_API_KEY` string.

- [ ] **Step 1: Add blocks to `settings.json`**

Insert after the closing `}` of the `"image_provider"` block (before `"review"`):

```json
  "image_gen": {
    "huggingface": {
      "width": 1024,
      "height": 1792,
      "num_inference_steps": 28
    },
    "pollinations": {
      "width": 1080,
      "height": 1920
    }
  },
  "background_audio": {
    "enabled": true,
    "provider": "pixabay",
    "query": "chanting meditation",
    "volume": 0.12
  },
```

- [ ] **Step 2: Expose new sections in `config.py`**

In `config.py`, after the existing `image_provider` line (line 36), add:

```python
    PIXABAY_API_KEY: str = os.getenv("PIXABAY_API_KEY", "")

    image_gen        = _settings.get("image_gen", {})
    background_audio = _settings.get("background_audio", {})
```

- [ ] **Step 3: Add key to `.env.example`**

Append to `.env.example`:

```
PIXABAY_API_KEY=           # free at pixabay.com — needed for background chanting audio
```

- [ ] **Step 4: Verify config loads**

```bash
python -c "from config import cfg; print(cfg.image_gen); print(cfg.background_audio); print(repr(cfg.PIXABAY_API_KEY))"
```

Expected: prints dicts with huggingface/pollinations keys and background_audio keys. `PIXABAY_API_KEY` is `""` if not set in `.env`.

- [ ] **Step 5: Commit**

```bash
git add settings.json config.py .env.example
git commit -m "feat: add image_gen and background_audio config blocks"
```

---

### Task 2: Image quality — read dims/steps from config in `image_gen.py`

**Files:**
- Modify: `pipeline/image_gen.py:40-43` (constants), `pipeline/image_gen.py:54-79` (`_hf_generate`), `pipeline/image_gen.py:259` (pollinations call site)
- Test: `tests/test_image_gen_config.py`

**Interfaces:**
- Consumes: `cfg.image_gen` from Task 1.
- Produces: `_hf_generate(prompt, seed, hf_token, width, height, num_inference_steps)` — new signature with explicit params.

- [ ] **Step 1: Write failing test**

Create `tests/test_image_gen_config.py`:

```python
"""Test that image_gen reads resolution/steps from cfg.image_gen."""
import types
from unittest.mock import patch, MagicMock
import pytest


def _make_cfg(hf_w=1024, hf_h=1792, hf_steps=28, poll_w=1080, poll_h=1920):
    cfg = MagicMock()
    cfg.HF_API_TOKEN = "tok"
    cfg.GOOGLE_AI_STUDIO_API_KEY = ""
    cfg.image_provider = {}
    cfg.image_gen = {
        "huggingface": {"width": hf_w, "height": hf_h, "num_inference_steps": hf_steps},
        "pollinations": {"width": poll_w, "height": poll_h},
    }
    return cfg


def test_hf_uses_config_dims():
    """_hf_generate must be called with width/height/steps from cfg.image_gen."""
    from pipeline import image_gen

    captured = {}

    def fake_hf(prompt, seed, token, width, height, num_inference_steps):
        captured.update({"w": width, "h": height, "steps": num_inference_steps})
        return b"\x89PNG", None

    with patch.object(image_gen, "_hf_generate", side_effect=fake_hf), \
         patch.object(image_gen, "load_quota_config", return_value={
             "fallback_chains": {"image_generation": ["huggingface"]}
         }), \
         patch.object(image_gen, "_save_image", return_value="/tmp/out.png"):
        image_gen.generate_scene_image(
            image_prompt="test",
            art_style_suffix="",
            seed=1,
            output_dir="/tmp",
            scene_index=0,
            video_id=1,
            cfg=_make_cfg(),
            conn=None,
        )

    assert captured["w"] == 1024
    assert captured["h"] == 1792
    assert captured["steps"] == 28


def test_pollinations_uses_config_dims():
    """_pollinations_generate must receive width/height from cfg.image_gen."""
    from pipeline import image_gen

    captured = {}

    def fake_poll(prompt, seed, width, height):
        captured.update({"w": width, "h": height})
        return b"\x89PNG", None

    with patch.object(image_gen, "_pollinations_generate", side_effect=fake_poll), \
         patch.object(image_gen, "load_quota_config", return_value={
             "fallback_chains": {"image_generation": ["pollinations"]}
         }), \
         patch.object(image_gen, "_save_image", return_value="/tmp/out.png"):
        image_gen.generate_scene_image(
            image_prompt="test",
            art_style_suffix="",
            seed=1,
            output_dir="/tmp",
            scene_index=0,
            video_id=1,
            cfg=_make_cfg(),
            conn=None,
        )

    assert captured["w"] == 1080
    assert captured["h"] == 1920
```

- [ ] **Step 2: Run test — verify it fails**

```bash
pytest tests/test_image_gen_config.py -v
```

Expected: `FAILED` — `_hf_generate` and `_pollinations_generate` don't accept width/height yet.

- [ ] **Step 3: Update `_hf_generate` signature**

In `pipeline/image_gen.py`, change `_hf_generate` (line 54) to accept explicit dims:

```python
def _hf_generate(
    prompt: str,
    seed: int,
    hf_token: str,
    width: int = 768,
    height: int = 1344,
    num_inference_steps: int = 4,
) -> tuple[bytes | None, int | None]:
    """Returns (image_bytes, error_code). error_code is None on success."""
    try:
        r = requests.post(
            _HF_API_URL,
            headers={"Authorization": f"Bearer {hf_token}"},
            json={
                "inputs": prompt,
                "parameters": {
                    "seed": seed,
                    "width": width,
                    "height": height,
                    "num_inference_steps": num_inference_steps,
                },
            },
            timeout=90,
        )
        ct = r.headers.get("content-type", "")
        if r.status_code == 200 and ct.startswith("image"):
            return r.content, None
        log.warning("HF: status=%d body=%s", r.status_code, r.text[:200])
        return None, r.status_code
    except requests.Timeout:
        log.warning("HF generate: timeout")
        return None, None
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else None
```

Also remove the now-unused module constants `_HF_WIDTH` and `_HF_HEIGHT` (lines 42-43).

- [ ] **Step 4: Read `image_gen` config in `generate_scene_image` and pass to `_hf_generate`**

In `generate_scene_image` (line 217 region), after `if cfg:` block, add image_gen extraction:

```python
    if cfg:
        hf_token     = getattr(cfg, "HF_API_TOKEN", "")
        gemini_key   = getattr(cfg, "GOOGLE_AI_STUDIO_API_KEY", "")
        ip           = getattr(cfg, "image_provider", {})
        gemini_model = ip.get("google_ai_studio", {}).get("model", gemini_model)

    # Read image gen dimensions from cfg.image_gen (Task 1)
    ig = getattr(cfg, "image_gen", {}) if cfg else {}
    hf_cfg   = ig.get("huggingface", {})
    poll_cfg = ig.get("pollinations", {})
    hf_width  = hf_cfg.get("width",  768)
    hf_height = hf_cfg.get("height", 1344)
    hf_steps  = hf_cfg.get("num_inference_steps", 4)
    poll_width  = poll_cfg.get("width",  1080)
    poll_height = poll_cfg.get("height", 1920)
```

Then in the provider dispatch (line 249), pass params:

```python
        if provider == "huggingface" and hf_token:
            log.info("image_gen: HuggingFace scene=%d seed=%d %dx%d steps=%d",
                     scene_index, seed, hf_width, hf_height, hf_steps)
            image_data, error_code = _hf_generate(
                full_prompt, seed, hf_token,
                width=hf_width, height=hf_height, num_inference_steps=hf_steps,
            )

        elif provider == "google_ai_studio" and gemini_key:
            log.info("image_gen: Google AI Studio scene=%d", scene_index)
            image_data, error_code = _gemini_generate(full_prompt, gemini_key, gemini_model, ref_b64)

        elif provider == "pollinations":
            log.info("image_gen: Pollinations scene=%d seed=%d %dx%d",
                     scene_index, seed, poll_width, poll_height)
            image_data, error_code = _pollinations_generate(
                full_prompt, seed, width=poll_width, height=poll_height
            )
```

- [ ] **Step 5: Run tests — verify pass**

```bash
pytest tests/test_image_gen_config.py -v
```

Expected: both tests PASS.

- [ ] **Step 6: Commit**

```bash
git add pipeline/image_gen.py tests/test_image_gen_config.py
git commit -m "feat: read HF and Pollinations dims/steps from cfg.image_gen"
```

---

### Task 3: New `pipeline/audio_bg.py` — Pixabay fetch + cache

**Files:**
- Create: `pipeline/audio_bg.py`
- Test: `tests/test_audio_bg.py`

**Interfaces:**
- Consumes: `cfg.PIXABAY_API_KEY`, `cfg.paths["audio"]` (both from Task 1).
- Produces: `fetch_bg_audio(query: str, duration_secs: float, cfg=None) -> str | None` — returns local path to cached MP3 or `None` on any failure.

- [ ] **Step 1: Write failing tests**

Create `tests/test_audio_bg.py`:

```python
"""Tests for pipeline/audio_bg.py — Pixabay fetch + cache logic."""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


def _make_cfg(api_key="testkey", audio_dir=None, tmp_path=None):
    cfg = MagicMock()
    cfg.PIXABAY_API_KEY = api_key
    cfg.paths = {"audio": str(tmp_path / "audio") if tmp_path else "data/audio"}
    return cfg


def _pixabay_response(audio_url="https://cdn.pixabay.com/audio/2024/chant.mp3"):
    resp = MagicMock()
    resp.json.return_value = {
        "total": 1,
        "hits": [{"id": 123, "name": "Om Chanting", "audio": audio_url}],
    }
    resp.raise_for_status.return_value = None
    return resp


def test_returns_none_when_no_api_key(tmp_path):
    from pipeline.audio_bg import fetch_bg_audio
    cfg = _make_cfg(api_key="", tmp_path=tmp_path)
    result = fetch_bg_audio("chanting meditation", cfg=cfg)
    assert result is None


def test_returns_cached_path_without_network(tmp_path):
    from pipeline.audio_bg import fetch_bg_audio
    cfg = _make_cfg(tmp_path=tmp_path)
    # Pre-populate cache
    cache_dir = tmp_path / "audio" / "bg"
    cache_dir.mkdir(parents=True)
    cached = cache_dir / "chanting-meditation.mp3"
    cached.write_bytes(b"FAKEMP3")

    with patch("pipeline.audio_bg.requests.get") as mock_get:
        result = fetch_bg_audio("chanting meditation", cfg=cfg)
        mock_get.assert_not_called()

    assert result == str(cached)


def test_downloads_and_caches_on_miss(tmp_path):
    from pipeline.audio_bg import fetch_bg_audio
    cfg = _make_cfg(tmp_path=tmp_path)

    with patch("pipeline.audio_bg.requests.get", return_value=_pixabay_response()) as mock_get, \
         patch("pipeline.audio_bg.urllib.request.urlretrieve") as mock_dl:
        mock_dl.side_effect = lambda url, path: Path(path).write_bytes(b"MP3DATA")
        result = fetch_bg_audio("chanting meditation", cfg=cfg)

    assert result is not None
    assert result.endswith("chanting-meditation.mp3")
    mock_get.assert_called_once()
    mock_dl.assert_called_once()


def test_returns_none_on_empty_hits(tmp_path):
    from pipeline.audio_bg import fetch_bg_audio
    cfg = _make_cfg(tmp_path=tmp_path)

    empty_resp = MagicMock()
    empty_resp.json.return_value = {"total": 0, "hits": []}
    empty_resp.raise_for_status.return_value = None

    with patch("pipeline.audio_bg.requests.get", return_value=empty_resp):
        result = fetch_bg_audio("chanting meditation", cfg=cfg)

    assert result is None


def test_returns_none_on_network_error(tmp_path):
    from pipeline.audio_bg import fetch_bg_audio
    cfg = _make_cfg(tmp_path=tmp_path)

    with patch("pipeline.audio_bg.requests.get", side_effect=Exception("timeout")):
        result = fetch_bg_audio("chanting meditation", cfg=cfg)

    assert result is None
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
pytest tests/test_audio_bg.py -v
```

Expected: `ModuleNotFoundError: No module named 'pipeline.audio_bg'`

- [ ] **Step 3: Create `pipeline/audio_bg.py`**

```python
"""
Background audio fetcher — Pixabay Music API.

fetch_bg_audio(query, duration_secs, cfg) -> str | None

Downloads first matching track to data/audio/bg/{slug}.mp3 and caches it.
Returns local path on success, None on any failure (caller degrades gracefully).
"""

import logging
import re
import urllib.request
from pathlib import Path

import requests

log = logging.getLogger(__name__)

_PIXABAY_MUSIC_URL = "https://pixabay.com/api/music/"


def _slug(query: str) -> str:
    """Convert query string to a safe filename slug."""
    return re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")


def fetch_bg_audio(query: str, duration_secs: float = 0.0, cfg=None) -> str | None:
    """
    Fetch a background audio track for the given query.

    Args:
        query:         Search term for Pixabay Music API (e.g. "chanting meditation").
        duration_secs: Intended video duration in seconds (informational, not used in search).
        cfg:           Config singleton. Reads PIXABAY_API_KEY and paths["audio"].

    Returns:
        Absolute path to cached MP3 file, or None if fetch fails for any reason.
    """
    api_key = getattr(cfg, "PIXABAY_API_KEY", "") if cfg else ""
    if not api_key:
        log.warning("audio_bg: PIXABAY_API_KEY not set — skipping background audio")
        return None

    audio_base = "data/audio"
    if cfg:
        audio_base = cfg.paths.get("audio", audio_base)
    cache_dir = Path(audio_base) / "bg"
    cache_path = cache_dir / f"{_slug(query)}.mp3"

    if cache_path.exists():
        log.info("audio_bg: cache hit %s", cache_path)
        return str(cache_path)

    try:
        resp = requests.get(
            _PIXABAY_MUSIC_URL,
            params={"key": api_key, "q": query, "category": "meditation"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        hits = data.get("hits", [])
        if not hits:
            log.warning("audio_bg: no results for query=%r", query)
            return None

        audio_url = hits[0].get("audio", "")
        if not audio_url:
            log.warning("audio_bg: first result has no audio URL")
            return None

        cache_dir.mkdir(parents=True, exist_ok=True)
        log.info("audio_bg: downloading %s → %s", audio_url, cache_path)
        urllib.request.urlretrieve(audio_url, cache_path)
        log.info("audio_bg: saved %s", cache_path)
        return str(cache_path)

    except Exception as e:
        log.warning("audio_bg: fetch failed: %s", e)
        return None
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
pytest tests/test_audio_bg.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/audio_bg.py tests/test_audio_bg.py
git commit -m "feat: add audio_bg module for Pixabay chanting fetch and cache"
```

---

### Task 4: FFmpeg assembler — `bg_audio_path` param + `filter_complex` mixing

**Files:**
- Modify: `pipeline/ffmpeg_assembler.py:265-272` (signature), `pipeline/ffmpeg_assembler.py:347-365` (Step 5 audio mix)
- Test: `tests/test_ffmpeg_bg_audio.py`

**Interfaces:**
- Consumes: `cfg.background_audio.get("volume", 0.12)` from Task 1.
- Produces: `assemble_from_images(..., bg_audio_path: str | None = None)` — extended signature; when `bg_audio_path` is a valid path, uses `filter_complex` for mixing; when `None`, existing code path unchanged.

- [ ] **Step 1: Write failing test**

Create `tests/test_ffmpeg_bg_audio.py`:

```python
"""Test that assemble_from_images passes bg_audio_path to ffmpeg filter_complex."""
import subprocess
from pathlib import Path
from unittest.mock import patch, call, MagicMock
import pytest


def _make_cfg(volume=0.12):
    cfg = MagicMock()
    cfg.video = {"resolution": [1080, 1920], "fps": 30, "caption_style": {}}
    cfg.background_audio = {"volume": volume}
    return cfg


def test_bg_audio_path_none_uses_simple_mux(tmp_path):
    """Without bg_audio_path, assembler must NOT use filter_complex."""
    from pipeline.ffmpeg_assembler import assemble_from_images

    img = tmp_path / "scene_0.png"
    img.write_bytes(b"PNG")
    audio = tmp_path / "narration.mp3"
    audio.write_bytes(b"MP3")
    out = str(tmp_path / "out.mp4")

    ffmpeg_calls = []

    def fake_ffmpeg(*args, check=True):
        ffmpeg_calls.append(list(args))
        result = MagicMock()
        result.returncode = 0
        return result

    with patch("pipeline.ffmpeg_assembler._ffmpeg", side_effect=fake_ffmpeg), \
         patch("pipeline.ffmpeg_assembler._get_duration", return_value=5.0):
        assemble_from_images(
            scene_images=[str(img)],
            audio_path=str(audio),
            output_path=out,
            scenes=None,
            cfg=_make_cfg(),
            bg_audio_path=None,
        )

    # No call should contain filter_complex
    all_args = [arg for call_args in ffmpeg_calls for arg in call_args]
    assert "-filter_complex" not in all_args


def test_bg_audio_path_provided_uses_filter_complex(tmp_path):
    """With bg_audio_path, assembler must use filter_complex with amix."""
    from pipeline.ffmpeg_assembler import assemble_from_images

    img = tmp_path / "scene_0.png"
    img.write_bytes(b"PNG")
    audio = tmp_path / "narration.mp3"
    audio.write_bytes(b"MP3")
    bg = tmp_path / "chanting.mp3"
    bg.write_bytes(b"MP3")
    out = str(tmp_path / "out.mp4")

    ffmpeg_calls = []

    def fake_ffmpeg(*args, check=True):
        ffmpeg_calls.append(list(args))
        result = MagicMock()
        result.returncode = 0
        return result

    with patch("pipeline.ffmpeg_assembler._ffmpeg", side_effect=fake_ffmpeg), \
         patch("pipeline.ffmpeg_assembler._get_duration", return_value=5.0), \
         patch("pipeline.ffmpeg_assembler.Path") as mock_path:
        mock_path.return_value.exists.return_value = True
        mock_path.side_effect = lambda p: Path(p)
        assemble_from_images(
            scene_images=[str(img)],
            audio_path=str(audio),
            output_path=out,
            scenes=None,
            cfg=_make_cfg(),
            bg_audio_path=str(bg),
        )

    all_args = [arg for call_args in ffmpeg_calls for arg in call_args]
    assert "-filter_complex" in all_args
    # Volume and amix must appear in the filter_complex string
    filter_str = next(a for a in all_args if "amix" in str(a))
    assert "amix" in filter_str
    assert "aloop" in filter_str
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
pytest tests/test_ffmpeg_bg_audio.py -v
```

Expected: `FAILED` — `assemble_from_images` doesn't accept `bg_audio_path` yet.

- [ ] **Step 3: Update `assemble_from_images` signature**

In `pipeline/ffmpeg_assembler.py`, change the function signature at line 265:

```python
def assemble_from_images(
    scene_images: list[str],
    audio_path: str,
    output_path: str,
    scenes: list[dict] | None = None,
    cfg=None,
    scene_duration: float | None = None,
    bg_audio_path: str | None = None,
) -> str:
```

Also update the docstring to include:
```
        bg_audio_path: Optional path to background audio (mp3). Mixed at low volume
                       under narration using ffmpeg filter_complex. Loops to video length.
```

- [ ] **Step 4: Replace Step 5 audio mix in assembler**

Replace the Step 5 block (lines 347-365 — `# Step 5: Mix audio` through end of the `_ffmpeg` call) with:

```python
        # Step 5: Mix audio
        audio_dur = _get_duration(audio_path)
        log.info("Audio duration: %.2fs", audio_dur)

        if audio_dur <= 0:
            _ffmpeg("-i", captioned_path, "-c", "copy", output_path)

        elif bg_audio_path and Path(bg_audio_path).exists():
            bg_volume = 0.12
            if cfg:
                bg_volume = cfg.background_audio.get("volume", 0.12)
            min_dur = min(total_video_dur, audio_dur)
            log.info("Mixing bg audio: %s volume=%.2f", bg_audio_path, bg_volume)
            _ffmpeg(
                "-i", captioned_path,
                "-i", audio_path,
                "-i", bg_audio_path,
                "-filter_complex",
                (
                    f"[2:a]volume={bg_volume},aloop=loop=-1:size=2000000000,"
                    f"atrim=duration={min_dur}[bg];"
                    f"[1:a][bg]amix=inputs=2:duration=first:dropout_transition=2[aout]"
                ),
                "-map", "0:v:0",
                "-map", "[aout]",
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "128k",
                "-t", str(min_dur),
                "-movflags", "+faststart",
                output_path,
            )

        else:
            min_dur = min(total_video_dur, audio_dur)
            _ffmpeg(
                "-i", captioned_path,
                "-i", audio_path,
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "128k",
                "-t", str(min_dur),
                "-movflags", "+faststart",
                output_path,
            )
```

Add `from pathlib import Path` import at top if not already present (it is — line 15).

- [ ] **Step 5: Run tests — verify they pass**

```bash
pytest tests/test_ffmpeg_bg_audio.py -v
```

Expected: both tests PASS.

- [ ] **Step 6: Run full test suite — verify nothing broken**

```bash
pytest -v
```

Expected: all pre-existing tests still pass.

- [ ] **Step 7: Commit**

```bash
git add pipeline/ffmpeg_assembler.py tests/test_ffmpeg_bg_audio.py
git commit -m "feat: add bg_audio_path param to assemble_from_images with filter_complex mixing"
```

---

### Task 5: `run_niche.py` — insert step 3.5 background audio fetch

**Files:**
- Modify: `run_niche.py:292-317` (between TTS and assembly steps)

**Interfaces:**
- Consumes: `fetch_bg_audio` from Task 3; `cfg.background_audio` from Task 1; `audio_dur` from TTS step (line 297).
- Produces: `bg_audio_path: str | None` passed into `assemble_from_images`.

- [ ] **Step 1: Add step 3.5 in `run_niche.py`**

After the TTS block (after line 304 — `conn.commit()` that sets `voice_ready`), insert:

```python
        # ── Step 3.5: Background audio ────────────────────────────────────────
        bg_audio_path = None
        if not args.dry_run and cfg.background_audio.get("enabled", False):
            log.info("[3.5/5] Fetching background audio...")
            from pipeline.audio_bg import fetch_bg_audio
            bg_audio_path = fetch_bg_audio(
                query=cfg.background_audio.get("query", "chanting meditation"),
                duration_secs=audio_dur,
                cfg=cfg,
            )
            if bg_audio_path:
                log.info("Background audio: %s", bg_audio_path)
            else:
                log.info("Background audio unavailable — continuing without it")
```

- [ ] **Step 2: Pass `bg_audio_path` into `assemble_from_images` call**

Change the `assemble_from_images` call (line 311) to add the new param:

```python
        assemble_from_images(
            scene_images=scene_image_paths,
            audio_path=audio_path,
            output_path=output_path,
            scenes=script["scenes"],
            cfg=cfg,
            bg_audio_path=bg_audio_path,
        )
```

- [ ] **Step 3: Verify dry-run skips bg fetch**

```bash
python run_niche.py mythology "story of Medusa" --dry-run
```

Expected: output shows steps 1-4 but NO `[3.5/5]` log line. Script generates and prints without network calls.

- [ ] **Step 4: Verify `--no-telegram` run logs step 3.5**

Set `PIXABAY_API_KEY=` (empty) in `.env`, then run:

```bash
python run_niche.py mythology "story of Apollo" --no-telegram
```

Expected: `[3.5/5] Fetching background audio...` appears, then `Background audio unavailable — continuing without it`. Video assembles normally without bg audio.

- [ ] **Step 5: Commit**

```bash
git add run_niche.py
git commit -m "feat: insert step 3.5 background audio fetch into niche pipeline"
```

---

## Verification

After all tasks done, run a full pipeline with a valid `PIXABAY_API_KEY`:

```bash
python run_niche.py mythology "story of Shiva and Parvati" --no-telegram
```

Confirm in logs:
1. `image_gen: HuggingFace ... 1024x1792 steps=28` (or Pollinations `1080x1920`)
2. `[3.5/5] Fetching background audio...` → `Background audio: data/audio/bg/chanting-meditation.mp3`
3. `Mixing bg audio: ... volume=0.12`
4. Final video plays with chanting under narration at low volume.

Run full test suite one final time:

```bash
pytest -v
```

All tests must pass.
