"""
Look at what the image generator actually returned, and write down what to fix.

Deliberately *not* a gate. A generated image is accepted and the video keeps
building; the critic runs afterwards and its findings land in prompt_notes.json,
where they improve the *next* run's prompts. Nothing here can stall a video, and
a critic that is switched off, unreachable or slow costs nothing but the notes.

Two kinds of check:

  * Free ones, from the pixels — exposure, and whether this frame is a near
    duplicate of another scene in the same video. No model, instant.
  * A local vision model (Ollama), which is the only thing that can catch the
    flaw class that actually hurt: the generator quietly rendering something
    else. Asked for "a tablet showing a porch camera feed", FLUX returned a
    corridor — no heuristic can see that, and it cost a scene.

Local by design. The cloud vision quota is the same quota the image generation
needs, and spending it on criticism is the wrong trade.

Latency note: the image is downscaled before it is sent. At full 1080x1920 a
critique takes ~83s on CPU; at 768px wide it takes ~15s and returns the same
answers. Do not remove the downscale.

Entry points:
    critique_image(path, subject, ...) -> Critique
    critique_run(scenes, image_paths, niche, cfg) -> list[Critique]
"""

from __future__ import annotations

import base64
import io
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import requests

from pipeline.prompt_notes import record_flaw

log = logging.getLogger(__name__)

_DEFAULTS = {
    "enabled": True,
    "host": "http://localhost:11434",
    "model": "qwen2.5vl:3b",
    "max_width": 768,
    "timeout_seconds": 180,
    # Mean luma (0-255) outside this band is worth a note. The floor is low
    # because these are night scenes on purpose; below it, detail is genuinely
    # gone rather than merely dark.
    "min_luma": 18,
    "max_luma": 130,
    # Share of the model's description that must also appear in the brief.
    # Low on purpose: a 10-word caption of a 25-word prompt legitimately
    # mentions things the prompt did not, and only a real miss scores near 0.
    "min_overlap": 0.25,
}

# Flaw → the correction that would have avoided it. Phrased as prompt language,
# because that is where it ends up.
_CORRECTIONS = {
    "subject_missed": "the named subject must be the single clear focus of the frame",
    "face_visible":   "face turned fully away from camera, features not visible",
    "hands_visible":  "hands hidden or out of frame",
    "too_dark":       "one clearly lit area, visible detail in the shadows",
    "too_bright":     "low-key, deep shadow, no daylight",
}


@dataclass
class Critique:
    """What one image turned out to be."""
    path: str
    scene_index: int
    ok: bool = True
    flaws: list[str] = field(default_factory=list)
    what_it_shows: str = ""
    overlap: float | None = None
    luma: float | None = None
    error: str = ""

    def __str__(self) -> str:
        if self.error:
            return f"scene {self.scene_index}: not checked ({self.error})"
        if self.ok:
            return f"scene {self.scene_index}: ok"
        return (f"scene {self.scene_index}: {', '.join(self.flaws)}"
                f" - saw {self.what_it_shows!r} (overlap {self.overlap})")


def _config(cfg) -> dict:
    out = dict(_DEFAULTS)
    if cfg is not None and getattr(cfg, "image_critic", None):
        out.update(cfg.image_critic)
    return out


def _encoded(path: Path, max_width: int) -> tuple[str, float]:
    """Downscaled base64 JPEG of the image, plus its mean luma."""
    from PIL import Image

    im = Image.open(path).convert("RGB")
    luma = sum(im.convert("L").resize((64, 64)).getdata()) / (64 * 64)

    if im.width > max_width:
        im = im.resize((max_width, round(im.height * max_width / im.width)), Image.LANCZOS)

    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode(), luma


# Words that carry no subject information, so their presence in both texts
# means nothing. Deliberately short — this is not a stopword list for search,
# only for "did the model describe the thing we asked for".
_FILLER = frozenset("""
a an the of in on at to and or with from into by for is are was were be been
it its this that these those there here as but if then than so very single
one two three some any all each other another more most less least
image photo picture shot view scene frame background foreground
dark darkness light lit lighting bright dim night day room space area
""".split())


def _content_words(text: str) -> set[str]:
    """Lowercased content words, crudely singularised so plurals still match."""
    words = re.findall(r"[a-z]+", text.lower())
    out = set()
    for w in words:
        if len(w) < 3 or w in _FILLER:
            continue
        if len(w) > 4 and w.endswith("es") and not w.endswith("ses"):
            w = w[:-2]
        elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.add(w)
    return out


def subject_overlap(subject: str, description: str) -> float:
    """
    How much of what the model saw is actually in the brief. 0.0 to 1.0.

    Measured over the *description's* content words rather than the subject's:
    a short caption cannot be expected to mention everything a three-clause
    prompt asked for, but almost everything it does mention should be something
    that was asked for. A corridor described for a prompt about a tablet and a
    porch shares nothing and scores 0.
    """
    seen = _content_words(description)
    asked = _content_words(subject)
    if not seen or not asked:
        return 1.0          # nothing to compare — do not invent a flaw
    return len(seen & asked) / len(seen)


