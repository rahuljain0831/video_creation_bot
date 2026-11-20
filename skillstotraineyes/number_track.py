"""
Number Track drill: follow the red ball, then name its number.

One ball starts red, turns white like the rest, and they all bounce. When they
stop every ball shows a number 1..8 and the viewer is asked which one it was.
The answer is never shown on video: the comments are the point. It lives in
`params["answer"]` only.

Expert adds three short colour flashes during the white phase to throw the eye.
Flash colours come from ACCENTS, never RED, so a flash cannot point at the target.
"""

import random

import numpy as np

from skillstotraineyes.drills import (
    ACCENTS, BG, CX, CY, FPS, RED, WHITE, Drill, _badge, _text, _text_ops, entry,
)
from skillstotraineyes.sim import make_sim, run

# level -> (n, ball_r, speed, white bounce seconds). Same feel as tracking's locked table.
NUMBER_LOCKED = {
    "medium": (8, 44, 420.0, 11.5),
    "hard":   (8, 32, 520.0, 15.0),
    "expert": (8, 26, 640.0, 18.5),
}
_LEVEL_MAP = {"easy": "medium", "god": "expert"}
SPEED_BOOST = 1.8
# HOLD_S must outlast the spoken question (~3.05s, starting 0.2s after the stop),
# or the voice is cut mid-line: 2.5s dropped "Comment below".
STILL_S, RED_MOVE_S, HOLD_S = 2.0, 3.0, 4.0
FLASHES, FLASH_S = 3, 0.4
MIN_GAP = 2.2                      # target clear of every other ball by this many radii
QUESTION = "Which number was it? Comment below!"   # no emoji: the video font has none


def number_track(seed: int, text: dict | None = None, level: str | None = None,
                 entry_cfg: dict | None = None) -> Drill:
    entry_cfg = entry_cfg or entry("number_track")
    lvl = _LEVEL_MAP.get(level or "medium", level or "medium")
    n, ball_r, speed, white_s = NUMBER_LOCKED[lvl]
    move_s = RED_MOVE_S + white_s
    arena_r = min(480, round(340 + 2.5 * ball_r))
    rng = random.Random(seed)
    accent = rng.choice(ACCENTS)

    # Keep the candidate whose target ends clearest; good enough is good enough.
    best = None
    for attempt in range(60):
        try:
            sim = make_sim(seed * 1000 + attempt, n, arena_r, ball_r, speed, arena_c=(CX, CY))
        except ValueError:
            continue
        sim.vel *= SPEED_BOOST * np.random.default_rng(seed + attempt).uniform(0.6, 1.4, n)[:, None]
        pos0 = sim.pos.copy()
        frames = run(sim, round(move_s * FPS), FPS)
        gap = sim.min_gap(0)
        if best is None or gap > best[0]:
            best = (gap, pos0, frames)
        if gap >= MIN_GAP:
            break
    gap, pos0, frames = best
    final = frames[-1]

    target = 0
    labels = list(range(1, n + 1))
    rng.shuffle(labels)
    answer = labels[target]

    t_move0 = STILL_S
    t_white = STILL_S + RED_MOVE_S
    t_stop = STILL_S + move_s
    total = t_stop + HOLD_S

    flashes = FLASHES if lvl == "expert" else 0
    windows = [(t_white + white_s * (k + 1) / (flashes + 1),) for k in range(flashes)]
    windows = [(s - FLASH_S / 2, s + FLASH_S / 2) for (s,) in windows]
    flash_cols = [[rng.choice(ACCENTS) for _ in range(n)] for _ in windows]

    hook = _text(text, "hook", "Follow the red ball")
    question = _text(text, "question", QUESTION)
    badge = _badge(level)           # like tracking: the asked level's label, mapped physics
    intro = "Follow the red ball. Which number will it land on?"
    label_size = round(ball_r * 1.3)

    def ops(f: int) -> list:
        t = f / FPS
        if t < t_move0:
            pos = pos0
        elif t < t_stop:
            pos = frames[min(int((t - t_move0) * FPS), len(frames) - 1)]
        else:
            pos = final
        flash = next((k for k, (a, b) in enumerate(windows) if a <= t < b), None)
        out = [("dotted", CX, CY, arena_r, accent)]
        for i in range(n):
            x, y = float(pos[i][0]), float(pos[i][1])
            if i == target and t < t_white:
                col = RED
            elif flash is not None:
                col = flash_cols[flash][i]
            else:
                col = WHITE
            out.append(("disc", x, y, ball_r, col))
            if t >= t_stop:
                out.append(("label", str(labels[i]), x, y, label_size, BG))
        return out + _text_ops(t, hook, 2.5, question, t_stop, "", total + 1, badge=badge)

    return Drill("number_track", total, ops,
                 {"family": "number_track", "level": level or "", "n": n, "arena": arena_r,
                  "ball": ball_r, "speed": round(speed, -1), "move": white_s,
                  "gap": round(float(gap), 1), "answer": answer, "flashes": flashes},
                 voice=[(0.3, intro), (t_stop + 0.2, question)], level=level or "")
