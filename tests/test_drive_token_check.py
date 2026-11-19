from datetime import datetime, timedelta, timezone

from scripts.drive_token_check import needs_reauth, token_age_days


def test_dead_token_always_needs_reauth():
    assert needs_reauth(0.1, alive=False)
    assert needs_reauth(None, alive=False)


def test_reauth_starts_two_days_before_expiry():
    assert not needs_reauth(4.9, alive=True)
    assert needs_reauth(5.0, alive=True)
    assert needs_reauth(6.5, alive=True)


def test_unknown_age_relies_on_live_check():
    assert not needs_reauth(None, alive=True)


def test_token_age_days():
    issued = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    assert round(token_age_days({"_issued_at": issued})) == 3
    assert token_age_days({}) is None
