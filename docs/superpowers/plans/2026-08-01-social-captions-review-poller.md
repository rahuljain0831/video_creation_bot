# Social Captions Generator + Review Poller Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate per-platform social media captions/hashtags after each video is sent to Telegram, and add a CLI poller that watches DB status until approve/reject is tapped.

**Architecture:** Single LLM call returns JSON with captions for all 6 platforms; formatted as one follow-up Telegram message after the video. A separate `wait_for_review.py` script polls SQLite every 5 seconds and exits on terminal status.

**Tech Stack:** Python 3.11+, `python-telegram-bot`, `sqlite3`, existing `llm_router.call_llm()`, `unittest.mock` for tests.

## Global Constraints

- No new pip dependencies
- Caption generation failure must be non-fatal — video already sent, log warning and skip
- Platforms: YouTube, Instagram, Facebook, TikTok, Pinterest, LinkedIn
- Telegram message is plain text (no parse_mode) — emoji headers OK
- Poll interval: 5 seconds; timeout: 30 minutes; exits on Ctrl+C
- Follow existing test pattern: `unittest.mock.patch`, `MagicMock`, `pytest` fixtures from `tests/conftest.py`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `pipeline/social_captions.py` | Create | LLM caption generation for all 6 platforms |
| `tests/test_social_captions.py` | Create | Unit tests for caption generation |
| `run_niche.py` | Modify (lines 327-343) | Send captions message after `send_for_review()` |
| `wait_for_review.py` | Create | DB poller CLI |

---

## Task 1: `pipeline/social_captions.py` — Caption generator

**Files:**
- Create: `pipeline/social_captions.py`
- Test: `tests/test_social_captions.py`

**Interfaces:**
- Produces: `generate_social_captions(script: dict, niche: dict, cfg=None) -> dict`
  - `script`: `{"story_title": str, "scenes": [{"narration": str, ...}]}`
  - `niche`: niche config dict with `"tone"` key
  - Returns: `{"youtube": {"caption": str, "hashtags": list[str]}, "instagram": {...}, "facebook": {...}, "tiktok": {...}, "pinterest": {...}, "linkedin": {...}}`
  - On any failure: returns `{}` (empty dict, caller handles gracefully)

- [ ] **Step 1: Write failing tests**

Create `tests/test_social_captions.py`:

```python
"""Tests for pipeline/social_captions.py"""
import pytest
from unittest.mock import patch, MagicMock


_SCRIPT = {
    "story_title": "The Birth of a Quasar",
    "scenes": [
        {"narration": "In the early universe, a black hole began to feed.", "image_prompt": "x"},
        {"narration": "Energy erupted across billions of light-years.", "image_prompt": "x"},
    ],
}
_NICHE = {"id": "space_science", "tone": "awe-inspiring, curious"}

_LLM_JSON = """{
  "youtube":   {"caption": "Quasars: the brightest objects in the universe.", "hashtags": ["#space", "#quasar", "#science", "#cosmos", "#universe"]},
  "instagram": {"caption": "When black holes feast, the cosmos lights up.", "hashtags": ["#space", "#quasar", "#blackhole", "#astronomy", "#cosmos", "#universe", "#nasa", "#sciencefacts", "#astrophysics", "#deepspace", "#milkyway", "#galaxies", "#spaceexploration", "#sciencelovers", "#cosmology"]},
  "facebook":  {"caption": "Did you know quasars outshine entire galaxies?", "hashtags": ["#space", "#quasar", "#science"]},
  "tiktok":    {"caption": "The universe's most powerful flashlights. #quasar", "hashtags": ["#space", "#quasar", "#learnontiktok", "#sciencetok", "#universe", "#blackhole", "#fyp"]},
  "pinterest": {"caption": "Quasars — ancient cosmic beacons from the dawn of time.", "hashtags": ["#space", "#quasar", "#astronomy", "#cosmos", "#science"]},
  "linkedin":  {"caption": "Quasars remind us how little we know about the cosmos.", "hashtags": ["#space", "#science", "#learning"]}
}"""


def test_returns_all_six_platforms():
    from pipeline.social_captions import generate_social_captions
    with patch("pipeline.social_captions.call_llm", return_value=(_LLM_JSON, "gemini/test")):
        result = generate_social_captions(_SCRIPT, _NICHE)
    assert set(result.keys()) == {"youtube", "instagram", "facebook", "tiktok", "pinterest", "linkedin"}


def test_each_platform_has_caption_and_hashtags():
    from pipeline.social_captions import generate_social_captions
    with patch("pipeline.social_captions.call_llm", return_value=(_LLM_JSON, "gemini/test")):
        result = generate_social_captions(_SCRIPT, _NICHE)
    for platform, data in result.items():
        assert "caption" in data, f"{platform} missing caption"
        assert "hashtags" in data, f"{platform} missing hashtags"
        assert isinstance(data["hashtags"], list), f"{platform} hashtags not a list"


def test_returns_empty_dict_on_llm_failure():
    from pipeline.social_captions import generate_social_captions
    with patch("pipeline.social_captions.call_llm", side_effect=Exception("LLM timeout")):
        result = generate_social_captions(_SCRIPT, _NICHE)
    assert result == {}


def test_returns_empty_dict_on_bad_json():
    from pipeline.social_captions import generate_social_captions
    with patch("pipeline.social_captions.call_llm", return_value=("not json at all", "gemini/test")):
        result = generate_social_captions(_SCRIPT, _NICHE)
    assert result == {}


def test_format_telegram_message():
    from pipeline.social_captions import format_telegram_message
    captions = {
        "youtube":   {"caption": "Test caption", "hashtags": ["#space", "#test"]},
        "instagram": {"caption": "Insta caption", "hashtags": ["#a", "#b"]},
        "facebook":  {"caption": "FB caption",   "hashtags": ["#x"]},
        "tiktok":    {"caption": "TT caption",   "hashtags": ["#y"]},
        "pinterest": {"caption": "Pin caption",  "hashtags": ["#z"]},
        "linkedin":  {"caption": "LI caption",   "hashtags": ["#w"]},
    }
    msg = format_telegram_message("The Birth of a Quasar", captions)
    assert "YouTube" in msg
    assert "Instagram" in msg
    assert "TikTok" in msg
    assert "#space" in msg
    assert "The Birth of a Quasar" in msg
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd "H:/aiAutomation/projects/video-creation-agent"
pytest tests/test_social_captions.py -v 2>&1 | head -30
```

Expected: ImportError or ModuleNotFoundError — `pipeline.social_captions` does not exist yet.

- [ ] **Step 3: Implement `pipeline/social_captions.py`**

