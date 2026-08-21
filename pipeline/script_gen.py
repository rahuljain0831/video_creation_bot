"""
Script generator — design-v3 pivot (niche-driven).

Takes a niche config dict + optional story seed → produces a structured script with
12-25 scenes, each with narration + image_prompt.

Tone is NOT auto-detected — it comes from the niche config.
All decisions are logged to the `decisions` table BEFORE image lookup runs.
"""

import json
import logging
import re
import sqlite3

# Camera framings image_gen knows how to prompt for. Imported rather than
# redeclared so the two lists cannot drift.
from pipeline.image_gen import SHOT_TYPES

log = logging.getLogger(__name__)

_VALID_NICHE_IDS = {
    "mythology", "scary_stories", "heists",
    "space_science", "ai_tech_tools", "finance_facts",
}

# Scene templates the Remotion procedural renderer knows how to draw.
# Order matters only for documentation; `line` is the safe default.
PROCEDURAL_VISUALS = ("hook", "line", "impact", "reveal", "scare", "end")

_ACCENT_MAX_CHARS = 24

# Templates whose beat gets a second image, so the cut inside it is a real cut.
_REVEAL_TEMPLATES = ("impact", "reveal", "scare")


def _niche_bound(niche: dict, cfg, key: str, default):
    """Per-niche override of a `video.*` tunable, falling back to the global."""
    if key in niche:
        return niche[key]
    if cfg:
        return cfg.video.get(key, default)
    return default


def _log_decision(
    conn: sqlite3.Connection,
    video_id: int,
    decision_point: str,
    chosen_option: str,
    reasoning: str,
    model_used: str,
) -> None:
    conn.execute(
        """INSERT INTO decisions (video_id, decision_point, chosen_option, reasoning)
           VALUES (?, ?, ?, ?)""",
        (video_id, decision_point, chosen_option, f"[{model_used}] {reasoning}"),
    )
    conn.commit()
    log.info("Decision logged: %s = %s", decision_point, chosen_option)


def _extract_json(text: str) -> dict:
    """Extract first JSON object from LLM response."""
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

    raise ValueError(f"No valid JSON object found in LLM response: {text[:300]}")


# Words that are free to appear on both sides without meaning the accent is a
# repeat of the line. Deliberately narrow: this is a duplication check, not a
# similarity metric.
_ACCENT_FILLER = frozenset(
    "a an the of in on at to and or is was were be been it its this that there "
    "from into for with my your his her their our".split()
)


# Contractions expanded before comparison, so an accent of "DON'T LOOK BACK"
# still matches a line that says "do not look back".
_CONTRACTIONS = (
    ("can't", "cannot"), ("won't", "will not"), ("n't", " not"),
    ("'re", " are"), ("'ll", " will"), ("'ve", " have"),
    ("'m", " am"), ("'s", ""),
)


def _accent_content(text: str) -> list[str]:
    """Comparable content words: contractions expanded, plurals folded."""
    lowered = text.lower()
    for src, dest in _CONTRACTIONS:
        lowered = lowered.replace(src, dest)

    out = []
    for word in re.findall(r"[a-z0-9:]+", lowered):
        if word in _ACCENT_FILLER:
            continue
        # Crude singularisation, so the accent "KNOCK." matches a line about
        # "three knocks".
        if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        out.append(word)
    return out


def _accent_is_spoken(accent: str, narration: str) -> bool:
    """
    True when the accent's words are already in the line the narrator says.

    The accent is drawn as huge type while the caption band shows the narration
    word by word, so an accent that repeats its own line puts the same words on
    screen twice — which is exactly what "3:07 AM" over the caption "At 3:07 AM
    the" looked like.

    Compared on content words rather than as a substring: the accent
    "FROM INSIDE" against the narration "unlocked from the inside" is the same
    duplication, and a substring test misses it over one article.
    """
    accent_words = _accent_content(accent)
    if not accent_words:
        return False
    spoken = set(_accent_content(narration))
    return all(w in spoken for w in accent_words)


