"""
Exercise the Remotion renderer without the LLM or TTS.

Two modes:

    python scripts/test_remotion_render.py --write-sample
        Regenerate remotion-scary/src/sample-props.json from the real props
        builder. Run this whenever the props schema changes so the checked-in
        sample can never drift from what the pipeline actually emits.

    python scripts/test_remotion_render.py --slug scary_stories_foo_79
        Re-render an existing run from its saved script + audio. This is the
        fast iteration loop — no LLM call, no TTS, seconds per attempt.
"""

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import cfg  # noqa: E402
from pipeline.image_post import is_worth_cutting_to  # noqa: E402
from pipeline.remotion_renderer import (  # noqa: E402
    RemotionRenderError, build_props, preflight, render_with_remotion,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("test_remotion_render")

_REPO = Path(__file__).resolve().parent.parent

# One scene per template, at deliberately uneven lengths so the timing
# reparameterization gets exercised at both extremes (2.5s and 9s).
_SAMPLE_SCENES = [
    ("hook",   "3:07 AM",      2.5, "Every night, at exactly seven minutes past three."),
    ("line",   "",             6.0, "I live alone on the fourth floor. There are no neighbours left on this side."),
    ("line",   "",             4.5, "The building manager says the flat next door has been empty for years."),
    ("impact", "KNOCK.",       5.0, "Three knocks. Always three. Never four, never two."),
    ("line",   "",             3.5, "I stopped counting the nights after the first month."),
    ("reveal", "I opened it.", 9.0, "Last night I finally walked to the door, put my hand on the latch, and opened it wide."),
    ("line",   "",             4.0, "The corridor light was still broken. The air was cold enough to see."),
    ("impact", "NOTHING.",     3.0, "There was nobody there."),
    ("line",   "",             5.5, "I stood in the doorway for a long time, listening to the building settle."),
    ("scare",  "FROM INSIDE.", 7.0, "Then the knocking started again, and this time it was behind me."),
    ("line",   "",             4.0, "I have not opened that door since."),
    ("end",    "DON'T ANSWER", 6.0, "If you hear three knocks tonight, do not answer."),
]


def _synthetic_word_timings(scenes: list[tuple], out_path: Path) -> Path:
    """
    Fake edge_tts word boundaries: every word in a scene spread evenly across
    that scene's duration. Enough to exercise the caption chunking and the
    highlight without needing a real mp3 in git.
    """
    words: list[dict] = []
    clock = 0.0
    for _v, _a, dur, narration in scenes:
        tokens = narration.split()
        if tokens:
            per = dur / len(tokens)
            for tok in tokens:
                words.append({
                    "text": tok,
                    "offset": int(clock * 10_000_000),
                    "duration": int(per * 0.85 * 10_000_000),
                })
                clock += per
        else:
            clock += dur

    out_path.write_text(json.dumps(words), encoding="utf-8")
    return out_path


def _sample_niche() -> dict:
    for niche in cfg.niches:
        if niche.get("renderer") == "remotion":
            return niche
    raise SystemExit("No niche in settings.json has renderer='remotion'.")


def write_sample() -> Path:
    import tempfile

    niche = _sample_niche()
    scenes = [{"visual": v, "accent": a, "narration": n, "repeat": 3 if v == "impact" else 1}
              for v, a, _d, n in _SAMPLE_SCENES]
    durations = [d for _v, _a, d, _n in _SAMPLE_SCENES]

    with tempfile.TemporaryDirectory() as tmp:
        timings = _synthetic_word_timings(_SAMPLE_SCENES, Path(tmp) / "word_timings.json")
        props = build_props(
            scenes=scenes,
            audio_src=None,          # silent: the sample needs no binary in git
            word_timings_path=str(timings),
            scene_durations=durations,
            cfg=cfg,
            niche=niche,
            title="The Sample That Knocks",
            seed=1,
        )

    project_dir = _REPO / niche.get("remotion", {}).get("project_dir", "remotion-scary")
    out = project_dir / "src" / "sample-props.json"
    out.write_text(json.dumps(props, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    total = props["audioDurationInFrames"]
    log.info("Wrote %s", out)
    log.info("  %d scenes, %d frames (%.1fs), transition %d, %d caption chunks",
             len(props["scenes"]), total, total / 30,
             props["transitionFrames"], len(props["captions"]))
    return out


def render_slug(slug: str) -> str:
    niche = _sample_niche()

    script_path = Path(cfg.paths["scripts"]) / f"{slug}.json"
    if not script_path.is_file():
        raise SystemExit(f"No saved script at {script_path}")

    payload = json.loads(script_path.read_text(encoding="utf-8"))
    script = payload["script"]
    # The saved niche is kept for the script it produced, but the point of
    # re-rendering an old run is to see it through today's renderer — so current
    # settings.json wins on anything they both define.
    saved_niche = {**(payload.get("niche") or {}), **niche}

    # Per-scene synthesis writes a WAV (concatenated in the sample domain, so no
    # MP3 encoder delay); the older single-pass path writes an MP3.
    audio_dir = Path(cfg.paths["audio"]) / slug
    audio_path = next(
        (p for p in (audio_dir / "voice.wav", audio_dir / "voice.mp3") if p.is_file()),
        None,
    )
    if audio_path is None:
        raise SystemExit(f"No narration (voice.wav or voice.mp3) in {audio_dir}")

    import subprocess

    from pipeline.scene_timing import compute_scene_durations

    word_timings = audio_dir / "word_timings.json"
    # Duration straight from the file rather than re-running TTS.
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(audio_path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip())

    durations = compute_scene_durations(script["scenes"], str(word_timings), dur)

    # Reuse the images the original run generated, in scene order. Without
    # these the re-render silently produces the imageless procedural look,
    # which is not what the pipeline actually ships.
    img_dir = Path(cfg.paths["images"]) / slug
    _exts = {".jpg", ".jpeg", ".png", ".webp"}
    scene_images = [
        str(p) for p in sorted(img_dir.glob("scene_*"))
        if p.suffix.lower() in _exts and not p.stem.endswith("_b")
    ]

    # The script is saved before the image stage runs, so `_image_b` is never in
    # it. Recover the beats' second images from disk instead, or the re-render
    # silently loses every hard cut.
    for i, scene in enumerate(script["scenes"]):
        extra = next(
            (str(p) for p in sorted(img_dir.glob(f"scene_{i:02d}_b.*"))
             if p.suffix.lower() in _exts),
            None,
        )
        if extra and i < len(scene_images) and is_worth_cutting_to(extra, scene_images[i]):
            scene["_image_b"] = extra
    if scene_images:
        log.info("Reusing %d generated scene images from %s", len(scene_images), img_dir)
    else:
        log.warning("No scene images in %s — rendering without backdrops", img_dir)

    out_path = str(Path(cfg.paths["video"]) / f"{slug}_remotion.mp4")
    return render_with_remotion(
        scene_images=scene_images,
        audio_path=str(audio_path),
        output_path=out_path,
        scenes=script["scenes"],
        cfg=cfg,
        word_timings_path=str(word_timings),
        scene_durations=durations,
        niche=saved_niche,
        title=script.get("story_title", ""),
        seed=payload.get("video_id", 0),
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write-sample", action="store_true",
                    help="regenerate remotion-scary/src/sample-props.json")
    ap.add_argument("--slug", help="re-render an existing run by output slug")
    ap.add_argument("--preflight", action="store_true",
                    help="only check that the Remotion project can run")
    args = ap.parse_args()

    if args.preflight:
        project_dir, entry, launcher = preflight(_sample_niche())
        log.info("project : %s", project_dir)
        log.info("entry   : %s", entry)
        log.info("launcher: %s", " ".join(launcher))
        return

    if args.write_sample:
        write_sample()
        if not args.slug:
            return

    if args.slug:
        try:
            log.info("Rendered: %s", render_slug(args.slug))
        except RemotionRenderError as e:
            log.error("%s", e)
            sys.exit(1)
        return

    ap.print_help()


if __name__ == "__main__":
    main()
