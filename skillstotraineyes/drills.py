"""
Eye-exercise drill templates.

A drill is pure data plus one function: `drill.ops(frame) -> list of draw ops`.
No pixels here — the renderer owns drawing. Seeded parameters in, drill out, so
the same seed always yields the same video.

Draw ops (all coordinates in the 1080x1920 frame):
    ("disc",   x, y, r, color)
    ("ring",   x, y, r, color, width)
    ("dotted", x, y, r, color)              dotted circle border
    ("cross",  x, y, half, color, width)
    ("square", x, y, half, color)
    ("text",   s, x, y, size, color)        centred, wrapped by the renderer

Layout keeps clear of Instagram's UI: nothing below SAFE_BOTTOM, and the
side gutters stay empty.
"""

import json
import logging
import math
import random
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable

from skillstotraineyes.sim import make_sim, run

log = logging.getLogger(__name__)

W, H = 1080, 1920
FPS = 30
SAFE_BOTTOM = 1630          # bottom ~15% is covered by the platform UI
CX, CY = 540, 900           # arena / play-area centre
TEXT_TOP_Y = 230
TEXT_LOW_Y = 1450
BADGE_Y = 120               # above the hook; the hook block starts at TEXT_TOP_Y
BADGE_SIZE = 54
BADGE_COLOR = (255, 196, 61)

BG = (10, 13, 26)
WHITE = (245, 245, 245)
DIM = (90, 95, 110)
RED = (235, 40, 45)
ACCENTS = [(255, 196, 61), (72, 219, 251), (120, 240, 140), (255, 130, 200)]

CATALOG_PATH = Path(__file__).with_name("catalog.json")


@lru_cache(maxsize=1)
def load_catalog() -> tuple[dict, ...]:
    """Every drill the niche knows, as data. Cached: the file never changes at runtime."""
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return tuple(data["drills"])


def entry(drill_id: str) -> dict:
    for e in load_catalog():
        if e["id"] == drill_id:
            return e
    raise KeyError(f"no catalog entry {drill_id!r}; known: {sorted(e['id'] for e in load_catalog())}")


def knob_names(drill_id: str) -> list[str]:
    """The knobs this drill rolls: its declared knobs minus the ones it pins."""
    e = entry(drill_id)
    return [n for n in e["knob_names"] if n not in e["knobs"]]


def _knobs(cfg: dict, level: str, rng: random.Random) -> dict:
    """Rolled values for a catalog entry's knobs at `level`, pinned ones winning."""
    from skillstotraineyes.difficulty import roll
    names = [n for n in cfg["knob_names"] if n not in cfg["knobs"]]
    return {**roll(names, level, rng), **cfg["knobs"]}


FAMILIES = tuple(e["id"] for e in load_catalog())

DEFAULT_TEXT = {
    "cta": "Follow for more",
}


@dataclass
class Drill:
    family: str
    duration: float
    ops: Callable[[int], list]
    params: dict                                   # key params, hashed for the uniqueness gate
    chimes: list = field(default_factory=list)     # seconds at which to play the reveal chime
    voice: list = field(default_factory=list)      # [(seconds, line)] for voiceover mode
    fps: int = FPS
    level: str = ""

    @property
    def frames(self) -> int:
        return round(self.duration * self.fps)


# ── shared pieces ─────────────────────────────────────────────────────────────

def _text_ops(t: float, hook: str, hook_until: float, question: str, t_q: float,
              cta: str, t_cta: float, badge: str = "") -> list:
    if t < hook_until:
        out = [("text", hook, CX, TEXT_TOP_Y, 92, WHITE)]
        if badge:
            # Inside the hook window on purpose: a badge segment of its own would
            # push saccade past the 30s ceiling.
            out.insert(0, ("text", badge, CX, BADGE_Y, BADGE_SIZE, BADGE_COLOR))
        return out
    if t >= t_cta:
        return [("text", cta, CX, TEXT_LOW_Y, 88, WHITE)]
    if t >= t_q:
        return [("text", question, CX, TEXT_LOW_Y, 66, WHITE)]
    return []


def _text(text: dict | None, key: str, default: str) -> str:
    return (text or {}).get(key) or default


def _badge(level: str | None) -> str:
    """Badge text for a level, or "" when the drill is unlevelled."""
    from skillstotraineyes.difficulty import LEVEL_LABEL
    return LEVEL_LABEL.get(level or "", "")


