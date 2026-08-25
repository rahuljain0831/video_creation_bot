"""
The horror voice treatment must not move a word's onset.

`word_timings.json` is written from Edge TTS boundaries measured *before*
`apply_voice_fx()` runs, and it drives captions, shot cuts and the riser. So the
treatment is allowed to change tone and add a tail, and is not allowed to change
duration by a single sample — anything that slips here desyncs the whole video
silently, with no error anywhere.

The level check is the other half. The first version of this chain put the room
level in `aecho`'s `out_gain` slot instead of its `decays` slot, which attenuated
the dry voice along with the echo by 18 dB. Nothing failed; the narration just
went quiet under the bed.
"""

import math
import shutil
import subprocess

import pytest

from pipeline.horror_audio import (
    VOICE_PROFILES,
    _duration,
    apply_voice_fx,
)

pytestmark = pytest.mark.slow  # every test here shells out to ffmpeg

SPEECH_LIKE = (
    # A tone burst with a gap, close enough to speech for level and length
    # assertions without shipping an audio fixture.
    "sine=f=180:d=1.2,apad=pad_dur=0.3"
)


def _peak_db(path):
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-af", "volumedetect",
         "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    for line in (proc.stderr or "").splitlines():
        if "max_volume:" in line:
            return float(line.split("max_volume:")[1].strip().split()[0])
    raise AssertionError(f"no max_volume in ffmpeg output for {path}")


@pytest.fixture
def dry(tmp_path):
    src = tmp_path / "dry.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", SPEECH_LIKE,
         "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(src)],
        capture_output=True, check=True,
    )
    return src


@pytest.mark.parametrize("profile", [p for p in VOICE_PROFILES if p != "none"])
def test_duration_is_locked(dry, tmp_path, profile):
    """Every profile returns audio of exactly the input length."""
    target = tmp_path / f"{profile}.wav"
    shutil.copy(dry, target)
    before = _duration(target)

    apply_voice_fx(target, profile=profile)

    after = _duration(target)
    assert before > 0
    # One sample at 48 kHz is 21 microseconds; the container rounds to the
    # millisecond, so this is as tight as the measurement goes.
    assert math.isclose(before, after, abs_tol=0.001), (
        f"{profile} drifted {after - before:+.6f}s — word timings are now wrong"
    )


@pytest.mark.parametrize("profile", ["line", "scare"])
def test_level_survives_the_chain(dry, tmp_path, profile):
    """
    The treatment reshapes the voice; it must not bury it.

    The bound is deliberately loose — the point is to catch a filter argument in
    the wrong slot costing double-digit dB, not to pin the mix.
    """
    target = tmp_path / f"{profile}.wav"
    shutil.copy(dry, target)
    before = _peak_db(target)

    apply_voice_fx(target, profile=profile)

    assert _peak_db(target) > before - 6.0


def test_none_profile_is_a_noop(dry, tmp_path):
    target = tmp_path / "untouched.wav"
    shutil.copy(dry, target)

    apply_voice_fx(target, profile="none")

    assert target.read_bytes() == dry.read_bytes()


def test_unknown_profile_does_not_raise(dry, tmp_path):
    """A bad template name must never cost the run its narration."""
    target = tmp_path / "unknown.wav"
    shutil.copy(dry, target)

    apply_voice_fx(target, profile="not-a-template")

    assert target.read_bytes() == dry.read_bytes()