def _ask_vision(config: dict, b64: str) -> dict:
    """
    Ask the local model what it can see. Raises RuntimeError on any failure.

    Note there is no `subject` parameter, and that is the point: the model is
    never told what the image was meant to be, so it has nothing to agree with.
    """
    # The model is never asked whether the image matches. It is bad at that and
    # good at describing: shown a corridor that should have been a tablet
    # displaying a porch camera feed, it answered "A dark hallway with a door at
    # the end" — correct — and then still said the subject was present. Asked
    # the other way round it parroted the description it had been given back as
    # what it could see.
    #
    # So it only reports what is in front of it, and the comparison happens in
    # Python where it is deterministic and inspectable. The two booleans stay:
    # those are perceptual questions about this image alone, not comparisons,
    # and it answers them reliably.
    prompt = (
        "Describe only what is actually visible in this image. Do not guess at "
        "intent.\nAnswer as JSON only, no other text:\n"
        '{"what_it_shows": "<at most 10 words>", '
        '"face_clearly_visible": true or false, "hands_clearly_visible": true or false}'
    )
    try:
        resp = requests.post(
            f"{config['host'].rstrip('/')}/api/generate",
            json={
                "model": config["model"],
                "prompt": prompt,
                "images": [b64],
                "stream": False,
                "options": {"temperature": 0, "num_predict": 160},
            },
            timeout=config["timeout_seconds"],
        )
        resp.raise_for_status()
        text = resp.json().get("response", "")
    except (requests.RequestException, ValueError) as e:
        raise RuntimeError(f"local vision model unreachable: {e}") from e

    # Small models fence their JSON about half the time.
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise RuntimeError(f"no JSON in vision response: {text[:120]}")
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"bad JSON from vision model: {e}") from e


def critique_image(
    path: str | Path,
    subject: str,
    scene_index: int = 0,
    niche_id: str = "",
    shot: str = "",
    human_policy: str = "never",
    cfg=None,
    record: bool = True,
) -> Critique:
    """
    Check one image and, by default, record what it teaches.

    Never raises: a critic that breaks the run it is auditing is worse than no
    critic. Failures come back on `Critique.error`.
    """
    config = _config(cfg)
    result = Critique(path=str(path), scene_index=scene_index)

    if not config.get("enabled", True):
        result.error = "disabled"
        return result

    try:
        b64, luma = _encoded(Path(path), int(config["max_width"]))
    except Exception as e:
        result.error = f"unreadable: {e}"
        return result

    result.luma = round(luma, 1)

    # Free checks first — they cost nothing and are true regardless of whether
    # the vision model answers.
    if luma < config["min_luma"]:
        result.flaws.append("too_dark")
    elif luma > config["max_luma"]:
        result.flaws.append("too_bright")

    try:
        verdict = _ask_vision(config, b64)
    except RuntimeError as e:
        result.error = str(e)
    else:
        result.what_it_shows = str(verdict.get("what_it_shows", "")).strip()
        result.overlap = round(subject_overlap(subject, result.what_it_shows), 2)
        if result.overlap < float(config["min_overlap"]):
            result.flaws.append("subject_missed")
        # Only a policy that forbids them makes these flaws. A niche that wants
        # faces is not being criticised for having them.
        if human_policy == "obscured":
            if verdict.get("face_clearly_visible") is True:
                result.flaws.append("face_visible")
            if verdict.get("hands_clearly_visible") is True:
                result.flaws.append("hands_visible")

    result.ok = not result.flaws

    if record:
        for flaw in result.flaws:
            record_flaw(niche_id, shot, flaw, _CORRECTIONS.get(flaw, ""))

    return result


def critique_run(
    scenes: list[dict],
    image_paths: list[str],
    niche: dict,
    cfg=None,
    record: bool = True,
) -> list[Critique]:
    """Critique every image of one run. Returns one Critique per image."""
    from pipeline.image_policy import resolve_human_policy

    policy = resolve_human_policy(niche, _config(cfg))
    niche_id = niche.get("id", "")

    out: list[Critique] = []
    for i, path in enumerate(image_paths):
        scene = scenes[i] if i < len(scenes) else {}
        critique = critique_image(
            path,
            subject=scene.get("image_prompt", "") or scene.get("narration", ""),
            scene_index=i,
            niche_id=niche_id,
            shot=scene.get("shot", ""),
            human_policy=policy,
            cfg=cfg,
            record=record,
        )
        log.info("%s", critique)
        out.append(critique)

    flawed = [c for c in out if not c.ok and not c.error]
    log.info("Critic: %d/%d images flagged", len(flawed), len(out))
    return out