def _lines(hook: str, question: str, cta: str, t_hook: float, t_q: float, t_cta: float) -> list:
    return [(0.3, hook), (t_q + 0.2, question), (t_cta + 0.1, cta)]


def pick_family(seed: int, last: list[str] | None = None) -> str:
    """Rotate through families; never the same one twice in a row."""
    pool = [f for f in FAMILIES if not last or f != last[-1]]
    return random.Random(seed).choice(pool)


def params_key(params: dict) -> str:
    """Stable string for a drill's key parameters (the uniqueness-gate identity)."""
    return json.dumps(params, sort_keys=True, separators=(",", ":"))


def too_close(params: dict, recent: list[dict]) -> bool:
    """True if `params` repeats a recent drill of the same family exactly.

    Params are already coarse (rounded speeds, discrete counts), so equality is
    the right notion of "the viewer would see the same video again".
    """
    key = params_key(params)
    return any(r.get("key") == key for r in recent)


# ── tracking (red ball) ───────────────────────────────────────────────────────

def tracking(seed: int, text: dict | None = None, level: str | None = None,
             entry_cfg: dict | None = None) -> Drill:
    from skillstotraineyes.difficulty import fits

    entry_cfg = entry_cfg or entry("tracking")

    rng = random.Random(seed)
    accent = rng.choice(ACCENTS)
    static_s, recolor_s = 2.0, 2.0

    # Re-roll rather than raise: an infeasible pack (ten balls at r=72) is a
    # legitimate draw from the knob table, and make_sim rejects it outright.
    arena_r = 440
    for _attempt in range(40):
        # The arena is re-picked each try, so a preset that pins n and ball_r can
        # still find a home rather than re-rolling identical values forever.
        arena_r = rng.choice([400, 420, 440])
        if level:
            k = _knobs(entry_cfg, level, rng)
            n, ball_r, speed, decoy, move_s = (
                k["n"], k["ball_r"], float(k["speed"]), k["decoy"], k["move_s"])
        else:
            n, ball_r = rng.randint(2, 5), rng.randint(34, 50)
            speed, decoy, move_s = rng.uniform(250, 450), "distinct", rng.choice([10, 12, 14, 16])
        if fits(n, arena_r, ball_r):
            break
    else:
        n, ball_r, speed, decoy, move_s = 4, 40, 320.0, "distinct", 12

    # The reveal must be unambiguous, but a crowded arena cannot always give the
    # target three clear radii. Scale the bar with the crowd and keep the best
    # candidate seen, because raising here would kill the whole run.
    def search(n, ball_r, speed, move_s, bumps):
        want = 3.0 if n <= 5 else 2.2
        best = None
        for bump in range(bumps):
            for attempt in range(60):
                try:
                    cand = make_sim(seed * 1000 + bump * 100000 + attempt, n, arena_r,
                                    ball_r, speed, arena_c=(CX, CY))
                except ValueError:      # fits() is only a heuristic
                    continue
                pos0 = cand.pos.copy()
                frames = run(cand, round(move_s * FPS), FPS)
                gap = cand.min_gap(0)
                if best is None or gap > best[0]:
                    best = (gap, cand, pos0, frames)
                if gap >= want:
                    return best
            if best and best[0] >= MIN_GAP:
                break
        return best

    MIN_GAP = 2.0
    best = search(n, ball_r, speed, move_s, 5)
    if best is None or best[0] < MIN_GAP:
        log.warning("tracking: no clear reveal at n=%d r=%d; using safe pack", n, ball_r)
        n, ball_r, speed, decoy, move_s = 4, 40, 320.0, "distinct", 12
        best = search(n, ball_r, speed, move_s, 5)
    gap, sim, pos0, frames = best

    target = 0                                  # id, not colour
    final = sim.pos.copy()
    t_move0 = static_s
    t_recolor = static_s + recolor_s
    t_q = static_s + move_s
    t_reveal = t_q + 2.0
    t_cta = t_reveal + 2.0
    total = t_cta + 2.5

    hook = _text(text, "hook", "Track the red ball")
    question = _text(text, "question", "Were you able to track it?")
    cta = _text(text, "cta", DEFAULT_TEXT["cta"])
    badge = _badge(level)

    def ops(f: int) -> list:
        t = f / FPS
        if t < t_move0:
            pos = pos0
        elif t < t_q:
            pos = frames[min(int((t - t_move0) * FPS), len(frames) - 1)]
        else:
            pos = final
        revealed = t >= t_reveal
        out = [("dotted", CX, CY, arena_r, accent)]
        for i in range(n):
            x, y = float(pos[i][0]), float(pos[i][1])
            if i == target:
                red = t < t_recolor or revealed
                out.append(("disc", x, y, ball_r, RED if red else WHITE))
                if revealed:
                    out.append(("ring", x, y, ball_r + 16, accent, 6))
            else:
                col = WHITE
                if revealed:
                    col = DIM
                elif t >= t_recolor:           # lures only once the target has gone white
                    if decoy == "identical" and i == 1:
                        col = RED
                    elif decoy == "similar":
                        col = (210, 110, 110)
                out.append(("disc", x, y, ball_r, col))
        return out + _text_ops(t, hook, 2.5, question, t_q, cta, t_cta, badge=badge)

    return Drill("tracking", total, ops,
                 {"family": "tracking", "level": level or "", "n": n,
                  "arena": arena_r, "ball": ball_r, "speed": round(speed, -1),
                  "decoy": decoy, "move": move_s, "gap": round(gap, 1)},
                 chimes=[t_reveal], voice=_lines(hook, question, cta, 2.5, t_q, t_cta),
                 level=level or "")


