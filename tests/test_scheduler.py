"""Tests for pipeline/scheduler.py — mock all external calls."""
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_db():
    """Return an in-memory DB initialised from schema.sql."""
    schema = (Path(__file__).parent.parent / "db" / "schema.sql").read_text()
    conn = sqlite3.connect(":memory:")
    conn.executescript(schema)
    return conn


_MINIMAL_SCHEDULER_CFG = {
    "enabled": True,
    "default_times_ist": {
        "youtube":   ["17:00", "20:00", "12:00"],
        "instagram": ["19:00", "21:00", "11:00"],
        "facebook":  ["13:00", "18:00", "21:00"],
    },
    "min_gap_minutes": 30,
    "exploration_rate": 0.0,   # disabled by default so tests are deterministic
    "trusted_sample_min": 3,
    "github_repo": "test-owner/test-repo",
}


def _patch_cfg(monkeypatch, cfg_override=None):
    """Patch _get_scheduler_config to return a controllable dict."""
    cfg = dict(_MINIMAL_SCHEDULER_CFG)
    if cfg_override:
        cfg.update(cfg_override)
    from pipeline import scheduler as sch
    monkeypatch.setattr(sch, "_get_scheduler_config", lambda: cfg)
    return cfg


# ---------------------------------------------------------------------------
# 1. get_next_platform — first call
# ---------------------------------------------------------------------------

def test_get_next_platform_first_call():
    """With no prior rotation data, should return 'youtube'."""
    from pipeline.scheduler import get_next_platform

    conn = make_db()
    result = get_next_platform("mythology", conn)
    assert result == "youtube"


# ---------------------------------------------------------------------------
# 2. get_next_platform — rotation cycle
# ---------------------------------------------------------------------------

def test_get_next_platform_rotates():
    """Should rotate youtube -> instagram -> facebook -> youtube."""
    from pipeline.scheduler import get_next_platform

    conn = make_db()
    niche = "mythology"
    results = [get_next_platform(niche, conn) for _ in range(4)]
    assert results == ["youtube", "instagram", "facebook", "youtube"]


# ---------------------------------------------------------------------------
# 3. pick_optimal_time — uses defaults when no perf data
# ---------------------------------------------------------------------------

def test_pick_optimal_time_uses_defaults(monkeypatch):
    """With no time_performance rows and exploration disabled, should pick from default IST hours."""
    from pipeline import scheduler as sch
    from pipeline.scheduler import _ist_to_utc_hour

    _patch_cfg(monkeypatch, {"exploration_rate": 0.0})
    conn = make_db()

    result = sch.pick_optimal_time("mythology", "youtube", conn)

    # result should be a timezone-aware datetime in the future
    assert isinstance(result, datetime)
    assert result.tzinfo is not None, "result must be timezone-aware"
    now = datetime.now(timezone.utc)
    assert result > now

    # Hour should be one of the default youtube IST slots converted to UTC
    default_ist = _MINIMAL_SCHEDULER_CFG["default_times_ist"]["youtube"]
    expected_utc_hours = [_ist_to_utc_hour(t) for t in default_ist]
    assert result.hour in expected_utc_hours


# ---------------------------------------------------------------------------
# 4. pick_optimal_time — avoids conflicts (min_gap)
# ---------------------------------------------------------------------------

def test_pick_optimal_time_avoids_conflicts(monkeypatch):
    """Should skip a slot already occupied within min_gap_minutes."""
    from pipeline import scheduler as sch
    from pipeline.scheduler import _ist_to_utc_hour

    _patch_cfg(monkeypatch, {"exploration_rate": 0.0, "min_gap_minutes": 30})
    conn = make_db()

    # Pre-occupy the first default youtube UTC hours for tomorrow
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    default_ist = _MINIMAL_SCHEDULER_CFG["default_times_ist"]["youtube"]
    utc_hours = [_ist_to_utc_hour(t) for t in default_ist]

    # Block the first default slot
    blocked_dt = tomorrow.replace(hour=utc_hours[0], minute=0, second=0, microsecond=0)
    conn.execute(
        "INSERT INTO upload_schedule (platform, niche_id, scheduled_at, status) "
        "VALUES ('youtube', 'mythology', ?, 'pending')",
        (blocked_dt.strftime("%Y-%m-%d %H:%M:%S"),),
    )
    conn.commit()

    result = sch.pick_optimal_time("mythology", "youtube", conn)

    # Result must be timezone-aware and not be the blocked hour (or within 30 min of it)
    assert result.tzinfo is not None, "result must be timezone-aware"
    diff = abs((result - blocked_dt).total_seconds() / 60)
    assert diff >= 30, f"Got slot {result} too close to blocked {blocked_dt}"

