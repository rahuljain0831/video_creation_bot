"""pick_hashtags: the platform's pick count (IG 5, FB 15), from the niche's bank, deterministic per seed."""
import json
from pathlib import Path

import pytest

BANKS = json.loads((Path(__file__).parent.parent / "hashtag_banks.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("bank_id", ["skillstotraineyes", "skillstotraineyes_hidden"])
@pytest.mark.parametrize("platform", ["instagram", "facebook"])
def test_bank_has_50_unique_tags_and_pick_matches_platform_count(bank_id, platform):
    from pipeline.social_captions import pick_hashtags
    bank = BANKS[bank_id][platform]
    assert len(bank) >= 50 and len(set(bank)) == len(bank) and all(t.startswith("#") for t in bank)
    picked = pick_hashtags(bank_id, platform, 3)
    n = {"instagram": 5, "facebook": 15}[platform]
    assert len(picked) == n and len(set(picked)) == n and set(picked) <= set(bank)
    assert picked == pick_hashtags(bank_id, platform, 3)

