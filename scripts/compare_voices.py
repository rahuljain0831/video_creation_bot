"""
Render the same lines through every available voice, so the choice is made by
listening rather than by whatever the config happened to say.

`settings.json` has carried `en-US-GuyNeural` for scary_stories since the niche
was created, and the alternatives in `voice_pool_edge` — plus Piper and Kokoro
sitting unused behind them — have never been compared against it.

    python scripts/compare_voices.py                       # scary_stories pool
    python scripts/compare_voices.py --niche heists
    python scripts/compare_voices.py --voices en-US-GuyNeural en-GB-RyanNeural
    python scripts/compare_voices.py --prosody              # one voice, per-beat delivery

Writes .scratch_img/voices/<voice>.mp3 and prints a table. Nothing is changed in
settings.json — pick a winner and set `voice_edge` yourself.
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import cfg  # noqa: E402
from pipeline.tts import _PROSODY, _get_duration, _shift, _synthesize_edge  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("compare_voices")

OUT = Path(".scratch_img/voices")

# Three lines that stress different things: a cold open, a plain beat, and the
# turn. A voice that sounds fine reading one of these can still fall apart on
# the others.
LINES = [
    ("hook", "At 3:14 in the morning, the lock on my front door clicked open."),
    ("line", "The building manager swore that flat had been empty for years."),
    ("scare", "Then the knocking started again, and this time it was behind me."),
]


def _niche(niche_id: str) -> dict:
    for n in cfg.niches:
        if n["id"] == niche_id:
            return n
    raise SystemExit(f"No niche {niche_id!r} in settings.json")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--niche", default="scary_stories")
    ap.add_argument("--voices", nargs="*", help="override the niche's voice pool")
    ap.add_argument("--prosody", action="store_true",
                    help="render each line with its template's delivery, not a flat read")
    args = ap.parse_args()

    niche = _niche(args.niche)
    tts_cfg = {**(cfg.tts or {}), **(niche.get("tts") or {})}
    voices = args.voices or tts_cfg.get("voice_pool_edge") or [tts_cfg["voice_edge"]]

    OUT.mkdir(parents=True, exist_ok=True)
    text = " ".join(line for _t, line in LINES)

    print(f"\nniche={args.niche}  base rate={tts_cfg['rate']} pitch={tts_cfg['pitch']}"
          f"  prosody={'per-beat' if args.prosody else 'flat'}\n")
    print(f"{'voice':30} {'secs':>6}  file")
    print("-" * 72)

    for voice in voices:
        try:
            if args.prosody:
                # One file per voice, but each line read the way its beat would
                # be read in a real video.
                pieces = []
                for template, line in LINES:
                    shift = _PROSODY.get(template, {})
                    part = OUT / f"{voice}_{template}.mp3"
                    _synthesize_edge(
                        line, voice, str(part),
                        rate=_shift(tts_cfg["rate"], int(shift.get("rate", 0)), "%"),
                        volume=tts_cfg["volume"],
                        pitch=_shift(tts_cfg["pitch"], int(shift.get("pitch", 0)), "Hz"),
                    )
                    pieces.append(part)
                dur = sum(_get_duration(str(p)) for p in pieces)
                where = f"{OUT}/{voice}_*.mp3"
            else:
                path = OUT / f"{voice}.mp3"
                _synthesize_edge(
                    text, voice, str(path),
                    rate=tts_cfg["rate"], volume=tts_cfg["volume"],
                    pitch=tts_cfg["pitch"],
                )
                dur = _get_duration(str(path))
                where = str(path)

            marker = "  <- current" if voice == tts_cfg.get("voice_edge") else ""
            print(f"{voice:30} {dur:6.2f}  {where}{marker}")
        except Exception as e:
            print(f"{voice:30} {'--':>6}  failed: {str(e)[:60]}")

    print(f"\nListen, then set niches[].tts.voice_edge in settings.json.")


if __name__ == "__main__":
    main()