# ── smooth-pursuit paths ──────────────────────────────────────────────────────

def _pursuit(seed: int, text: dict | None, family: str, hook_default: str,
             dots: list[Callable[[float], tuple]], dur_choices: list, params: dict,
             trail: bool, level: str | None = None,
             knobs: dict | None = None) -> Drill:
    rng = random.Random(seed)
    accent = rng.choice(ACCENTS)
    body = rng.choice(dur_choices)
    hook_until = 3.0
    t_q = hook_until + body
    t_cta = t_q + 2.0
    total = t_cta + 2.5
    hook = _text(text, "hook", hook_default)
    question = _text(text, "question", "Did you keep up?")
    cta = _text(text, "cta", DEFAULT_TEXT["cta"])
    badge = _badge(level)

    def ops(f: int) -> list:
        t = f / FPS
        out = [("dotted", CX, CY, 430, DIM)]
        if t >= hook_until - 0.5 and t < t_q:
            tm = max(0.0, t - (hook_until - 0.5))
            for k, path in enumerate(dots):
                col = accent if k == 0 else WHITE
                if trail:
                    for j in range(10, 0, -1):
                        px, py = path(max(0.0, tm - j * 0.045))
                        out.append(("disc", px, py, 26 - j, tuple(int(c * (0.5 - j * 0.04)) for c in col)))
                x, y = path(tm)
                out.append(("disc", x, y, 34, col))
        elif t < hook_until - 0.5:
            x, y = dots[0](0.0)
            out.append(("disc", x, y, 34, accent))
        return out + _text_ops(t, hook, hook_until - 0.5, question, t_q, cta, t_cta, badge=badge)

    return Drill(family, total, ops, {**params, "family": family, "body": body,
                  "level": level or "", **(knobs or {})},
                 voice=_lines(hook, question, cta, hook_until, t_q, t_cta),
                 level=level or "")


def pursuit_dual(seed: int, text: dict | None = None, level: str | None = None,
                 entry_cfg: dict | None = None) -> Drill:
    entry_cfg = entry_cfg or entry("pursuit_dual")
    rng = random.Random(seed)
    a, b = rng.choice([(2, 3), (3, 4), (3, 2), (1, 2)])
    w = rng.uniform(0.55, 0.85)
    k = {}
    if level:
        k = _knobs(entry_cfg, level, rng)
        w *= k["tempo"]
    ph = rng.uniform(0, math.pi)
    A, B = 360, 360

    def p1(t): return CX + A * math.sin(a * w * t), CY + B * math.sin(b * w * t + ph)
    def p2(t): return CX + A * math.sin(a * w * t + math.pi), CY + B * math.sin(b * w * t + ph + 1.3)

    return _pursuit(seed, text, "pursuit_dual", "Follow both dots with your eyes",
                    [p1, p2], [12, 15, 18], {"a": a, "b": b, "w": round(w, 1)}, trail=False, level=level, knobs=k)


