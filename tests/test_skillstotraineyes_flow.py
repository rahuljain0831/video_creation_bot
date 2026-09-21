"""Tests for wording validation, uniqueness gate, account routing and publish()."""
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from skillstotraineyes.drills import build, params_key, too_close
from skillstotraineyes.wording import generate_wording, has_claim


# ── wording: LLM output is untrusted ──────────────────────────────────────────

def _llm(monkeypatch, payload):
    from skillstotraineyes import wording
    monkeypatch.setattr(wording, "call_llm",
                        lambda *a, **k: (payload if isinstance(payload, str) else json.dumps(payload), "m"))


DEFAULTS = {"hook": "Track the red ball", "question": "Were you able to track it?"}


def test_wording_accepts_clean_output(monkeypatch):
    _llm(monkeypatch, {"hook": "Spot the red one", "question": "Did you keep up?", "caption": "Fun one!"})
    assert generate_wording("tracking", DEFAULTS, []) == {
        "hook": "Spot the red one", "question": "Did you keep up?", "caption": "Fun one!"}


@pytest.mark.parametrize("bad", ["Improve your eyesight", "This will cure myopia", "Doctor approved",
                                 "x" * 300])
def test_wording_rejects_claims_and_overlong(monkeypatch, bad):
    _llm(monkeypatch, {"hook": bad, "question": "Ready?", "caption": bad})
    out = generate_wording("tracking", DEFAULTS, [])
    assert "hook" not in out and "caption" not in out and out["question"] == "Ready?"


def test_wording_rejects_recent_repeat(monkeypatch):
    _llm(monkeypatch, {"hook": "Follow the dot", "question": "Ready?", "caption": "Hi"})
    assert "hook" not in generate_wording("figure8", DEFAULTS, [{"hook": "follow the dot"}])


def test_wording_survives_llm_failure(monkeypatch):
    from skillstotraineyes import wording
    def boom(*a, **k): raise RuntimeError("offline")
    monkeypatch.setattr(wording, "call_llm", boom)
    assert generate_wording("tracking", DEFAULTS, []) == {}
    _llm(monkeypatch, "not json at all")
    assert generate_wording("tracking", DEFAULTS, []) == {}


def test_has_claim():
    assert has_claim("improve your vision") and not has_claim("A fun eye game")


# ── uniqueness gate ───────────────────────────────────────────────────────────

def test_too_close_matches_identical_params_only():
    d = build("tracking", 1)
    assert too_close(d.params, [{"key": params_key(d.params)}])
    assert not too_close(d.params, [{"key": "other"}])
    assert not too_close(d.params, [])


def test_gate_reseeds_away_from_recent():
    import run_skillstotraineyes as r
    first = build("tracking", 5)
    recent = [{"family": "tracking", "key": params_key(first.params)}]
    drill, seed = r._build_unique("tracking", 5, None, recent)
    assert params_key(drill.params) != params_key(first.params) and seed != 5


# ── account routing: exclusive accounts stay isolated ─────────────────────────

CFG = {
    "platforms": {"instagram": {"enabled": True}, "youtube": {"enabled": True}},
    "accounts": [
        {"account_id": "all_ig", "platform": "instagram", "niche": "all", "enabled": True},
        {"account_id": "eye_ig", "platform": "instagram", "niche": "skillstotraineyes",
         "enabled": True, "exclusive": True},
        {"account_id": "myth_yt", "platform": "youtube", "niche": "mythology", "enabled": True},
    ],
}


def _ids(platform, niche):
    from scripts.upload_all_platforms import _get_accounts_for_platform
    return [a["account_id"] for a in _get_accounts_for_platform(platform, CFG, niche)]


def test_eye_niche_posts_only_to_its_own_account():
    assert _ids("instagram", "skillstotraineyes") == ["eye_ig"]


def test_story_niches_never_see_the_eye_account():
    assert _ids("instagram", "scary_stories") == ["all_ig"]
    assert _ids("instagram", None) == ["all_ig"]          # manual CLI, no niche


def test_legacy_youtube_behaviour_unchanged():
    # Existing behaviour: every video goes to every non-exclusive account.
    assert _ids("youtube", "scary_stories") == ["myth_yt"]


