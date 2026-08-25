"""
Procedural horror audio beds and stingers, synthesized with ffmpeg.

Why synthesize instead of downloading: no API key, no rate limit, no licensing
question, works offline, and — the point — it is *seeded*, so every video gets a
different bed and a different set of detunings from the same six recipes. A
stock-library approach would make every video sound identical within days.

Nothing here is sample-based. Everything is sine tones, filtered noise, and
echo, which is exactly how most cinematic drone/ambience is actually built.

Entry points:
    build_ambience(duration, out_path, seed) -> (path, bed_name)
    build_sting(out_path, seed, kind) -> path
    apply_voice_fx(path, profile) -> None   (in place, duration-locked)
"""

from __future__ import annotations

import logging
import random
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

# Ambience sits well under narration. Peak-normalized to this, then attenuated
# again in the composition, so the voice always wins.
_AMBIENCE_PEAK_DB = -14.0
_STING_PEAK_DB = -4.0


class HorrorAudioError(RuntimeError):
    """Raised when ffmpeg cannot synthesize the requested audio."""


def _run(args: list[str]) -> None:
    proc = subprocess.run(args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise HorrorAudioError(
            f"ffmpeg failed (exit {proc.returncode}):\n{(proc.stderr or '')[-1500:]}"
        )


def _peak_db(path: str | Path) -> float | None:
    """Measured peak in dBFS, or None if it can't be read."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    for line in (proc.stderr or "").splitlines():
        if "max_volume:" in line:
            try:
                return float(line.split("max_volume:")[1].strip().split()[0])
            except (IndexError, ValueError):
                return None
    return None


def normalize_peak(path: str | Path, target_db: float) -> None:
    """
    Apply a flat gain so the file peaks at `target_db`.

    Deliberately a fixed gain, not loudnorm: loudnorm is dynamic and can nudge
    sample timing, and the narration track has to stay frame-aligned with
    word_timings.json. Pure gain cannot desync anything.
    """
    peak = _peak_db(path)
    if peak is None:
        log.warning("horror_audio: could not measure peak of %s — leaving as is", path)
        return

    gain = target_db - peak
    if abs(gain) < 0.5:
        return

    src = Path(path)
    tmp = src.with_suffix(".norm" + src.suffix)
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(src),
          "-af", f"volume={gain:.2f}dB", str(tmp)])
    tmp.replace(src)
    log.debug("horror_audio: %s peak %.1f → %.1f dB", src.name, peak, target_db)


# ── Ambience beds ─────────────────────────────────────────────────────────────
#
# Each builder returns (lavfi_source_args, filter_complex). The graph must end
# with a single unlabelled output. `rng` lets each render detune slightly so two
# videos on the same bed still don't sound identical.

def _bed_abyss(d: float, rng: random.Random) -> tuple[list[str], str]:
    """Bottomless sub-bass drone. The default dread bed."""
    root = rng.uniform(38, 46)
    fifth = root * rng.uniform(1.48, 1.52)
    return (
        ["-f", "lavfi", "-i", f"sine=f={root:.2f}:d={d}",
         "-f", "lavfi", "-i", f"sine=f={fifth:.2f}:d={d}",
         "-f", "lavfi", "-i", f"anoisesrc=c=brown:d={d}:a=0.3"],
        "[0]volume=0.55[a];"
        "[1]volume=0.20[b];"
        f"[2]lowpass=f={rng.randint(150, 240)},volume=0.55[c];"
        "[a][b][c]amix=inputs=3:normalize=0,"
        # tremolo rejects f < 0.1, so 0.1 is the slowest swell available here.
        f"tremolo=f={rng.uniform(0.10, 0.16):.3f}:d=0.45,"
        "aecho=0.8:0.9:1100:0.3",
    )


def _bed_windhollow(d: float, rng: random.Random) -> tuple[list[str], str]:
    """Wind through an empty structure. Sparse, cold, exterior."""
    return (
        ["-f", "lavfi", "-i", f"anoisesrc=c=brown:d={d}:a=0.7",
         "-f", "lavfi", "-i", f"sine=f={rng.uniform(50, 62):.2f}:d={d}"],
        f"[0]lowpass=f={rng.randint(300, 520)},highpass=f=60,"
        f"tremolo=f={rng.uniform(0.10, 0.15):.3f}:d=0.75,volume=0.9[a];"
        "[1]volume=0.22[b];"
        "[a][b]amix=inputs=2:normalize=0,aecho=0.8:0.88:1700:0.35",
    )


def _bed_heartbeat(d: float, rng: random.Random) -> tuple[list[str], str]:
    """Slow pulse under a low drone. Reads as your own heartbeat."""
    bpm = rng.uniform(48, 58)
    return (
        ["-f", "lavfi", "-i", f"sine=f={rng.uniform(46, 54):.2f}:d={d}",
         "-f", "lavfi", "-i", f"anoisesrc=c=brown:d={d}:a=0.25"],
        f"[0]tremolo=f={bpm / 60:.3f}:d=0.92,asubboost,volume=0.9[a];"
        "[1]lowpass=f=200,volume=0.4[b];"
        "[a][b]amix=inputs=2:normalize=0,aecho=0.7:0.8:420:0.25",
    )


def _bed_deadair(d: float, rng: random.Random) -> tuple[list[str], str]:
    """Failing fluorescent / electrical hum. Institutional, interior."""
    base = rng.uniform(98, 102)
    return (
        ["-f", "lavfi", "-i", f"sine=f={base:.2f}:d={d}",
         "-f", "lavfi", "-i", f"sine=f={base + rng.uniform(0.8, 1.8):.2f}:d={d}",
         "-f", "lavfi", "-i", f"anoisesrc=c=white:d={d}:a=0.15"],
        "[0]volume=0.4[a];"
        "[1]volume=0.4[b];"
        "[2]highpass=f=2000,volume=0.25[c];"
        "[a][b][c]amix=inputs=3:normalize=0,"
        f"tremolo=f={rng.uniform(0.3, 0.8):.3f}:d=0.18",
    )


def _bed_musicbox(d: float, rng: random.Random) -> tuple[list[str], str]:
    """Decaying bell tones in a large room. Childhood-gone-wrong register."""
    note = rng.choice([784.0, 880.0, 932.3, 1046.5])
    return (
        ["-f", "lavfi", "-i", f"sine=f={note:.1f}:d={d}",
         "-f", "lavfi", "-i", f"sine=f={note * 0.5:.1f}:d={d}",
         "-f", "lavfi", "-i", f"anoisesrc=c=brown:d={d}:a=0.4"],
        # The bells lead, the rumble is the floor under them. It used to be the
        # other way round (bells 0.16, noise 0.7), and since the peak
        # normalisation that follows is driven by whatever is loudest, the noise
        # set the level and the bells landed far below audibility under a voice
        # — a bed named "musicbox" with no music in it.
        f"[0]tremolo=f={rng.uniform(0.18, 0.3):.3f}:d=1,volume=0.50[a];"
        "[1]tremolo=f=0.21:d=1,volume=0.30[b];"
        "[2]lowpass=f=160,volume=0.35[c];"
        "[a][b][c]amix=inputs=3:normalize=0,aecho=0.8:0.9:2100:0.45",
    )


def _bed_breathroom(d: float, rng: random.Random) -> tuple[list[str], str]:
    """Something in the room, breathing slowly. The most personal bed."""
    rate = rng.uniform(0.18, 0.26)
    return (
        ["-f", "lavfi", "-i", f"anoisesrc=c=pink:d={d}:a=0.8",
         "-f", "lavfi", "-i", f"sine=f={rng.uniform(40, 50):.2f}:d={d}"],
        f"[0]highpass=f=280,lowpass=f=1100,tremolo=f={rate:.3f}:d=0.95,volume=0.75[a];"
        "[1]volume=0.45[b];"
        "[a][b]amix=inputs=2:normalize=0,aecho=0.8:0.85:900:0.3",
    )


def _bed_dirge(d: float, rng: random.Random) -> tuple[list[str], str]:
    """
    Slow minor-key pad. The only bed that is actually *music* rather than noise.

    The other beds are texture — drone, wind, hum — and under a spoken narration
    at bed level they read as room tone, not as a soundtrack. This is a held
    minor triad with a shimmer an octave and a half above it, which the ear
    hears as a chord even at -25 dBFS under a voice.

    Kept to one sustained chord on purpose: anything with a melody competes with
    the narration for attention, and the brief is music you notice only when it
    stops.
    """
    root = rng.uniform(104, 116)          # ~A2
    third = root * 1.1892                 # minor third
    fifth = root * 1.4983                 # perfect fifth
    shimmer = root * rng.choice([4.0, 6.0])
    return (
        ["-f", "lavfi", "-i", f"sine=f={root:.2f}:d={d}",
         "-f", "lavfi", "-i", f"sine=f={third:.2f}:d={d}",
         "-f", "lavfi", "-i", f"sine=f={fifth:.2f}:d={d}",
         "-f", "lavfi", "-i", f"sine=f={shimmer:.2f}:d={d}",
         "-f", "lavfi", "-i", f"anoisesrc=c=brown:d={d}:a=0.3"],
        # Each voice swells on its own slow cycle, so the chord breathes instead
        # of sitting as a static organ tone.
        f"[0]tremolo=f={rng.uniform(0.10, 0.14):.3f}:d=0.30,volume=0.55[a];"
        f"[1]tremolo=f={rng.uniform(0.10, 0.15):.3f}:d=0.40,volume=0.34[b];"
        f"[2]tremolo=f={rng.uniform(0.11, 0.16):.3f}:d=0.35,volume=0.26[c];"
        f"[3]tremolo=f={rng.uniform(0.16, 0.24):.3f}:d=0.80,volume=0.09[e];"
        "[4]lowpass=f=220,volume=0.35[n];"
        "[a][b][c][e][n]amix=inputs=5:normalize=0,"
        "lowpass=f=2600,aecho=0.8:0.9:1500:0.35",
    )


_BEDS = {
    "dirge":      _bed_dirge,
    "abyss":      _bed_abyss,
    "windhollow": _bed_windhollow,
    "heartbeat":  _bed_heartbeat,
    "deadair":    _bed_deadair,
    "musicbox":   _bed_musicbox,
    "breathroom": _bed_breathroom,
}

BED_NAMES = tuple(_BEDS)

# Beds that read as music rather than as room tone. A niche that wants a scored
# feel sets `bed_pool` to this rather than pinning one bed, so videos still
# differ from each other.
#
# `abyss` and `heartbeat` are deliberately absent: both are tonal, but they sit
# at 40-55 Hz, which a phone speaker does not reproduce. Music the audience
# cannot hear on the device they watch on is not music.
MUSICAL_BEDS = ("dirge", "musicbox")


def build_ambience(
    duration: float,
    out_path: str | Path,
    seed: int = 0,
    bed: str | None = None,
    bed_pool: "list[str] | tuple[str, ...] | None" = None,
) -> tuple[str, str]:
    """
    Synthesize a full-length ambience bed.

    `bed` forces a specific recipe; otherwise it is chosen from the seed, so a
    given video always regenerates identically but neighbouring videos differ.
    `bed_pool` narrows what that choice can land on — a niche that wants a
    scored feel passes `MUSICAL_BEDS` and still gets a different bed per video,
    which pinning `bed` would cost.

    Returns (path, bed_name).
    """
    rng = random.Random(seed)
    if bed in _BEDS:
        name = bed
    else:
        pool = [b for b in (bed_pool or BED_NAMES) if b in _BEDS] or list(BED_NAMES)
        name = rng.choice(pool)
    builder = _BEDS[name]

    # A little headroom past the video end so the echo tail is never clipped
    # mid-decay, then faded so it doesn't stop dead.
    d = max(2.0, duration + 3.0)
    sources, graph = builder(d, rng)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    fade = f",afade=t=in:st=0:d=2.5,afade=t=out:st={max(0.0, duration - 3.0):.2f}:d=3.0"
    _run(["ffmpeg", "-y", "-v", "error", *sources,
          "-filter_complex", graph + fade,
          "-t", f"{duration:.3f}", "-ac", "2", "-ar", "48000", str(out)])

    normalize_peak(out, _AMBIENCE_PEAK_DB)
    log.info("Horror ambience: bed=%s %.1fs → %s", name, duration, out.name)
    return str(out), name


# ── Stingers ──────────────────────────────────────────────────────────────────

def _sting_impact(rng: random.Random) -> tuple[list[str], str, float]:
    """Classic downward-sweep hit. For the reveal."""
    d = 1.6
    top = rng.uniform(280, 360)
    return (
        ["-f", "lavfi", "-i",
         f"aevalsrc='0.9*sin(2*PI*t*({top:.0f}-{top * 0.8:.0f}*t))':d={d}:s=48000",
         "-f", "lavfi", "-i", f"anoisesrc=c=white:d={d}:a=0.5"],
        "[0]afade=t=out:st=0.4:d=1.1[s];"
        "[1]highpass=f=800,afade=t=out:st=0:d=0.3,volume=0.55[n];"
        "[s][n]amix=inputs=2:normalize=0,asubboost",
        d,
    )


def _sting_screech(rng: random.Random) -> tuple[list[str], str, float]:
    """Strings-style upward shriek. The jump-scare hit."""
    d = 1.1
    base = rng.uniform(900, 1300)
    return (
        ["-f", "lavfi", "-i",
         f"aevalsrc='0.8*sin(2*PI*t*({base:.0f}+{base * 0.9:.0f}*t))':d={d}:s=48000",
         "-f", "lavfi", "-i", f"anoisesrc=c=white:d={d}:a=0.4"],
        "[0]vibrato=f=7:d=0.6,afade=t=out:st=0.6:d=0.5[s];"
        "[1]bandpass=f=2500:width_type=h:w=1500,afade=t=out:st=0.1:d=0.5,volume=0.5[n];"
        "[s][n]amix=inputs=2:normalize=0",
        d,
    )


def _sting_boom(rng: random.Random) -> tuple[list[str], str, float]:
    """
    Sub-bass detonation with a long tail. For the closing card.

    The sweep alone bottoms out around 20 Hz, which phone speakers reproduce as
    nothing. The added fixed sub sits in the 40-55 Hz band that small drivers
    still move air at, so the hit is felt on the device people actually watch on
    rather than only on headphones.
    """
    d = 2.2
    sub = rng.uniform(41, 54)
    return (
        ["-f", "lavfi", "-i",
         f"aevalsrc='sin(2*PI*t*(90-70*t))':d={d}:s=48000",
         "-f", "lavfi", "-i", f"anoisesrc=c=brown:d={d}:a=0.6",
         "-f", "lavfi", "-i", f"sine=f={sub:.2f}:d={d}"],
        "[0]afade=t=out:st=0.3:d=1.8,asubboost,volume=1.0[s];"
        "[1]lowpass=f=300,afade=t=out:st=0:d=0.7,volume=0.5[n];"
        "[2]afade=t=out:st=0.15:d=1.5,volume=0.8[b];"
        "[s][n][b]amix=inputs=3:normalize=0,aecho=0.8:0.9:600:0.4",
        d,
    )


def _sting_riser(rng: random.Random) -> tuple[list[str], str, float]:
    """
    Rising tension bed that ends dead, not fading.

    Placed so its last sample lands just before a scare hit. The hard stop is
    the whole point: the ear tracks the rise, the rise vanishes, and the couple
    of frames of silence that follow are what make the hit feel like a drop
    rather than another loud noise in a continuously loud video.
    """
    d = 3.0
    top = rng.uniform(1500, 2100)
    return (
        ["-f", "lavfi", "-i",
         f"aevalsrc='0.55*sin(2*PI*t*(120+{top:.0f}*t*t/{d * d:.1f}))':d={d}:s=48000",
         "-f", "lavfi", "-i", f"anoisesrc=c=white:d={d}:a=0.5",
         "-f", "lavfi", "-i", f"sine=f={rng.uniform(44, 52):.2f}:d={d}"],
        # Everything swells together, then all three are cut off flat.
        "[0]vibrato=f=5.5:d=0.35,afade=t=in:st=0:d=2.2[s];"
        f"[1]highpass=f=900,afade=t=in:st=0.4:d={d - 0.5:.1f},volume=0.5[n];"
        "[2]afade=t=in:st=0:d=2.6,asubboost,volume=0.7[b];"
        "[s][n][b]amix=inputs=3:normalize=0,"
        # 60ms of taper, just enough to avoid a click on the cut.
        f"afade=t=out:st={d - 0.06:.2f}:d=0.06",
        d,
    )


_STINGS = {
    "impact":  _sting_impact,
    "screech": _sting_screech,
    "boom":    _sting_boom,
    "riser":   _sting_riser,
}

STING_KINDS = tuple(_STINGS)

# The riser is a bed, not a hit: it runs for three seconds under the narration
# and must never compete with it. Everything else is a one-shot and gets the
# full sting level.
_STING_PEAK_OVERRIDES = {"riser": -13.0}


def sting_duration(kind: str) -> float:
    """Length in seconds of a stinger recipe, without rendering it."""
    builder = _STINGS.get(kind, _sting_impact)
    return builder(random.Random(0))[2]


def build_sting(out_path: str | Path, seed: int = 0, kind: str = "impact") -> str:
    """Synthesize one stinger. Returns its path."""
    rng = random.Random(seed)
    builder = _STINGS.get(kind, _sting_impact)
    sources, graph, _d = builder(rng)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    _run(["ffmpeg", "-y", "-v", "error", *sources,
          "-filter_complex", graph, "-ac", "2", "-ar", "48000", str(out)])

    normalize_peak(out, _STING_PEAK_OVERRIDES.get(kind, _STING_PEAK_DB))
    return str(out)


# ── Voice treatment ───────────────────────────────────────────────────────────
#
# Edge TTS neural voices are clean, close-mic'd and completely undamaged, which
# is right for a tutorial and wrong for a ghost story. Prosody knobs cannot fix
# that: rate and pitch change *how fast and how low*, never *where the voice is*.
# What makes a narrator sound frightening is the room around him and the second
# thing underneath him, and both are post-processing.
#
# Three layers, all of which survive intelligibility:
#
#   1. Body EQ. A low-mid lift and a cut at the 3 kHz presence peak takes off
#      the announcer sheen and moves the voice closer to the ear.
#   2. A shadow. A copy of the voice pitched down three semitones, low-passed
#      and mixed in far underneath. The ear does not separate it out — it hears
#      one voice that is somehow wrong. This is the whole effect; the rest is
#      support.
#   3. A room. Short dark echo so the line has walls around it instead of being
#      recorded in a booth six inches from your face.
#
# HARD CONSTRAINT: none of this may move a word's onset. `word_timings.json`
# drives captions, shot cuts and the riser, and it is written from Edge TTS
# boundaries measured *before* this runs. So: no atempo on the main path, no
# time-stretch, and the output is trimmed back to the input's exact duration —
# every filter here either adds a tail or changes tone, and the tail is cut.
#
# The shadow does use asetrate+atempo (the only pitch shift ffmpeg has without
# rubberband), but it is a separate low-level layer, so its rounding error
# smears one quiet copy by a millisecond or two rather than moving the voice.

_VOICE_PROFILES = {
    # shadow  = level of the pitched-down double
    # echo    = (delay_ms, decay) of the room — decay is aecho's LAST argument
    # body    = dB lift at 170 Hz
    "none":   {"shadow": 0.00, "echo": (0, 0.0),    "body": 0.0},
    "line":   {"shadow": 0.10, "echo": (90, 0.12),  "body": 2.0},
    "hook":   {"shadow": 0.13, "echo": (110, 0.15), "body": 2.5},
    "impact": {"shadow": 0.16, "echo": (120, 0.18), "body": 3.0},
    "reveal": {"shadow": 0.16, "echo": (130, 0.18), "body": 3.0},
    "scare":  {"shadow": 0.24, "echo": (150, 0.24), "body": 3.5},
    "end":    {"shadow": 0.18, "echo": (140, 0.20), "body": 3.0},
}

VOICE_PROFILES = tuple(_VOICE_PROFILES)

# Three semitones down. Deeper than this stops being a shadow and starts being
# a demon impression, which is funny rather than frightening.
_SHADOW_RATIO = 2 ** (-3 / 12)


def apply_voice_fx(path: str | Path, profile: str = "line") -> None:
    """
    Apply the horror voice treatment to a narration file, in place.

    The result is trimmed to the input's exact duration so word timings
    measured before the call stay valid. Unknown profiles are a no-op rather
    than an error: a bad template name must not cost the run its narration.
    """
    settings = _VOICE_PROFILES.get(profile)
    if settings is None or settings["shadow"] <= 0:
        return

    src = Path(path)
    duration = _duration(src)
    if duration <= 0:
        log.warning("voice fx: could not read duration of %s — skipped", src.name)
        return

    delay_ms, decay = settings["echo"]
    graph = (
        "[0:a]asplit=2[main][sh];"
        # Rumble out, chest in, announcer-presence peak down.
        "[main]highpass=f=85,"
        f"equalizer=f=170:t=q:w=1.1:g={settings['body']:.1f},"
        "equalizer=f=3000:t=q:w=1.6:g=-2.0,"
        "acompressor=threshold=-18dB:ratio=3:attack=8:release=180:makeup=2[m];"
        # The shadow. Low-passed so it reads as weight under the voice rather
        # than as a second intelligible speaker competing with the first.
        f"[sh]asetrate=48000*{_SHADOW_RATIO:.6f},aresample=48000,"
        f"atempo={1 / _SHADOW_RATIO:.6f},lowpass=f=1800,"
        f"volume={settings['shadow']:.3f}[s];"
        "[m][s]amix=inputs=2:normalize=0,"
        # aecho is in_gain:out_gain:delays:decays — the room level is the LAST
        # value. Putting it in the out_gain slot attenuates the dry voice along
        # with the echo, which cost 18 dB the first time this was written.
        f"aecho=0.9:0.88:{delay_ms}:{decay:.2f},"
        # The layers stack, so the sum can exceed 0 dBFS on a loud syllable.
        "alimiter=limit=0.95"
    )

    tmp = src.with_suffix(".fx" + src.suffix)
    try:
        _run(["ffmpeg", "-y", "-v", "error", "-i", str(src),
              "-filter_complex", graph,
              # The trim is what keeps word_timings.json honest.
              "-t", f"{duration:.6f}",
              "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(tmp)])
    except HorrorAudioError as e:
        log.warning("voice fx (%s) failed, using the dry take: %s", profile, e)
        tmp.unlink(missing_ok=True)
        return

    tmp.replace(src)


def _duration(path: str | Path) -> float:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    try:
        return float((proc.stdout or "").strip())
    except ValueError:
        return 0.0