```python
"""
Social media caption generator.

Single LLM call → JSON with captions + hashtags for 6 platforms.
Returns {} on any failure — caller must handle gracefully.
"""
import json
import logging
import re

log = logging.getLogger(__name__)

_PLATFORMS = ("youtube", "instagram", "facebook", "tiktok", "pinterest", "linkedin")

_PLATFORM_SPECS = {
    "youtube":   "SEO-optimized description 200-300 chars, 3-5 hashtags",
    "instagram": "casual/engaging 100-150 chars, 15-20 hashtags",
    "facebook":  "conversational 150-200 chars, 3-5 hashtags",
    "tiktok":    "punchy/trend-aware 80-100 chars, 5-10 hashtags",
    "pinterest": "descriptive/searchable 100-150 chars, 5-8 hashtags",
    "linkedin":  "professional/educational tone 150-200 chars, 3-5 hashtags",
}

_EMOJI = {
    "youtube":   "▶️ YouTube",
    "instagram": "📸 Instagram",
    "facebook":  "👍 Facebook",
    "tiktok":    "🎵 TikTok",
    "pinterest": "📌 Pinterest",
    "linkedin":  "💼 LinkedIn",
}


def _extract_json(text: str) -> dict:
    """Extract first JSON object from LLM response — mirrors script_gen pattern."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    match = re.search(r"(\{.*\})", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    raise ValueError(f"No valid JSON in LLM response: {text[:200]}")


def generate_social_captions(script: dict, niche: dict, cfg=None) -> dict:
    """
    Generate platform captions and hashtags for a video script.

    Args:
        script: generate_script() output — needs story_title + scenes[narration]
        niche:  niche config dict (uses tone key)
        cfg:    config singleton for llm_router settings

    Returns:
        {"youtube": {"caption": str, "hashtags": [str]}, ...} for all 6 platforms.
        Returns {} on any failure.
    """
    from llm_router import call_llm

    cfg_router = cfg.llm_router if cfg else {}
    story_title = script.get("story_title", "Untitled")
    narration = " ".join(s["narration"] for s in script.get("scenes", []))
    tone = niche.get("tone", "engaging")

    platform_instructions = "\n".join(
        f'  "{p}": {spec}' for p, spec in _PLATFORM_SPECS.items()
    )

    prompt = f"""You are a social media copywriter. Write captions and hashtags for this video.

Title: {story_title}
Tone: {tone}
Script summary: {narration[:500]}

Write captions for these platforms with these requirements:
{platform_instructions}

Respond with ONLY valid JSON, no markdown fences:
{{
  "youtube":   {{"caption": "...", "hashtags": ["#tag1", "#tag2"]}},
  "instagram": {{"caption": "...", "hashtags": ["#tag1", ...]}},
  "facebook":  {{"caption": "...", "hashtags": ["#tag1", ...]}},
  "tiktok":    {{"caption": "...", "hashtags": ["#tag1", ...]}},
  "pinterest": {{"caption": "...", "hashtags": ["#tag1", ...]}},
  "linkedin":  {{"caption": "...", "hashtags": ["#tag1", ...]}}
}}"""

    try:
        raw, model_used = call_llm(prompt, cfg_router=cfg_router, temperature=0.7)
        data = _extract_json(raw)
    except Exception as e:
        log.warning("social_captions: LLM call or parse failed: %s", e)
        return {}

    result = {}
    for platform in _PLATFORMS:
        entry = data.get(platform, {})
        caption = str(entry.get("caption", "")).strip()
        hashtags = entry.get("hashtags", [])
        if not isinstance(hashtags, list):
            hashtags = []
        if caption:
            result[platform] = {"caption": caption, "hashtags": hashtags}

    if not result:
        log.warning("social_captions: LLM returned no valid platform entries. raw=%s", raw[:200])

    log.info("social_captions: generated for %d platforms using %s", len(result), model_used)
    return result


def format_telegram_message(story_title: str, captions: dict) -> str:
    """Format all platform captions into a single Telegram-ready text message."""
    lines = [f"📢 Social Media Captions — {story_title}", ""]
    for platform in _PLATFORMS:
        if platform not in captions:
            continue
        data = captions[platform]
        header = _EMOJI.get(platform, platform.title())
        caption = data.get("caption", "")
        hashtags = " ".join(data.get("hashtags", []))
        lines.append(header)
        lines.append(caption)
        if hashtags:
            lines.append(hashtags)
        lines.append("")
    return "\n".join(lines).strip()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd "H:/aiAutomation/projects/video-creation-agent"
pytest tests/test_social_captions.py -v
```

Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/social_captions.py tests/test_social_captions.py
git commit -m "feat: add social captions generator (6 platforms, single LLM call)"
```

---

## Task 2: Wire captions into `run_niche.py` Telegram step

**Files:**
- Modify: `run_niche.py` (Telegram step, lines ~327-343)

**Interfaces:**
- Consumes: `generate_social_captions(script, niche, cfg) -> dict` from Task 1
- Consumes: `format_telegram_message(story_title, captions) -> str` from Task 1
- Consumes: `cfg.TELEGRAM_BOT_TOKEN`, `cfg.TELEGRAM_CHAT_ID` (already used in this file)

- [ ] **Step 1: Locate the Telegram step in `run_niche.py`**

Open `run_niche.py`. Find the block starting at `# ── Step 5: Telegram` (~line 323). The block ends after `conn.execute("UPDATE videos SET status='sent'...")`.