def test_eye_niche_never_falls_back_to_legacy_accounts_on_other_platforms():
    # schedule_all_platforms.py schedules every 'assembled' video on all platforms.
    # An eye video landing on youtube must find no account, not mythology_yt.
    assert _ids("youtube", "skillstotraineyes") == []


def test_real_social_config_wires_the_eye_account():
    cfg = json.loads((Path(__file__).parent.parent / "social_config.json").read_text())
    from scripts.upload_all_platforms import _get_accounts_for_platform
    eye = _get_accounts_for_platform("instagram", cfg, "skillstotraineyes")
    assert [a["account_id"] for a in eye] == ["skillstotraineyes_ig"]
    other = _get_accounts_for_platform("instagram", cfg, "scary_stories")
    assert "skillstotraineyes_ig" not in [a["account_id"] for a in other]


# ── publish(): instagram only, caption ships in the manifest ──────────────────

def test_publish_schedules_only_requested_platforms(monkeypatch, tmp_path):
    schema = (Path(__file__).parent.parent / "db" / "schema.sql").read_text()
    conn = sqlite3.connect(":memory:")
    conn.executescript(schema)
    conn.execute("INSERT INTO videos (status, niche_id) VALUES ('assembled','skillstotraineyes')")
    conn.commit()

    import pipeline.drive_storage as ds
    import pipeline.scheduler as sch
    monkeypatch.setattr(ds, "upload_to_drive", lambda *a, **k: "drive123")
    monkeypatch.setattr(sch, "next_queue_slot", lambda *a, **k: sch.datetime.now(sch.timezone.utc) + sch.timedelta(days=1))
    seen = []
    monkeypatch.setattr(sch, "schedule_video", lambda *a, **k: seen.append(k) or {})

    from pipeline.publisher import publish
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    cfg = SimpleNamespace(paths={"scripts": str(tmp_path)})
    publish(1, str(video), {"id": "skillstotraineyes"}, conn, cfg,
            platforms=["instagram"], title="T", caption="C + disclaimer", hashtags=["#a"])

    assert [k["force_platform"] for k in seen] == ["instagram"]
    assert seen[0]["caption"] == "C + disclaimer" and seen[0]["title"] == "T"
    assert conn.execute("SELECT status FROM videos WHERE id=1").fetchone()[0] == "approved"


def test_schedule_video_writes_caption_into_manifest(monkeypatch):
    schema = (Path(__file__).parent.parent / "db" / "schema.sql").read_text()
    conn = sqlite3.connect(":memory:")
    conn.executescript(schema)
    conn.execute("INSERT INTO videos (status, niche_id) VALUES ('approved','skillstotraineyes')")
    conn.commit()

    written = {}
    import pipeline.drive_storage as ds
    def fake_upload(path, folder_name=None):
        written["manifest"] = json.loads(Path(path).read_text())
        return "id"
    monkeypatch.setattr(ds, "upload_to_drive", fake_upload)

    import pipeline.scheduler as sch
    from datetime import datetime, timedelta, timezone
    sch.schedule_video(1, "skillstotraineyes", "fid", "", conn, force_platform="instagram",
                       force_time=datetime.now(timezone.utc) + timedelta(days=1),
                       title="Hook", caption="Body\n\nNot medical advice.", hashtags=["#eyes"])
    m = written["manifest"]
    assert m["title"] == "Hook" and "Not medical advice." in m["caption"] and m["hashtags"] == ["#eyes"]
    assert m["platform"] == "instagram" and m["niche_id"] == "skillstotraineyes"


# -- drill pick / levels ------------------------------------------------------

def test_pick_drill_falls_back_to_the_catalog_on_a_bad_llm_answer(monkeypatch):
    """Untrusted output: an unknown id must not reach build()."""
    from skillstotraineyes import wording
    from skillstotraineyes.drills import FAMILIES
    monkeypatch.setattr(wording, "call_llm", lambda *a, **k: ("drop table drills", "fake"))
    assert wording.pick_drill(3, [], None) in FAMILIES
    monkeypatch.setattr(wording, "call_llm", lambda *a, **k: ('{"drill_id": "nope"}', "fake"))
    assert wording.pick_drill(3, [], None) in FAMILIES