def figure8(seed: int, text: dict | None = None, level: str | None = None,
            entry_cfg: dict | None = None) -> Drill:
    entry_cfg = entry_cfg or entry("figure8")
    rng = random.Random(seed)
    w = rng.uniform(0.7, 1.1)
    k = {}
    if level:
        k = _knobs(entry_cfg, level, rng)
        w *= k["tempo"]
    A, B = 380, 300
    tilt = rng.choice([0.0, math.pi / 2])   # horizontal or vertical infinity

    def p(t):
        x = A * math.sin(w * t)
        y = B * math.sin(w * t) * math.cos(w * t) * 2
        return (CX + x, CY + y) if tilt == 0 else (CX + y, CY + x)

    return _pursuit(seed, text, "figure8", "Follow the dot in a figure eight",
                    [p], [14, 18, 22], {"w": round(w, 1), "tilt": int(tilt > 0)}, trail=True, level=level, knobs=k)


# ── saccade grid ──────────────────────────────────────────────────────────────

def saccade(seed: int, text: dict | None = None, level: str | None = None,
            entry_cfg: dict | None = None) -> Drill:
    entry_cfg = entry_cfg or entry("saccade")
    rng = random.Random(seed)
    cols, rows = rng.choice([(2, 4), (3, 4), (2, 5), (3, 5)])
    step = rng.choice([0.7, 0.85, 1.0])
    accent = rng.choice(ACCENTS)
    if level:
        k = _knobs(entry_cfg, level, rng)
        (cols, rows), step = k["cells"], k["step"]
    gx, gy = {2: 460, 3: 300}.get(cols, 220), (180 if rows > 5 else 210)
    xs = [CX + (i - (cols - 1) / 2) * gx for i in range(cols)]
    ys = [CY + (r - (rows - 1) / 2) * gy for r in range(rows)]
    cells = [(x, y) for y in ys for x in xs]

    order: list[int] = []
    for r in range(rows):
        row = list(range(r * cols, (r + 1) * cols))
        rng.shuffle(row)
        order += row
    # 20s body + 7.5s frame stays under 30s; short sweeps repeat to clear the 15s floor.
    seq_len = min(int(20 / step), max(len(order) * 2, math.ceil(8 / step)))
    lap = order + order[::-1]
    seq = (lap * (seq_len // len(lap) + 1))[:seq_len]

    hook_until = 3.0
    t_q = hook_until + seq_len * step
    t_cta = t_q + 2.0
    total = t_cta + 2.5
    hook = _text(text, "hook", "Look at each dot the moment it lights up")
    question = _text(text, "question", "How fast were your eyes?")
    cta = _text(text, "cta", DEFAULT_TEXT["cta"])
    badge = _badge(level)

    def ops(f: int) -> list:
        t = f / FPS
        lit = -1
        if hook_until <= t < t_q:
            lit = seq[int((t - hook_until) / step)]
        out = []
        for i, (x, y) in enumerate(cells):
            out.append(("disc", x, y, 44, accent if i == lit else DIM))
        return out + _text_ops(t, hook, hook_until, question, t_q, cta, t_cta, badge=badge)

    return Drill("saccade", total, ops,
                 {"family": "saccade", "level": level or "", "cols": cols, "rows": rows, "step": step, "len": seq_len},
                 voice=_lines(hook, question, cta, hook_until, t_q, t_cta),
                 level=level or "")


# ── peripheral flash ──────────────────────────────────────────────────────────

def peripheral(seed: int, text: dict | None = None, level: str | None = None,
               entry_cfg: dict | None = None) -> Drill:
    entry_cfg = entry_cfg or entry("peripheral")
    rng = random.Random(seed)
    accent = rng.choice(ACCENTS)
    n = rng.randint(8, 14)
    gap = rng.choice([1.2, 1.5, 1.8])
    flash_s = rng.choice([0.25, 0.35])
    radius = rng.choice([320, 380, 420])
    if level:
        k = _knobs(entry_cfg, level, rng)
        gap, flash_s, radius = k["gap"], k["flash"], k["radius"]
    n = min(n, int(20 / gap))                       # n*gap <= 20s keeps the video under 30s

    angs: list[float] = []
    while len(angs) < n:
        a = rng.uniform(0, 2 * math.pi)
        if not angs or abs((a - angs[-1] + math.pi) % (2 * math.pi) - math.pi) > 0.9:
            angs.append(a)

    hook_until = 3.0
    t_q = hook_until + n * gap
    t_cta = t_q + 2.0
    total = t_cta + 2.5
    hook = _text(text, "hook", "Keep your eyes on the cross")
    question = _text(text, "question", "How many flashes did you catch?")
    cta = _text(text, "cta", DEFAULT_TEXT["cta"])
    badge = _badge(level)

    def ops(f: int) -> list:
        t = f / FPS
        out = [("cross", CX, CY, 34, WHITE, 8)]
        if hook_until <= t < t_q:
            k = int((t - hook_until) / gap)
            if (t - hook_until) - k * gap < flash_s:
                a = angs[k]
                out.append(("disc", CX + radius * math.cos(a), CY + radius * math.sin(a), 30, accent))
        return out + _text_ops(t, hook, hook_until, question, t_q, cta, t_cta, badge=badge)

    return Drill("peripheral", total, ops,
                 {"family": "peripheral", "level": level or "", "n": n, "gap": gap, "flash": flash_s, "radius": radius},
                 voice=_lines(hook, question, cta, hook_until, t_q, t_cta),
                 level=level or "")


# ── scatter search ────────────────────────────────────────────────────────────

def search(seed: int, text: dict | None = None, level: str | None = None,
           entry_cfg: dict | None = None) -> Drill:
    entry_cfg = entry_cfg or entry("search")
    rng = random.Random(seed)
    accent = rng.choice(ACCENTS)
    cols, rows = rng.choice([(5, 7), (6, 8), (5, 8)])
    look_s = rng.choice([8, 10, 12])
    if level:
        k = _knobs(entry_cfg, level, rng)
        (cols, rows), look_s = k["density"], k["look_s"]
    sx, sy = min(140, 880 // cols), min(130, 900 // rows)
    xs = [CX + (i - (cols - 1) / 2) * sx for i in range(cols)]
    ys = [CY + (j - (rows - 1) / 2) * sy for j in range(rows)]
    cells = [(x, y) for y in ys for x in xs]
    odd = rng.randrange(len(cells))
    jitter = [(rng.uniform(-14, 14), rng.uniform(-14, 14)) for _ in cells]

    hook_until = 3.0
    t_q = hook_until + look_s
    t_reveal = t_q + 1.5
    t_cta = t_reveal + 2.0
    total = t_cta + 2.5
    hook = _text(text, "hook", "Find the square")
    question = _text(text, "question", "Did you spot it?")
    cta = _text(text, "cta", DEFAULT_TEXT["cta"])
    badge = _badge(level)

    def ops(f: int) -> list:
        t = f / FPS
        out = []
        if t >= hook_until - 0.5:
            for i, ((x, y), (jx, jy)) in enumerate(zip(cells, jitter)):
                if i == odd:
                    out.append(("square", x + jx, y + jy, 30, WHITE))
                else:
                    out.append(("disc", x + jx, y + jy, 32, WHITE))
            if t >= t_reveal:
                x, y = cells[odd]
                out.append(("ring", x + jitter[odd][0], y + jitter[odd][1], 58, accent, 8))
        return out + _text_ops(t, hook, hook_until - 0.5, question, t_q, cta, t_cta, badge=badge)

    return Drill("search", total, ops,
                 {"family": "search", "level": level or "", "cols": cols, "rows": rows, "look": look_s},
                 chimes=[t_reveal], voice=_lines(hook, question, cta, hook_until, t_q, t_cta),
                 level=level or "")


BUILDERS = {
    "tracking": tracking,
    "pursuit_dual": pursuit_dual,
    "figure8": figure8,
    "saccade": saccade,
    "peripheral": peripheral,
    "search": search,
}


PRESET_CHANCE = 0.25


def build(family: str, seed: int, text: dict | None = None,
          level: str | None = None) -> Drill:
    """
    Build a catalog drill. `family` is a catalog entry id, not a builder name.

    A quarter of the time an entry with presets uses one instead of rolling: a
    preset is a hand-picked knob mix worth repeating exactly (ten tiny identical
    balls, or four huge fast ones), and it carries its own level. An explicit
    `level` argument is honoured, so --level always wins.
    """
    e = entry(family)
    preset = None
    if e["presets"] and level is None and random.Random(seed ^ 0x9E37).random() < PRESET_CHANCE:
        preset = random.Random(seed).choice(e["presets"])
        e = {**e, "knobs": {**e["knobs"], **preset["knobs"]}}
        level = preset["level"]
    drill = BUILDERS[e["builder"]](seed, text, level, e)
    drill.params["drill_id"] = family
    drill.params["preset"] = preset["name"] if preset else ""
    return drill