- [ ] **Step 2: Add caption send after `send_for_review()`**

In `run_niche.py`, replace the Telegram step block:

```python
        # ── Step 5: Telegram ──────────────────────────────────────────────────
        if args.no_telegram:
            log.info("[5/5] --no-telegram: skipping.")
            log.info("Final video: %s", output_path)
        else:
            log.info("[5/5] Sending to Telegram...")
            caption = (
                f"*Niche:* {niche['label']}\n"
                f"*Story:* {script['story_title']}\n"
                f"*Scenes:* {script['scene_count']}"
            )
            from review.telegram_bot import send_for_review
            send_for_review(
                video_id=video_id,
                file_path=output_path,
                quote_text=caption,
                conn=conn,
            )
            conn.execute("UPDATE videos SET status='sent' WHERE id=?", (video_id,))
            conn.commit()
            log.info("Sent to Telegram.")
```

With:

```python
        # ── Step 5: Telegram ──────────────────────────────────────────────────
        if args.no_telegram:
            log.info("[5/5] --no-telegram: skipping.")
            log.info("Final video: %s", output_path)
        else:
            log.info("[5/5] Sending to Telegram...")
            caption = (
                f"*Niche:* {niche['label']}\n"
                f"*Story:* {script['story_title']}\n"
                f"*Scenes:* {script['scene_count']}"
            )
            from review.telegram_bot import send_for_review
            send_for_review(
                video_id=video_id,
                file_path=output_path,
                quote_text=caption,
                conn=conn,
            )
            conn.execute("UPDATE videos SET status='sent' WHERE id=?", (video_id,))
            conn.commit()
            log.info("Sent to Telegram.")

            # Send per-platform social captions as follow-up message
            try:
                from pipeline.social_captions import generate_social_captions, format_telegram_message
                import asyncio
                from telegram import Bot
                from telegram.request import HTTPXRequest

                log.info("[5/5] Generating social captions...")
                social_caps = generate_social_captions(script, niche, cfg)
                if social_caps:
                    msg_text = format_telegram_message(script["story_title"], social_caps)
                    async def _send_captions():
                        bot = Bot(
                            token=cfg.TELEGRAM_BOT_TOKEN,
                            request=HTTPXRequest(connect_timeout=30, read_timeout=60),
                        )
                        await bot.send_message(chat_id=cfg.TELEGRAM_CHAT_ID, text=msg_text)
                    asyncio.run(_send_captions())
                    log.info("Social captions sent to Telegram.")
                else:
                    log.warning("Social captions empty — skipping follow-up message.")
            except Exception as e:
                log.warning("Social captions send failed (non-fatal): %s", e)
```

- [ ] **Step 3: Smoke-test with dry-run (no Telegram needed)**

```bash
cd "H:/aiAutomation/projects/video-creation-agent"
python run_niche.py space_science "quasars" --dry-run
```

Expected: pipeline runs script generation, exits after script step, no errors.

- [ ] **Step 4: Commit**

```bash
git add run_niche.py
git commit -m "feat: send social media captions to Telegram after video review send"
```

---

## Task 3: `wait_for_review.py` — DB poller CLI

**Files:**
- Create: `wait_for_review.py`

**Interfaces:**
- Consumes: `cfg.paths["db"]` for DB path
- CLI: `python wait_for_review.py <video_id>`
- Prints status each poll, exits on `approved` or `rejected` or timeout (30 min) or Ctrl+C

- [ ] **Step 1: Create `wait_for_review.py`**

