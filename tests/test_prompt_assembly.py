"""
Prompt assembly, the human policy, and the critic's subject matching.

The expensive lessons behind these are all empirical — measured by generating
the same scene many ways — so they are pinned here. Every constant they check
was chosen because the alternative visibly failed.
"""

import re

import pytest

from pipeline.image_critic import subject_overlap
from pipeline.image_gen import _LOOKS, build_positive_prompt, build_style_token
from pipeline.image_policy import (
    apply_human_policy,
    resolve_human_policy,
    sanitize_obscured,
)

NICHE = {
    "id": "scary_stories",
    "art_style_prompt_suffix": "cinematic horror photograph, photorealistic, real location",
}
SUBJECT = "A figure at the end of a dark hallway, one bare bulb overhead."


# ── Prompt assembly ───────────────────────────────────────────────────────────

def test_flux_prompt_stays_under_the_attention_cliff():
    """
    FLUX-family models truncate around 77 CLIP tokens.

    Measured: a ~30-word look block plus the full negative list produced a
    111-word prompt that rendered a corridor and an alley for a brief about
    rain-streaked bedroom windows. Roughly 60 words is the working ceiling.
    """
    prompt = build_positive_prompt(
        SUBJECT, NICHE, "threshold", build_style_token(0),
        provider_type="pollinations", human_policy="obscured",
    )
    assert len(prompt.split()) <= 60


def test_flux_prompt_leads_with_the_look():
    """Look first, subject after — the ordering the frame test settled on."""
    look = build_style_token(0)
    prompt = build_positive_prompt(
        SUBJECT, NICHE, "wide", look, provider_type="pollinations",
    )
    assert prompt.startswith(look)
    assert prompt.index(look) < prompt.index("figure")


def test_every_look_block_is_short():
    """A long look block wins the frame and loses the subject. Keep them terse."""
    for look in _LOOKS:
        assert len(look.split()) <= 14, look


def test_pollinations_gets_constraints_positively():
    """It has no negative channel, so a constraint must ride in the prompt."""
    prompt = build_positive_prompt(
        SUBJECT, NICHE, "wide", "", provider_type="pollinations", human_policy="obscured",
    )
    assert "no face" in prompt and "no hands" in prompt
    assert "no daylight" in prompt


def test_gemini_gets_the_art_style_and_flux_does_not():
    """Gemini reads the whole prompt; on FLUX those words cost the subject."""
    gemini = build_positive_prompt(SUBJECT, NICHE, "wide", "", provider_type="gemini")
    flux = build_positive_prompt(SUBJECT, NICHE, "wide", "", provider_type="pollinations")
    assert "photorealistic" in gemini
    assert "photorealistic" not in flux


def test_the_two_providers_carry_the_same_subject():
    """A fallback must ask for the same picture, not a different one."""
    for provider in ("gemini", "pollinations", "together_ai"):
        prompt = build_positive_prompt(
            SUBJECT, NICHE, "wide", build_style_token(3), provider_type=provider,
        )
        assert "bare bulb overhead" in prompt


def test_notes_go_last():
    """Corrections refine the prompt; they must not compete with the subject."""
    prompt = build_positive_prompt(
        SUBJECT, NICHE, "wide", "", provider_type="pollinations",
        notes="one clearly lit area",
    )
    assert prompt.endswith("one clearly lit area")


# ── Human policy ──────────────────────────────────────────────────────────────

def test_obscured_keeps_the_figure_and_drops_the_face():
    out = sanitize_obscured(
        "A figure at the end of a hallway, face lit by a phone, hands on the door"
    )
    assert "figure" in out
    assert "face lit" not in out
    assert "hands on the door" not in out


def test_obscured_adds_both_sides_of_the_rule():
    positive, negative = apply_human_policy("A figure in a doorway", "blurry", "obscured")
    assert "face not visible" in positive
    assert "deformed hands" in negative


def test_never_still_removes_people():
    positive, negative = apply_human_policy("A man in a doorway", "blurry", "never")
    # Word-boundary check: "man" is a substring of the "no humans" suffix.
    assert not re.search(r"\bman\b", positive)
    assert "doorway" not in positive
    assert "no people" in positive
    assert "person" in negative


def test_none_leaves_the_prompt_alone():
    positive, negative = apply_human_policy("A man in a doorway", "blurry", "none")
    assert positive == "A man in a doorway"
    assert negative == "blurry"


@pytest.mark.parametrize("niche,config,expected", [
    ({"human_policy": "obscured"}, {}, "obscured"),
    ({"no_humans": False}, {}, "none"),
    ({}, {"no_humans": True}, "never"),
    ({}, {}, "never"),
    ({"human_policy": "nonsense"}, {}, "never"),
    # An explicit human_policy outranks the legacy boolean.
    ({"human_policy": "obscured", "no_humans": True}, {}, "obscured"),
])
def test_policy_resolution(niche, config, expected):
    assert resolve_human_policy(niche, config) == expected


# ── Subject matching ──────────────────────────────────────────────────────────

def test_overlap_catches_a_real_miss():
    """The failure that started this: a tablet-and-porch brief, a corridor back."""
    score = subject_overlap(
        "A tablet screen displaying a security camera feed of an empty wooden porch, "
        "illuminated by a single overhead yellow porch light.",
        "A dark hallway with a door at the end",
    )
    assert score == pytest.approx(0.0)


def test_overlap_passes_a_good_match():
    score = subject_overlap(
        "Two dark glass windows of a bedroom, heavy rain streaming down them, "
        "illuminated by a single distant streetlamp outside.",
        "rain streaming down a bedroom window at night",
    )
    assert score > 0.5


def test_overlap_ignores_filler_and_plurals():
    """'window' must match 'windows', and 'dark'/'night' must count for nothing."""
    assert subject_overlap("windows in the dark room at night", "a window") == 1.0


def test_overlap_is_forgiving_when_there_is_nothing_to_compare():
    """No description means no evidence, not a flaw."""
    assert subject_overlap("a lighthouse", "") == 1.0
    assert subject_overlap("", "a lighthouse") == 1.0
