# Video Creation Agent — Design v3
**Project:** General prompt-driven video generator
**Owner:** Rahul
**Status:** Pre-build, Phase 0 not yet started
**Supersedes:** quote-video-automation, health-benefit ingredient videos (both shelved, not deleted)

---

## 1. What this is

A pipeline that takes a one-line prompt — any topic, any register — and produces a
15-20 second vertical video, end to end, with minimal manual work once running.

Examples of input prompts:
- "two cats of different breeds cooking, food spills, they laugh"
- "how to care for a broken heart plant"

The system does not assume a fixed niche, fixed tone, or fixed visual style.
Topic, tone, and structure are inferred per prompt.

## 2. Why this shape (history, briefly)

Three prior pivots, kept for context:
1. Motivational quote videos — background/TTS/quote pipeline, shelved (unit
   economics didn't work at low RPM).
2. Ingredient health-benefit videos — evidence-gated, Blender anatomy library,
   shelved (evidence-gating and accuracy-stakes were too heavy for the actual
   risk tolerance of a side project — correctly identified, not a failure).
3. **Current: general prompt-driven generator** — visuals moved from
   scripted-in-Blender to AI-generated video per scene, because a general
   prompt can't be served by any fixed asset library.

What carries over from prior designs unchanged: Phase 0 setup, LiteLLM routing
for LLM calls, local TTS, ffmpeg-based assembly, Telegram review bot, SQLite
storage with decision tagging, per-batch agentic decision pattern.

What does NOT carry over: Blender, anatomy assets, evidence-tier gating,
MeSH routing, Europe PMC — all specific to the shelved health pivot.

## 3. Locked decisions

| Area | Decision |
|---|---|
| Video length | 15-20 seconds |
| Scenes per video | 1-2 (each API video-gen call is fixed-cost regardless of clip length — more scenes just burns quota) |
| Tone | LLM auto-detects from the prompt itself, no user input required |
| Ending | LLM decides per video (punchline / tip / reveal / other), no fixed rule |
| Guardrail | Detected tone + ending choice + one-line reasoning logged to the decisions table *before* any video-gen API call — lets Rahul catch a bad read before quota is spent |
| Visuals | AI video generation per scene — NOT Blender/library/compositor |
| Video-gen fallback chain | Veo 3.1 (primary) → Kling → Hugging Face-hosted open-weight models (Wan 2.2 / Mochi 1 / CogVideoX) as third fallback |
| Local video generation | Confirmed not viable — even the lightest open models (LTX-Video, AnimateDiff) need 8GB VRAM minimum; laptop has 4GB. All video generation is API-based, never local. **This is a hardware ceiling that applies regardless of provider (Veo/Kling/HF) — see §3a.** |
| Audio | Local TTS (Piper / Kokoro / Edge TTS) |
| Assembly | ffmpeg (MoviePy for prototyping only — ffmpeg is 4.5-5.5x faster) |
| Spend | Free tier only for now; lower volume accepted as the tradeoff |
| Character/scene consistency | Imperfect by nature of independent per-scene generation; reference-image conditioning helps, doesn't fully solve it. Accepted limitation, not a blocker. |

## 3a. Local vs. online fallback — applies per task, not uniformly

Easy to assume "local + online free tier" is a blanket pattern across the
whole pipeline. It isn't. It applies where hardware allows it, and stops
where it doesn't:

| Task | Online tiers | Local fallback? |
|---|---|---|
| Script/LLM generation | Groq → Cerebras → Google AI Studio (via LiteLLM) | **Yes** — Ollama as last-resort floor. Works because small/quantized LLMs fit in 4GB VRAM or run fine on CPU. |
| TTS / audio | — | **Local only** — Piper/Kokoro/Edge TTS. No online fallback needed; local is already free and unlimited. |
| Video generation | Veo 3.1 → Kling → HF-hosted open-weight models (Wan/Mochi/CogVideoX) | **No.** Confirmed not possible — even the lightest open video models (LTX-Video, AnimateDiff) need 8GB VRAM minimum. Laptop has 4GB. This is a hardware ceiling, not a config choice, and does not change no matter how the fallback chain is extended. |

**Practical consequence:** if all three video-gen tiers are exhausted (rare,
but possible — outage, quota exhaustion across all three), there is no
fourth "fall back to local" rung. The correct behavior is to log the failure
and skip that video, not silently attempt a local load that will OOM.

## 4. Pipeline

```
prompt
  → LLM writes scene-by-scene script
      - detects tone, logs reasoning
      - decides ending type, logs reasoning
      - 1-2 scenes, each with a video-gen prompt + narration line
  → per scene: call video-gen API (Veo → Kling → HF fallback)
  → TTS narration
  → ffmpeg assembles clips + audio + captions → 9:16 output
  → Telegram review
      - shows video + logged tone/ending reasoning
      - Rahul gives good/bad/quality verdict
  → (deferred) publish
```

## 5. Topic-based routing — Option A (manual, not automatic)

**Problem this solves:** as topic variety grows, some topics may need different
models or providers than others (e.g. "plant videos do better with Cerebras
scripts," "car videos need Veo's better physics"). Rather than hardcoding this
guesswork now, the system is built to make it *discoverable and editable*
later.

**How it works (Phase 1, current scope):**
- Every generation decision is tagged in SQLite: which script LLM, which
  video-gen provider, which TTS voice, detected tone, ending type, and a
  topic label (inferred loosely from the prompt — e.g. "plant," "animal,"
  "cooking" — not a rigid taxonomy).
- Rahul reviews videos in Telegram and gives a verdict.
- After a batch of videos (50-100), Rahul looks at tagged outcomes and
  manually edits a **routing config** (a simple lookup: topic → preferred
  model/provider) if a pattern is clear.
- No automatic learning yet. Rahul is the pattern-matcher.

**Explicitly deferred (Phase 2+):** automatic adjustment via a bandit-style
learner (e.g. Thompson sampling / contextual bandit) that updates routing
weights from feedback without manual intervention. Deferred because:
- It's a real ML problem, not a lookup table — needs enough data to avoid
  optimizing toward noise.
- We don't yet know which parameters actually matter (topic? tone? provider?
  script LLM?) — premature to automate before the manual phase reveals that.

## 6. Feedback loop — the core self-improving mechanism

This is treated as the heart of the system, per Rahul's framing: it should
eventually apply to *every* tunable parameter (script LLM, video-gen
provider, TTS voice, tone-detection accuracy, ending-choice quality).

**Phase 1 (current):**
- Manual verdict per video (good/bad/quality) via Telegram.
- Verdict + all decision tags stored together in SQLite — this is what makes
  future pattern-finding possible, whether done manually (Rahul) or later
  automatically (bandit learner).
- Rahul is the feedback signal. No automated interpretation yet.

**Explicitly deferred to Phase 2+:**
- Automatic routing adjustment from aggregated feedback.
- Engagement-based feedback (platform metrics) once auto-post is live —
  more scalable than manual review at higher volume, but not needed at
  current low volume.

## 7. Script quality — explicitly not being optimized yet

Script format (does the LLM follow the JSON shape) was tested in isolation
and hit parsing issues (empty responses from Ollama — likely local
model/config issue, unresolved). But per Rahul's own correction: **script
quality can't be judged in isolation from the resulting video** — a
technically valid script could produce a bad video, and vice versa.

**Decision:** stop optimizing script generation blind. Build one full
end-to-end video (script → video-gen → TTS → assembly) and judge the whole
chain together. Revisit script-generation quality only if the finished video
reveals it's the actual bottleneck.

## 8. Open items

| Item | Status |
|---|---|
| One full end-to-end video, any prompt | Not yet attempted — next real milestone |
| Ollama local script-gen debugging (empty response issue) | Unresolved, deprioritized until after first end-to-end video |
| Topic taxonomy for routing config | Not defined — will emerge from real batches, not designed upfront |
| Hugging Face-hosted fallback — how it's actually called | Needs its own setup pass (see conversation) |
| Publish step | Fully deferred, same as prior designs |

## 9. Phases

| Phase | Scope | Done when |
|---|---|---|
| 0 | Repo/env setup, SQLite schema with decision tagging, LiteLLM config | Test call through router with fallback verified |
| 1 | One full video end-to-end (any prompt) | A 15-20s video exists, reviewed in Telegram |
| 2 | Repeat for 10-20 varied prompts, start tagging + manual routing | Rahul can point to at least one real pattern in the data |
| 3 | Manual routing config in use | Config demonstrably changes provider choice for at least one topic |
| 4 (deferred) | Automatic bandit-based routing | Not started — revisit after Phase 3 has real signal |
| 5 (deferred) | Publish | Same as before — after 0-3 are stable |
