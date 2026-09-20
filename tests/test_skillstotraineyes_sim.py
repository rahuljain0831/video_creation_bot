"""Tests for skillstotraineyes/sim.py"""
import numpy as np
import pytest

from skillstotraineyes.sim import MAX_BALLS, Sim, make_sim, run


def _sim(seed=1, n=6, speed=400.0):
    return make_sim(seed, n=n, arena_r=420.0, ball_r=40.0, speed=speed)


def test_same_seed_same_state():
    a, b = _sim(7), _sim(7)
    ra, rb = run(a, 90), run(b, 90)
    assert all(np.array_equal(x, y) for x, y in zip(ra, rb))


def test_different_seed_differs():
    assert not np.array_equal(_sim(1).pos, _sim(2).pos)


def test_no_escape_no_overlap_over_900_steps():
    s = _sim(3, n=8, speed=600.0)
    for frame in run(s, 900):
        dist = np.linalg.norm(frame - s.arena_c, axis=1)
        assert (dist <= s.arena_r - s.radius + 1e-6).all()
        for i in range(s.n):
            for j in range(i + 1, s.n):
                # Tolerance: push-apart is per-substep, sub-pixel residue is fine.
                assert np.linalg.norm(frame[i] - frame[j]) >= s.radius[i] + s.radius[j] - 1.0


def test_head_on_collision_conserves_momentum_and_energy():
    s = Sim(
        pos=np.array([[500.0, 800.0], [560.0, 800.0]]),  # 60px apart < 80px min
        vel=np.array([[100.0, 0.0], [-100.0, 0.0]]),
        radius=np.array([40.0, 40.0]),
        arena_c=np.array([540.0, 800.0]),
        arena_r=1000.0,
    )
    p0, e0 = s.momentum().copy(), s.kinetic_energy()
    s._balls()
    assert np.allclose(s.momentum(), p0)
    assert s.kinetic_energy() == pytest.approx(e0)
    assert s.vel[0, 0] < 0 < s.vel[1, 0]  # bounced apart


def test_wall_reflects_and_keeps_speed():
    s = Sim(
        pos=np.array([[540.0 + 355.0, 800.0], [540.0, 800.0]]),
        vel=np.array([[100.0, 0.0], [0.0, 0.0]]),
        radius=np.array([40.0, 40.0]),
        arena_c=np.array([540.0, 800.0]),
        arena_r=400.0,
    )
    s.step(0.1)
    assert s.vel[0, 0] < 0
    assert abs(s.vel[0, 0]) == pytest.approx(100.0)


def test_ball_count_bounds():
    with pytest.raises(ValueError):
        make_sim(1, n=1, arena_r=420, ball_r=40, speed=300)
    with pytest.raises(ValueError):
        make_sim(1, n=MAX_BALLS + 1, arena_r=420, ball_r=40, speed=300)


def test_oversized_ball_rejected():
    with pytest.raises(ValueError):
        make_sim(1, n=3, arena_r=100, ball_r=40, speed=100)
