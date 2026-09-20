"""
Deterministic ball physics for eye-exercise drills.

Pure numpy, no drawing. Each ball has a stable integer id (a target is an id,
never a colour, so identity survives recolouring). The arena is a circle; all
balls stay inside it. Ball-ball collisions are equal-mass elastic with a
positional push-apart, otherwise balls stick together and tunnel at speed.
"""

from dataclasses import dataclass

import numpy as np

MIN_BALLS = 2
MAX_BALLS = 10


@dataclass
class Sim:
    pos: np.ndarray      # (n, 2) float64
    vel: np.ndarray      # (n, 2) float64
    radius: np.ndarray   # (n,)   float64
    arena_c: np.ndarray  # (2,)   arena centre
    arena_r: float

    @property
    def n(self) -> int:
        return len(self.radius)

    def step(self, dt: float) -> None:
        self.pos = self.pos + self.vel * dt
        self._walls()
        self._balls()

    def _walls(self) -> None:
        # Reflect about the wall normal, then clamp back inside. Radius-aware.
        rel = self.pos - self.arena_c
        dist = np.linalg.norm(rel, axis=1)
        limit = self.arena_r - self.radius
        out = dist > limit
        if not out.any():
            return
        nrm = rel[out] / dist[out, None]
        v = self.vel[out]
        vn = (v * nrm).sum(axis=1, keepdims=True)
        # Only reflect if moving outward, so a clamped ball is not re-flipped.
        v = np.where(vn > 0, v - 2 * vn * nrm, v)
        self.vel[out] = v
        self.pos[out] = self.arena_c + nrm * limit[out, None]

    def _balls(self) -> None:
        # A few relaxation passes: resolving one pair can push a ball into a
        # neighbour or the wall, so a single pass leaves residual overlap in
        # dense clusters. The velocity swap only fires on approach, so repeats
        # are safe.
        n = self.n
        for _ in range(4):
            moved = False
            for i in range(n):
                for j in range(i + 1, n):
                    d = self.pos[j] - self.pos[i]
                    dist = float(np.hypot(*d))
                    min_d = self.radius[i] + self.radius[j]
                    if dist >= min_d:
                        continue
                    moved = True
                    nrm = d / dist if dist > 1e-9 else np.array([1.0, 0.0])
                    push = (min_d - dist) / 2
                    self.pos[i] -= nrm * push
                    self.pos[j] += nrm * push
                    # Equal-mass elastic: swap the normal velocity components.
                    vi, vj = self.vel[i] @ nrm, self.vel[j] @ nrm
                    if vi - vj > 0:  # approaching
                        self.vel[i] += (vj - vi) * nrm
                        self.vel[j] += (vi - vj) * nrm
            if not moved:
                break
            self._walls()

    def min_gap(self, idx: int) -> float:
        """Centre distance from ball `idx` to its nearest neighbour, in radii of `idx`."""
        d = np.linalg.norm(self.pos - self.pos[idx], axis=1)
        d[idx] = np.inf
        return float(d.min() / self.radius[idx])

    def kinetic_energy(self) -> float:
        return float(0.5 * (self.vel ** 2).sum())

    def momentum(self) -> np.ndarray:
        return self.vel.sum(axis=0)


def make_sim(seed: int, n: int, arena_r: float, ball_r: float, speed: float,
             arena_c=(540.0, 800.0)) -> Sim:
    """Random non-overlapping start inside the arena. Deterministic per seed."""
    if not MIN_BALLS <= n <= MAX_BALLS:
        raise ValueError(f"ball count must be {MIN_BALLS}..{MAX_BALLS}, got {n}")
    if ball_r * 4 > arena_r:
        raise ValueError("ball radius too large for arena")
    if not 0 < speed <= arena_r * 2:
        raise ValueError("speed out of range for arena")

    rng = np.random.default_rng(seed)
    c = np.array(arena_c, dtype=np.float64)
    pos: list[np.ndarray] = []
    for _ in range(n):
        for _attempt in range(1000):
            ang = rng.uniform(0, 2 * np.pi)
            r = (arena_r - ball_r) * np.sqrt(rng.uniform())
            p = c + r * np.array([np.cos(ang), np.sin(ang)])
            if all(np.linalg.norm(p - q) >= 2.2 * ball_r for q in pos):
                pos.append(p)
                break
        else:
            raise ValueError("could not place balls without overlap; reduce n or ball_r")

    ang = rng.uniform(0, 2 * np.pi, n)
    vel = speed * np.stack([np.cos(ang), np.sin(ang)], axis=1)
    return Sim(np.array(pos), vel, np.full(n, float(ball_r)), c, float(arena_r))


def run(sim: Sim, frames: int, fps: int = 30, substeps: int = 4) -> list[np.ndarray]:
    """Advance `frames` frames, returning a positions snapshot per frame.

    Substeps keep fast balls from tunnelling through each other between frames.
    """
    out = []
    dt = 1.0 / fps / substeps
    for _ in range(frames):
        for _ in range(substeps):
            sim.step(dt)
        out.append(sim.pos.copy())
    return out
