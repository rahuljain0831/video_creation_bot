"""
Invariants for the beat → shot split in the Remotion renderer.

The composition throws when a beat's shots don't sum back to the beat, so a
regression here fails the render rather than shipping a bad video — but it fails
it after a multi-minute headless-Chrome bundle. These run in milliseconds.
"""

import pytest

from pipeline.remotion_renderer import _MIN_SHOT_SEC, plan_frames, plan_shots

FPS = 30


def _plan(frames, *, beat_start=0, word_starts=None, image_a="a.jpg", image_b=None, index=0):
    return plan_shots(
        narrative_frames=frames,
        beat_start=beat_start,
        fps=FPS,
        word_starts=word_starts if word_starts is not None else [],
        image_a=image_a,
        image_b=image_b,
        index=index,
    )


@pytest.mark.parametrize("frames", [30, 45, 71, 72, 90, 135, 165, 210, 282, 400])
def test_shots_sum_to_the_beat(frames):
    """The invariant calculateMetadata asserts on the TypeScript side."""
    shots = _plan(frames)
    assert sum(s["durationInFrames"] for s in shots) == frames


@pytest.mark.parametrize("frames", [30, 45, 71, 72, 90, 135, 165, 210, 282, 400])
def test_no_shot_is_shorter_than_the_floor(frames):
    """A sub-second shot reads as a glitch, not as a cut."""
    shots = _plan(frames)
    floor = max(2, round(_MIN_SHOT_SEC * FPS))
    assert all(s["durationInFrames"] >= floor for s in shots)


def test_short_beat_stays_a_single_shot():
    assert len(_plan(45)) == 1


def test_medium_beat_cuts_once():
    assert len(_plan(120)) == 2


def test_long_beat_cuts_twice():
    assert len(_plan(270)) == 3


def test_second_image_is_used_when_present():
    shots = _plan(120, image_b="b.jpg")
    assert [s["imageSrc"] for s in shots] == ["a.jpg", "b.jpg"]


def test_same_image_cut_punches_in():
    """With one picture the cut has to come from the framing."""
    shots = _plan(120)
    assert shots[1]["move"] == "punch"


def test_cuts_land_on_word_boundaries():
    """A cut inside a spoken word is the one thing viewers consciously notice."""
    # Beat runs frames 300..420 absolute; words start every 17 frames.
    word_starts = list(range(300, 420, 17))
    shots = _plan(120, beat_start=300, word_starts=word_starts)
    cut_at = 300 + shots[0]["durationInFrames"]
    assert cut_at in word_starts


def test_cut_is_clamped_inside_the_beat_when_no_word_fits():
    """A word boundary outside the legal window must not move the cut there."""
    shots = _plan(80, beat_start=0, word_starts=[0, 79, 200])
    floor = max(2, round(_MIN_SHOT_SEC * FPS))
    assert all(s["durationInFrames"] >= floor for s in shots)
    assert sum(s["durationInFrames"] for s in shots) == 80


def test_missing_image_still_produces_shots():
    """Procedural niches have no pictures; the beat must still be planned."""
    shots = _plan(120, image_a=None)
    assert sum(s["durationInFrames"] for s in shots) == 120
    assert all(s["imageSrc"] is None for s in shots)


def test_shots_compose_with_transition_padding():
    """
    The two layers of arithmetic have to agree.

    plan_frames pads every sequence after the first by the transition length;
    plan_shots works in unpadded narrative frames. The renderer adds the lead-in
    to the first shot, and the totals must still line up.
    """
    durations = [2.5, 6.0, 4.5, 9.0]
    padded, narrative, lead_in, total, transition = plan_frames(durations, FPS, 12)

    for i, frames in enumerate(narrative):
        shots = plan_shots(
            narrative_frames=frames, beat_start=0, fps=FPS, word_starts=[],
            image_a="a.jpg", image_b=None, index=i,
        )
        shots[0]["durationInFrames"] += lead_in[i]
        assert sum(s["durationInFrames"] for s in shots) == padded[i]

    assert total == sum(narrative)
    assert transition <= 12
