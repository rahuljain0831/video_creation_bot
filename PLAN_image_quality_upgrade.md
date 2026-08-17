# PLAN: Image Quality + Per-Scene Relevance Upgrade

## Goal
Improve two things without touching the rest of the pipeline:
1. Image quality — fix distorted faces/hands, push to 720p+
2. Image relevance — image matches the sentence being narrated at that moment, not the whole script's vibe

## Constraint
Wrapper-only. No existing function signatures change. Test standalone, compare output, swap in only if it wins.

---

## Part 1: Per-Scene Relevant Prompting (do this FIRST)

Why first: garbage prompt → garbage image, regardless of model quality. No point upscaling a wrong image.

### Current state
Script split into equal-duration scenes, one broad image prompt/style reused across all of them.

### Change
New wrapper function: `generate_scene_prompts(script_text, niche) -> list[dict]`
- Input: full script (already segmented by existing scene-split logic — unchanged)
- For each scene/sentence: call Ollama (`qwen3.5:2b`, fits your "agentic decision" pattern) to produce a short, visual, model-ready prompt describing what should be seen *at that moment* (e.g. horror story: "abandoned hallway, flickering light, POV" for scene 3, not generic "horror atmosphere")
- Output: list of `{scene_id, prompt}` — feeds into existing image-generation call, same interface, just a better prompt string per scene

### Test
- Run on 1 existing script (reuse a past one from repo) through new wrapper only
- Manually eyeball: does image N actually match sentence N?
- No pipeline change yet — just print/save prompts, don't wire into generation

### Done when
Prompts are visibly scene-specific on 3-5 sample scripts across your active niches (finance_facts, ai_tech_tools, space_science) + horror if that's a new niche.

---

## Part 2: Local Image Quality Chain

Why second: only worth fixing image quality once the *content* of the image is correct.

### Chain (ComfyUI, `--lowvram`)
1. Generate at 512×768 — SD1.5-based checkpoint (RealisticVision or similar; pick per-niche later — horror ≠ finance_facts visual style)
2. ADetailer pass — inpaint detected face/hand regions (this is what fixes distortion, not resolution)
3. Real-ESRGAN upscale to 720p+

### Wrapper
`generate_image_local(prompt, niche_style) -> image_path`
- Same input/output contract as whatever your current image-gen fallback function returns (check `image_prompt_rules` / existing fallback chain function signature first — don't guess it)
- Runs as a **new fallback tier**, not a replacement, until proven

### Test
- Run wrapper standalone on 5-10 prompts from Part 1 output
- Compare against current FLUX/Gemini/Pollinations output: face/hand quality, resolution, generation time
- Time budget check: confirm total (SD1.5 + ADetailer + upscale) stays well under your 1hr/video tolerance per image count needed

### Done when
Local chain output beats current fallback chain on: face/hand accuracy, resolution, and is fast enough per-image to not blow the 1hr budget.

---

## Integration decision (after both tests pass)
Only then decide: replace existing image-gen fallback entirely, or insert as a new tier (e.g. local-first, cloud fallback if local fails/slow). Not decided now — decide with test data in hand.

## Explicitly out of scope for this plan
- Scene *timing* sync (word-level, from `word_timings.json`) — separate known gap, do not fold in here
- Video motion / Ken Burns replacement — separate lever, not this plan
- Posting/upload pipeline — untouched
