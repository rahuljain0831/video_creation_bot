"""Day-gate for scheduled skillstotraineyes generation: exactly 2 days/week fire."""
from datetime import datetime, timedelta, timezone

from scripts.run_scheduled_skillstotraineyes import (
    MIDWEEK_POOL, WEEKEND_POOL, is_posting_day, picked_days,
)


def test_exactly_two_days_fire_in_a_week():
    monday = datetime(2026, 9, 28, tzinfo=timezone.utc)
    fired = [(monday + timedelta(days=i)).strftime("%A") for i in range(7)
             if is_posting_day(monday + timedelta(days=i))]
    assert len(fired) == 2
    weekend, midweek = picked_days(monday.isocalendar()[0] * 100 + monday.isocalendar()[1])
    assert weekend in WEEKEND_POOL and weekend in fired
    assert midweek in MIDWEEK_POOL and midweek in fired


def test_same_week_is_stable_across_calls():
    d = datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert picked_days(d.isocalendar()[0] * 100 + d.isocalendar()[1]) == \
           picked_days(d.isocalendar()[0] * 100 + d.isocalendar()[1])


def test_pairs_vary_across_weeks():
    seen = set()
    for wk in range(1, 20):
        d = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(weeks=wk)
        seen.add(picked_days(d.isocalendar()[0] * 100 + d.isocalendar()[1]))
    assert len(seen) >= 3