def _normalise_procedural_scene(raw: dict, narration: str) -> dict:
    """
    Coerce one LLM scene into the shape the Remotion procedural renderer expects.

    Unknown template names degrade to `line` rather than failing the run — a
    bogus enum value is the most common way a small local model goes wrong here.
    """
    visual = str(raw.get("visual", "")).strip().lower()
    if visual not in PROCEDURAL_VISUALS:
        if visual:
            log.warning("script_gen: unknown visual %r → 'line'", visual)
        visual = "line"

    accent = str(raw.get("accent") or "").strip()[:_ACCENT_MAX_CHARS]

    try:
        repeat = int(raw.get("repeat", 1))
    except (TypeError, ValueError):
        repeat = 1
    repeat = max(1, min(repeat, 3))

    # Shot type drives the camera phrase in image_gen. An unknown value degrades
    # to "wide" — the same posture as the template enum above.
    shot = str(raw.get("shot", "")).strip().lower()
    if shot not in SHOT_TYPES:
        if shot:
            log.warning("script_gen: unknown shot %r → 'wide'", shot)
        shot = "wide"

    return {
        "narration": narration,
        "visual":    visual,
        "accent":    accent,
        "repeat":    repeat,
        "shot":      shot,
    }


def _break_line_runs(scenes: list[dict], max_run: int = 3) -> None:
    """
    Stop long stretches where nothing punctuates the narration, in place.

    `line` beats are where dread accumulates, so most scenes should be one — but
    eleven scenes carrying only two non-`line` beats left runs of four and five
    with no typography at all, and those stretches are where a viewer leaves.

    Only a beat that already has an accent can be promoted: `impact` with
    nothing to punch onto the screen is a flash and a shake over nothing, and
    inventing an accent here would undo the duplication guard.
    """
    run_start = 0
    for i in range(len(scenes) + 1):
        is_line = i < len(scenes) and scenes[i]["visual"] == "line"
        if is_line:
            continue

        run = scenes[run_start:i]
        if len(run) > max_run:
            middle = run_start + len(run) // 2
            # Search outward from the middle for a beat that can carry a hit.
            for offset in range(len(run) // 2 + 1):
                for j in (middle - offset, middle + offset):
                    if run_start <= j < i and scenes[j]["accent"]:
                        scenes[j]["visual"] = "impact"
                        log.info("script_gen: %d plain beats in a row — scene %d promoted",
                                 len(run), j)
                        run_start = i + 1
                        break
                else:
                    continue
                break
            else:
                log.info("script_gen: %d plain beats in a row from %d, none had an accent",
                         len(run), run_start)
        run_start = i + 1


def _enforce_shot_variety(scenes: list[dict]) -> None:
    """
    Break up runs of the same shot type, in place.

    The model reliably ignores "vary the framing" and returns twelve wides,
    which is exactly what made the last render look flat. Asking is not enough;
    this makes it true. Rotation order goes wide → threshold → detail → pov →
    object, so a forced replacement is still a sensible framing for the line
    rather than a random one.
    """
    rotation = ("wide", "threshold", "detail", "pov", "object")

    for i in range(2, len(scenes)):
        window = {scenes[i - 2]["shot"], scenes[i - 1]["shot"], scenes[i]["shot"]}
        if len(window) > 1:
            continue
        current = scenes[i]["shot"]
        scenes[i]["shot"] = rotation[(rotation.index(current) + 1) % len(rotation)]
        log.info("script_gen: scene %d shot %s → %s (three in a row)",
                 i, current, scenes[i]["shot"])

    # At least a third of beats should be close: wides carry no detail, and a
    # short made entirely of establishing shots has no sense of scale.
    close = {"detail", "object"}
    quota = max(1, len(scenes) // 3)
    if sum(1 for s in scenes if s["shot"] in close) >= quota:
        return

    # Convert the widest interior beats first, never the opener or the closer.
    for scene in scenes[1:-1]:
        if sum(1 for s in scenes if s["shot"] in close) >= quota:
            break
        if scene["shot"] == "wide":
            scene["shot"] = "detail"
            log.info("script_gen: forced a detail shot to meet the close-shot quota")


def generate_script(
    niche: dict,
    story_seed: str,
    conn: sqlite3.Connection,
    video_id: int,
    cfg=None,
    myth_type: str | None = None,
    content_type: str = "story",
) -> dict:
    """
    Generate a full video script for a niche.

    Args:
        niche:        Niche config dict from settings.json.
        story_seed:   One-line story concept. Empty = LLM picks within the niche.
        conn:         SQLite connection.
        video_id:     DB video row id.
        cfg:          Config singleton.
        myth_type:    For mythology niche: "hindu" | "norse" | "egypt" | "greek".
                      Determines sub-label used in the prompt.
        content_type: "story" | "teachings" | "facts". Controls narrative format.

    Returns:
        {"niche_id", "story_title", "scene_count", "script_schema", "scenes": [...]}

        Scene shape depends on the niche's `script_schema`:
          "image"             → {"narration", "image_prompt"}   (default)
          "procedural_scary"  → {"narration", "visual", "accent", "repeat"}

    Raises:
        RuntimeError on LLM failure or bad JSON.
    """
    from llm_router import call_llm

    cfg_router  = cfg.llm_router if cfg else {}
    min_scenes  = _niche_bound(niche, cfg, "min_scenes", 8)
    max_scenes  = _niche_bound(niche, cfg, "max_scenes", 15)
    min_dur     = _niche_bound(niche, cfg, "min_duration_sec", 90)
    max_dur     = _niche_bound(niche, cfg, "max_duration_sec", 180)
    schema      = niche.get("script_schema", "image")

    niche_id    = niche.get("id", "unknown")
    niche_label = niche.get("label", niche_id)
    tone        = niche.get("tone", "dramatic")
    art_style   = niche.get("art_style_prompt_suffix", "")
    default_rules = "Specify the main subject prominently in the foreground."
    image_prompt_rules = niche.get("image_prompt_rules", default_rules)

    # Apply mythology sub-type label if provided
    if myth_type and niche_id == "mythology":
        sub = niche.get("sub_types", {}).get(myth_type, {})
        if sub:
            niche_label = sub.get("label", niche_label)
            tone        = sub.get("tone", tone)

    # Content format directive
    if content_type == "teachings":
        format_note = (
            "Format: philosophical teachings and wisdom. Each scene reveals a profound lesson "
            "or truth from the source material. Narration speaks directly to the viewer "
            "as if sharing ancient wisdom."
        )
    elif content_type == "facts":
        format_note = (
            "Format: fascinating facts and explanations. Each scene presents a surprising or "
            "illuminating piece of knowledge about the mythology. Educational but gripping."
        )
    else:
        format_note = (
            "Format: dramatic narrative story with rising tension and resolution."
        )

    story_directive = (
        f'Base the content on: "{story_seed}"'
        if story_seed.strip()
        else f"Choose an engaging, original {niche_label} topic."
    )

    system = (
        "You are a scriptwriter for short-form vertical social media story videos. "
        "You always respond with valid JSON only — no markdown fences, no extra text."
    )

    if schema == "cinematic_scary":
        style_note = (
            f"Each scene shows a generated photographic image of what is being described, "
            f"graded dark with fog, film grain and a vignette, with horror typography over it. "
            f"Art style applied automatically: {art_style}"
        )
        json_block = """{
  "story_title": "<compelling title, max 8 words>",
  "scenes": [
    {
      "narration": "<what the narrator says, told aloud, max 20 words, ending in . ! or ?. Fragments are allowed and wanted — see the narrator voice rules.>",
      "image_prompt": "<LITERAL description of what the viewer must SEE for this exact line. Name the place, every object the narration mentions, and the EXACT COUNT of them. Name the light source and the weather. 1-3 sentences. No camera or art-style words — those are added automatically from \\"shot\\".>",
      "shot": "<one of: wide | threshold | detail | pov | object>",
      "visual": "<one of: hook | line | impact | reveal | scare | end>",
      "accent": "<optional 1-4 word phrase shown as huge on-screen type, e.g. '3:07 AM' or 'KNOCK.' or 'FROM INSIDE.'. Omit or leave empty for plain scenes.>",
      "reveal_prompt": "<second image for this beat, cut to partway through it. Required when visual is impact, reveal or scare — describe the single closest detail of what the line is about. Omit otherwise.>",
      "repeat": 1
    }
  ]
}"""
        rules = f"""The image and the narration must match exactly. If the line mentions five
tents, the image_prompt says five tents. If the line says it was cold outside the
door, the image_prompt describes that door with mist beyond it. Never write a
generic mood image — every noun the narrator says should be visible.
{image_prompt_rules}

Camera framings ("shot") — this is how far the camera is, not what is in frame:
  wide      — the whole location at once. Establishing.
  threshold — looking through a doorway, window or gap, into the next space.
  detail    — one small object filling the frame. A lock, a handprint, a phone screen.
  pov       — what the person in the story is looking at, from where they stand.
  object    — a single object at arm's length, background out of focus.

Vary the framing constantly. Never use the same "shot" three beats in a row, and
never open more than two beats in the whole script with "wide". A short made of
twelve wide shots is boring no matter how good the story is. At least a third of
the beats must be "detail" or "object" — close shots are what create unease.

Scene templates ("visual") — this is what the typography does:
  hook   — opening beat. Huge accent (a time, a number, a short phrase). Use for scene 1 only.
  line   — plain narrated beat, atmosphere only, no on-screen accent. Use for most scenes.
  impact — one short accent word punched onto screen "repeat" times (1-3), e.g. "KNOCK.".
  reveal — a slit of light widening, like a door opening. One accent phrase.
  scare  — the turn. Hard-cut capitals over a red wash. Use once, near the end.
  end    — closing card. Short accent call-to-action. Use for the final scene only.

Use {min_scenes} to {max_scenes} scenes. Total narration must run {min_dur}-{max_dur} seconds when spoken aloud.
Scene 1 must be "hook" and the final scene must be "end".
Use "line" for the majority of scenes — accents are punctuation, not wallpaper —
but never more than three "line" scenes in a row: a stretch that long with no
typography is where the viewer leaves.

Write an "accent" for at least half the scenes, including "line" ones. A "line"
scene never draws its accent, so an unused one costs nothing — but it is what
lets a scene be promoted if the script turns out to run too long without a beat.
A scene with no accent can never carry one.

"accent" and "narration" must never contain the same words. The accent is drawn
as huge type while the narration is spoken and captioned underneath it, so an
accent that repeats its own line shows the viewer the same words twice. If the
line says "At 3:14 AM the lock clicked open", the accent is NOT "3:14 AM" — it is
something the line does not say, like "IT OPENED" — or it is left empty.

Structure, in this order:
  1. Scene 1 is the cold open: at most 12 words, and it starts already inside
     the situation — no scene-setting, no "it was a dark night". Put the
     concrete hook — a clock time, a number, a place name — in scene 1's
     "accent", and let the narration carry a *different* concrete detail.
  2. The beat closest to the middle of the script is a "impact" turn — the line
     that tells the viewer the story is not what they thought it was.
  3. The "scare" beat is the twist. Everything before it exists to set it up.
  4. The final line must loop: it should mean something different if the viewer
     immediately rewatches scene 1.

Build dread beat by beat. Concrete, specific, mundane details are scarier than
adjectives — a wet footprint beats "an unspeakable horror".

NARRATOR VOICE — this is someone telling you a scary story in a dark room, not a
report being read out. The single most common failure is narration that is
grammatically perfect, evenly paced and emotionally flat: "I found single
barefoot tracks in the fresh snow leading toward my porch." That is a witness
statement. Write it the way it would be *said*:

  - Break the rhythm. Most beats are one short sentence. Some are a fragment of
    three or four words on their own: "Barefoot. In the snow." A script where
    every line is the same length has no pulse, and the voice reads it flat.
  - Use commas and full stops as breathing, not as grammar. The narrator pauses
    mid-line for effect: "The door was open. Wide open." Every comma and stop
    becomes a real pause when this is spoken, so punctuate for delivery.
  - Talk to the viewer. Second person and direct address are allowed and should
    appear a few times: "You know that sound a house makes when it settles.
    This wasn't that."
  - Understate the horror. The narrator stays calm while describing something
    that should not be calm — that gap is the fear. Never announce the emotion
    ("I was terrified", "it was horrifying"); describe the detail that caused it
    and let the viewer supply the feeling.
  - Withhold. Name the thing one beat later than you want to. Say what it was
    not before you say what it was.
  - No literary narration. No "an eerie silence descended", no "little did I
    know", no "unbeknownst to me". Plain spoken words only.
  - The scare beat is the shortest line in the script. Fear is not verbose."""
    else:
        style_note = f"Art style (for reference, do NOT include in narration): {art_style}"
        json_block = f"""{{
  "story_title": "<compelling title, max 8 words>",
  "scenes": [
    {{
      "narration": "<what the narrator says for this scene, 1-2 complete sentences with proper punctuation ending in . ! or ?, max 20 words each>",
      "image_prompt": "<detailed visual description for AI image generation, 1-3 sentences, no art-style words (added automatically). {image_prompt_rules}>"
    }}
  ]
}}"""
        rules = f"""Use {min_scenes} to {max_scenes} scenes. Each scene is 3-8 seconds of screen time.
Build narrative tension across scenes. Final scene should resolve or land with impact.
Narration must be tight — total video is {min_dur}-{max_dur} seconds.
Make the story genuinely interesting: unexpected twists, real tension, vivid details.
Avoid generic openings — hook the viewer in scene 1."""

    user_prompt = f"""Write a {niche_label} video script.

Tone: {tone}
{format_note}
{story_directive}
{style_note}

Respond with exactly this JSON structure:
{json_block}

{rules}"""

    raw, model_used = call_llm(user_prompt, system=system, cfg_router=cfg_router, temperature=0.85)

    try:
        data = _extract_json(raw)
    except ValueError as e:
        raise RuntimeError(f"script_gen: JSON parse failed. model={model_used} error={e}")

    story_title = str(data.get("story_title", f"Untitled {niche_label} Story")).strip()
    scenes_raw  = data.get("scenes", [])

    if not scenes_raw or not isinstance(scenes_raw, list):
        raise RuntimeError(f"script_gen: no scenes in LLM response. raw={raw[:300]}")

    # ── Log decisions BEFORE returning (the guardrail) ───────────────────────
    _log_decision(
        conn, video_id,
        "script_generation",
        story_title,
        f"niche={niche_id} seed={story_seed[:60] or 'random'} scenes_requested={min_scenes}-{max_scenes}",
        model_used,
    )

    # ── Normalise scenes ─────────────────────────────────────────────────────
    scenes = []
    for s in scenes_raw[:max_scenes]:
        narration = str(s.get("narration", "")).strip()

        if schema == "cinematic_scary":
            if not narration:
                log.warning("script_gen: skipping scene with no narration: %s", s)
                continue
            scene = _normalise_procedural_scene(s, narration)
            # Keep image_prompt alongside the template fields: this schema drives
            # both the generated backdrop and the typography layer over it.
            scene["image_prompt"] = str(s.get("image_prompt", "")).strip()
            scene["reveal_prompt"] = str(s.get("reveal_prompt", "")).strip()
            scenes.append(scene)
            continue

        image_prompt = str(s.get("image_prompt", "")).strip()

        if not narration or not image_prompt:
            log.warning("script_gen: skipping incomplete scene: %s", s)
            continue

        scenes.append({"narration": narration, "image_prompt": image_prompt})

    if not scenes:
        raise RuntimeError("script_gen: all scenes invalid after parsing")

    if schema == "cinematic_scary":
        # The renderer relies on a real opening and closing card regardless of
        # what the model chose.
        scenes[0]["visual"] = "hook"
        scenes[-1]["visual"] = "end"

        # A scene with no image_prompt would render as a black hole between two
        # photographic frames. Fall back to the narration itself.
        for sc in scenes:
            if not sc["image_prompt"]:
                sc["image_prompt"] = sc["narration"]
                log.warning("script_gen: scene had no image_prompt — using narration")

        # Never show the same words twice. The accent is huge type; the caption
        # band already spells the narration out underneath it. This has to be
        # enforced here rather than asked for in the prompt — the prompt has
        # asked for it all along and the model does it anyway, because the
        # cold-open rule pushes a concrete time into scene 1's narration and the
        # obvious accent for that scene is the same time.
        for i, sc in enumerate(scenes):
            if sc["accent"] and _accent_is_spoken(sc["accent"], sc["narration"]):
                log.info("script_gen: scene %d accent %r is spoken in its own line — dropped",
                         i, sc["accent"])
                sc["accent"] = ""

        # An accent-less `impact` is a flash and a camera shake with no word to
        # show. The model returns one often enough to be worth normalising away
        # rather than rendering — and the duplication guard above can create one.
        for i, sc in enumerate(scenes[1:-1], start=1):
            if sc["visual"] in ("impact", "scare") and not sc["accent"]:
                was = sc["visual"]
                sc["visual"] = "line"
                log.info("script_gen: scene %d was %r with no accent → 'line'", i, was)

        # The opener and the closer read as the two ends of the same frame, so
        # they get the two framings that bookend well regardless of content.
        scenes[0]["shot"] = "wide"
        scenes[-1]["shot"] = "threshold"
        _enforce_shot_variety(scenes)
        _break_line_runs(scenes)

        # The mid-script turn. The model puts it wherever it likes, or nowhere;
        # this guarantees the video has a second hook halfway through, which is
        # where short-form retention actually falls off.
        #
        # Only a scene that already carries an accent can be promoted: `impact`
        # punches a word onto the screen, and without one it fires a flash and a
        # camera shake with nothing to show, which reads as a glitch.
        mid = len(scenes) // 2
        if 0 < mid < len(scenes) - 1:
            candidates = [mid, mid - 1, mid + 1]
            for j in candidates:
                if scenes[j]["visual"] == "line" and scenes[j]["accent"]:
                    scenes[j]["visual"] = "impact"
                    log.info("script_gen: promoted scene %d to the mid-script turn", j)
                    break
            else:
                if not any(s["visual"] == "impact" for s in scenes[1:-1]):
                    log.info("script_gen: no mid-script turn — no candidate beat had an accent")

        # Dramatic beats need their second image; without one the mid-beat cut
        # degrades to a crop change and the hit lands softer.
        for i, sc in enumerate(scenes):
            if sc["visual"] in _REVEAL_TEMPLATES and not sc["reveal_prompt"]:
                sc["reveal_prompt"] = sc["image_prompt"]
                log.info("script_gen: scene %d had no reveal_prompt — reusing image_prompt", i)

    if len(scenes) < min_scenes:
        log.warning(
            "script_gen: only %d scenes returned (min=%d) — proceeding anyway",
            len(scenes), min_scenes,
        )

    scene_count = len(scenes)

    # ── Update videos row ────────────────────────────────────────────────────
    conn.execute(
        """UPDATE videos SET niche_id=?, scene_count=?, prompt=? WHERE id=?""",
        (niche_id, scene_count, story_seed or f"[random {niche_label}]", video_id),
    )
    conn.commit()

    log.info(
        "Script generated: video_id=%d niche=%s title=%r scenes=%d model=%s",
        video_id, niche_id, story_title, scene_count, model_used,
    )

    return {
        "niche_id":      niche_id,
        "story_title":   story_title,
        "scene_count":   scene_count,
        "script_schema": schema,
        "scenes":        scenes,
    }
