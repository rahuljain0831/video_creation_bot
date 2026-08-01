# Social Captions Generator + Review Poller — Design Spec

**Date:** 2026-08-01  
**Status:** Approved

---

## Overview

Two new capabilities added to the video pipeline:

1. **Per-platform social captions** — after each video is sent to Telegram for review, a single follow-up message is sent containing LLM-generated captions and hashtags for all 6 publishing platforms.
2. **Review poller** — a CLI script that polls the DB for a given `video_id` and prints to console when status changes to `approved` or `rejected`.

Immediately applied to: quasars (space_science) + scary story runs, used to test approve and reject flows end-to-end.

---

## Component 1: `pipeline/social_captions.py`

### Purpose
Generate platform-tailored captions and hashtags from a video script in a single LLM call.

### Interface
```python
def generate_social_captions(script: dict, niche: dict, cfg=None) -> dict:
    """
    Args:
        script: output of generate_script() — has story_title, scenes[narration]
        niche:  niche config dict from settings.json
        cfg:    config singleton (for llm_router settings)

    Returns:
        {
          "youtube":   {"caption": str, "hashtags": list[str]},
          "instagram": {"caption": str, "hashtags": list[str]},
          "facebook":  {"caption": str, "hashtags": list[str]},
          "tiktok":    {"caption": str, "hashtags": list[str]},
          "pinterest":  {"caption": str, "hashtags": list[str]},
          "linkedin":  {"caption": str, "hashtags": list[str]},
        }
    """
```

### LLM Prompt Strategy
- Input: story title + concatenated narration + niche tone
- Single call to `call_llm()` via existing `llm_router`
- Prompt requests JSON with all 6 platforms
- Platform-specific constraints embedded in prompt:
  - YouTube: 3-5 hashtags, SEO-optimized description, 200-300 chars
  - Instagram: 15-20 hashtags, casual/engaging, 100-150 chars
  - Facebook: 3-5 hashtags, conversational, 150-200 chars
  - TikTok: 5-10 hashtags, punchy/trend-aware, 80-100 chars
  - Pinterest: 5-8 hashtags, descriptive/searchable, 100-150 chars
  - LinkedIn: 3-5 hashtags, professional/educational tone, 150-200 chars
- Uses same `_extract_json()` pattern as `script_gen.py` for robust parsing
- Falls back gracefully: if LLM fails, returns empty dict (captions step skipped, video still sent)

---

## Component 2: `run_niche.py` — Telegram caption send

### Change
After the existing `send_for_review()` call succeeds, call `generate_social_captions()` and send one additional Telegram text message.

### Message Format
```
📢 Social Media Captions — [Story Title]

▶️ YouTube
[caption text]
#hashtag1 #hashtag2 #hashtag3

📸 Instagram
[caption text]
#hashtag1 #hashtag2 ... #hashtag20

👍 Facebook
[caption text]
#hashtag1 #hashtag2

🎵 TikTok
[caption text]
#hashtag1 #hashtag2 ... #hashtag10

📌 Pinterest
[caption text]
#hashtag1 ... #hashtag8

💼 LinkedIn
[caption text]
#hashtag1 #hashtag2
```

### Error Handling
- Caption generation failure is non-fatal — log warning, skip the follow-up message
- Video already sent at this point; no rollback needed

---

## Component 3: `wait_for_review.py`

### Purpose
CLI poller that watches a `video_id` in the DB and prints status updates until the review decision is made.

### Usage
```bash
python wait_for_review.py <video_id>
# e.g.
python wait_for_review.py 42
```

### Behavior
- Connects to `output/db/agent.db` (from `cfg.paths["db"]`)
- Polls `SELECT status FROM videos WHERE id=?` every 5 seconds
- Prints each status it observes (not just on change) for visibility
- Exits with message when status reaches `approved` or `rejected`
- Also exits on `KeyboardInterrupt` (Ctrl+C)
- Timeout: exits after 30 minutes if no decision made (avoids infinite hang)

### Output Example
```
Watching video_id=42 — press Ctrl+C to stop
[12:01:05]  status = sent
[12:01:10]  status = sent
[12:01:15]  status = approved  ✅
Decision reached: APPROVED
```

---

## End-to-End Test Plan

### Video 1 — Approve flow
```bash
python run_niche.py space_science "quasars"
# note video_id from output
python wait_for_review.py <video_id>
# tap Approve in Telegram
# verify: console prints "approved"
```

### Video 2 — Reject flow
```bash
python run_niche.py scary_stories
# note video_id from output
python wait_for_review.py <video_id>
# tap Reject in Telegram
# verify: console prints "rejected"
```

### DB verification (manual)
```bash
sqlite3 output/db/agent.db "SELECT id, status, niche_id FROM videos ORDER BY id DESC LIMIT 5;"
```

---

## Files Changed

| File | Change |
|------|--------|
| `pipeline/social_captions.py` | New — caption generator |
| `run_niche.py` | Add caption send after `send_for_review()` |
| `wait_for_review.py` | New — review poller CLI |

No DB schema changes needed. No new dependencies.