```python
"""
wait_for_review.py — Poll DB for video review decision.

Usage:
    python wait_for_review.py <video_id>

Polls every 5 seconds. Exits when status reaches 'approved' or 'rejected',
after 30-minute timeout, or on Ctrl+C.
"""
import sqlite3
import sys
import time
from datetime import datetime

POLL_INTERVAL = 5       # seconds between DB checks
TIMEOUT_SECONDS = 1800  # 30 minutes

TERMINAL_STATUSES = {"approved", "rejected"}


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def watch(video_id: int, db_path: str) -> None:
    print(f"Watching video_id={video_id} — press Ctrl+C to stop")
    deadline = time.monotonic() + TIMEOUT_SECONDS
    last_status = None

    while time.monotonic() < deadline:
        try:
            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "SELECT status FROM videos WHERE id=?", (video_id,)
            ).fetchone()
            conn.close()
        except Exception as e:
            print(f"[{_now()}]  DB error: {e}")
            time.sleep(POLL_INTERVAL)
            continue

        if row is None:
            print(f"[{_now()}]  video_id={video_id} not found in DB")
            time.sleep(POLL_INTERVAL)
            continue

        status = row[0]
        print(f"[{_now()}]  status = {status}")

        if status in TERMINAL_STATUSES:
            icon = "✅" if status == "approved" else "❌"
            print(f"\nDecision reached: {status.upper()} {icon}")
            return

        last_status = status
        time.sleep(POLL_INTERVAL)

    print(f"\nTimeout after {TIMEOUT_SECONDS // 60} minutes. Last status: {last_status}")


def main() -> None:
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        print("Usage: python wait_for_review.py <video_id>")
        sys.exit(1)

    video_id = int(sys.argv[1])

    from config import cfg
    db_path = cfg.paths["db"]

    try:
        watch(video_id, db_path)
    except KeyboardInterrupt:
        print("\nStopped by user.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify script runs against DB**

First check DB has at least one video:

```bash
cd "H:/aiAutomation/projects/video-creation-agent"
python -c "import sqlite3; conn=sqlite3.connect('output/db/agent.db'); print(conn.execute('SELECT id,status FROM videos ORDER BY id DESC LIMIT 3').fetchall())"
```

Then test with a known video_id (e.g. id=1):

```bash
python wait_for_review.py 1
```

Expected: prints `[HH:MM:SS]  status = <whatever status is>` every 5 seconds. Ctrl+C stops it cleanly.

- [ ] **Step 3: Commit**

```bash
git add wait_for_review.py
git commit -m "feat: add wait_for_review.py DB poller CLI"
```

---

## Task 4: End-to-end run — Quasars (approve) + Scary Story (reject)

**Files:** None modified — this is execution + verification.

- [ ] **Step 1: Run quasars video**

```bash
cd "H:/aiAutomation/projects/video-creation-agent"
python run_niche.py space_science "quasars"
```

Note the `video_id` from log output: `Created video row: id=<N>`

- [ ] **Step 2: Start poller for quasars video in a second terminal**

```bash
python wait_for_review.py <video_id_from_step_1>
```

- [ ] **Step 3: Tap Approve in Telegram**

Poller prints `Decision reached: APPROVED ✅` and exits.

- [ ] **Step 4: Run scary story video**

```bash
python run_niche.py scary_stories
```

Note the `video_id`.

- [ ] **Step 5: Start poller for scary story video**

```bash
python wait_for_review.py <video_id_from_step_4>
```

- [ ] **Step 6: Tap Reject in Telegram**

Poller prints `Decision reached: REJECTED ❌` and exits.

- [ ] **Step 7: Verify both statuses in DB**

```bash
python -c "
import sqlite3
conn = sqlite3.connect('output/db/agent.db')
rows = conn.execute('SELECT id, niche_id, status FROM videos ORDER BY id DESC LIMIT 5').fetchall()
for r in rows:
    print(r)
"
```

Expected: both video rows show correct final statuses (`approved` and `rejected`).