def test_pick_drill_accepts_a_real_catalog_id(monkeypatch):
    from skillstotraineyes import wording
    monkeypatch.setattr(wording, "call_llm",
                        lambda *a, **k: ('{"drill_id": "zigzag_pursuit"}', "fake"))
    assert wording.pick_drill(3, [], None) == "zigzag_pursuit"


def test_pick_drill_never_repeats_the_previous_drill(monkeypatch):
    from skillstotraineyes import wording
    monkeypatch.setattr(wording, "call_llm",
                        lambda *a, **k: ('{"drill_id": "zigzag_pursuit"}', "fake"))
    assert wording.pick_drill(3, [{"drill_id": "zigzag_pursuit"}], None) != "zigzag_pursuit"


def test_pick_drill_survives_a_dead_llm(monkeypatch):
    from skillstotraineyes import wording
    from skillstotraineyes.drills import FAMILIES

    def boom(*a, **k):
        raise RuntimeError("no providers")
    monkeypatch.setattr(wording, "call_llm", boom)
    assert wording.pick_drill(3, [], None) in FAMILIES


def test_level_is_recorded_in_variation_params():
    """The uniqueness gate keys on (family, level, knob mix)."""
    a = build("tracking", 5, level="easy")
    b = build("tracking", 5, level="god")
    assert params_key(a.params) != params_key(b.params)


def test_niche_config_has_the_new_keys():
    cfgd = json.loads(Path("settings.json").read_text(encoding="utf-8"))
    niche = next(n for n in cfgd["niches"] if n["id"] == "skillstotraineyes")
    assert niche["human_policy"] == "none", "a sniper is a human; 'never' would strip it"
    assert niche["levels"] is True


# -- presets reachable from the entry point ------------------------------------

def _preset_seeds(fid="tracking", level="easy", allow=True):
    return [(s, build(fid, s, level=level, allow_preset=allow)) for s in range(80)]


def test_allow_preset_fires_and_overrides_level():
    from skillstotraineyes.drills import entry
    fired = [d for _, d in _preset_seeds() if d.params["preset"]]
    assert fired, "no preset in 80 seeds"
    for d in fired:
        p = next(p for p in entry("tracking")["presets"] if p["name"] == d.params["preset"])
        assert d.level == p["level"]


def test_no_allow_preset_never_fires():
    assert not any(d.params["preset"] for _, d in _preset_seeds(allow=False))


def test_allow_preset_is_a_noop_without_presets():
    from skillstotraineyes.drills import load_catalog
    fid = next(e["id"] for e in load_catalog() if not e["presets"])
    assert all(d.level == "easy" and not d.params["preset"] for _, d in _preset_seeds(fid))


def test_build_unique_respects_allow_preset():
    import run_skillstotraineyes as r
    seed = next(s for s, d in _preset_seeds() if d.params["preset"] and d.level != "easy")
    d, _ = r._build_unique("tracking", seed, None, [], "easy", True)
    assert d.params["preset"] and d.level != "easy"
    d2, _ = r._build_unique("tracking", seed, None, [], "easy", False)
    assert d2.level == "easy" and not d2.params["preset"]


def test_pick_drill_reads_old_rows_with_family_only(monkeypatch):
    from skillstotraineyes import wording
    monkeypatch.setattr(wording, "call_llm",
                        lambda *a, **k: ('{"drill_id": "tracking"}', "fake"))
    assert wording.pick_drill(3, [{"family": "tracking"}], None) != "tracking"


def test_variation_records_level_and_drill_id():
    import run_skillstotraineyes as r
    d = build("tracking", 5, level="hard")
    v = r._variation(5, "tracking", d, "h", "q", "c", "muted")
    assert v["level"] == "hard" and v["drill_id"] == "tracking" and v["key"] == params_key(d.params)


def test_importing_wording_does_not_load_llm_router():
    """llm_router pulls in config/.env; importing wording must not do that at import time."""
    import subprocess
    import sys
    code = ("import sys, skillstotraineyes.wording; "
            "raise SystemExit(1 if 'llm_router' in sys.modules else 0)")
    root = str(Path(__file__).resolve().parent.parent)
    assert subprocess.run([sys.executable, "-c", code], cwd=root).returncode == 0
