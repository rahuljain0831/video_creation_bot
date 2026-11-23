"""
Shrink Focus drill: "How small can you see?".

A circle shrinks and fades into the background while a counter climbs 1 -> 100.
The viewer remembers the number where it vanished and comments it. There is no
right answer and nothing is revealed.

Size alone is not enough: a phone shows a 1px dot on black, so everyone would
reach 100. Fading the contrast at the same time is what spreads the answers.
The fade curve, colours and background shade are seeded so the vanishing point
moves between videos.

A score, never an age or an eye claim (user decision 2026-11-23).
"""

import random

from skillstotraineyes.drills import ACCENTS, CX, CY, FPS, H, W, WHITE, Drill, _text, _text_ops, entry

HOOK_S, SHRINK_S, HOLD_S = 2.0, 20.0, 4.0
R_START, R_END = 150.0, 1.0
CONTRAST_END = 0.04
COUNTER_Y, COUNTER_SIZE = 380, 150
BACKGROUNDS = [(10, 13, 26), (24, 24, 30), (14, 22, 34), (30, 18, 28)]
HOOK = "How small can you see?"
INTRO = "How small can you see? Remember the number where it disappears."
QUESTION = "When did it disappear for you? Comment your number!"


def _progress(t: float) -> float:
    return min(max((t - HOOK_S) / SHRINK_S, 0.0), 1.0)


def circle_at(drill: Drill, t: float) -> tuple[float, float]:
    """(radius px, contrast 0..1) at time t."""
    u = _progress(t)
    r = max(R_END, R_START * (R_END / R_START) ** u)
    contrast = 1 - (1 - CONTRAST_END) * u ** drill.params["fade"]
    return r, contrast


def shrink_focus(seed: int, text: dict | None = None, level: str | None = None,
                 entry_cfg: dict | None = None) -> Drill:
    entry_cfg = entry_cfg or entry("shrink_focus")
    rng = random.Random(seed)
    colour = rng.choice(ACCENTS + [WHITE])
    bg = rng.choice(BACKGROUNDS)
    fade = round(rng.uniform(0.6, 1.6), 2)       # <1 fades early, >1 holds contrast longer

    t_q = HOOK_S + SHRINK_S
    total = t_q + HOLD_S
    hook = _text(text, "hook", HOOK)
    question = _text(text, "question", QUESTION)
    badge = entry_cfg.get("badge", "")
    drill = Drill("shrink_focus", total, lambda f: [], {}, voice=[(0.3, INTRO), (t_q + 0.2, question)],
                  level="")
    drill.params = {"family": "shrink_focus", "level": "", "colour": list(colour),
                    "bg": list(bg), "fade": fade}

    def ops(f: int) -> list:
        t = f / FPS
        r, c = circle_at(drill, t)
        col = tuple(round(b + (a - b) * c) for a, b in zip(colour, bg))
        out = [("rect", W / 2, H / 2, W / 2, H / 2, bg), ("disc", CX, CY, max(1, round(r)), col)]
        if t >= HOOK_S:
            n = 1 + int(99 * _progress(t))
            out.append(("text", str(n), CX, COUNTER_Y, COUNTER_SIZE, WHITE))
        return out + _text_ops(t, hook, HOOK_S, question, t_q, "", total + 1, badge=badge)

    drill.ops = ops
    return drill
