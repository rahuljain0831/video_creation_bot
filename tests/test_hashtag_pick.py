"""pick_hashtags: half of the platform cap, from the niche's bank, deterministic per seed."""
import json
from pathlib import Path

import pytest

BANKS = json.loads((Path(__file__).parent.parent / "hashtag_banks.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("bank_id", ["skillstotraineyes", "skillstotraineyes_hidden"])
@pytest.mark.parametrize("platform", ["instagram", "facebook"])
def test_bank_has_50_unique_tags_and_pick_is_half_the_cap(bank_id, platform):
    from pipeline.social_captions import pick_hashtags
    bank = BANKS[bank_id][platform]
    assert len(bank) >= 50 and len(set(bank)) == len(bank) and all(t.startswith("#") for t in bank)
    picked = pick_hashtags(bank_id, platform, 3)
    assert len(picked) == 15 and len(set(picked)) == 15 and set(picked) <= set(bank)
    assert picked == pick_hashtags(bank_id, platform, 3)

