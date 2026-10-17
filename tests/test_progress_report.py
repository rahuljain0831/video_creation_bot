"""run_progress_report: the KPI (14d) block built from refreshed engagement_json."""
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


def _db():
    conn = sqlite3.connect(":memory:")
    conn.executescript((ROOT / "db" / "schema.sql").read_text())
    conn.row_factory = sqlite3.Row
    return conn


def _post(conn, niche, platform, post_id, views, metrics, days_ago=2):
    at = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """INSERT INTO upload_schedule (platform, niche_id, scheduled_at, status, platform_post_id,
           engagement_views, engagement_json) VALUES (?, ?, ?, 'done', ?, ?, ?)""",
        (platform, niche, at, post_id, views, json.dumps(metrics)),
    )


def test_kpi_block_groups_by_account_and_platform():
    import run_progress_report as rpt
    conn = _db()
    _post(conn, "skillstotraineyes", "instagram", "m1", 2154,
          {"views": 2154, "reach": 1800, "ig_reels_avg_watch_time": 9000, "reels_skip_rate": 40})
    _post(conn, "skillstotraineyes", "instagram", "m2", 1460,
          {"views": 1460, "reach": 1200, "ig_reels_avg_watch_time": 7000, "reels_skip_rate": 50})
    _post(conn, "space_science", "youtube", "SCqGwqs5QPw", 540,
          {"views": 540, "avg_view_duration_s": 41.5, "avg_view_percentage": 48.9})
    _post(conn, "space_science", "youtube", "old", 99999, {"views": 99999}, days_ago=30)

    kpi = {(k["account"], k["platform"]): k for k in rpt._kpi_14d(conn)}

    eye = kpi[("Skillstotraineyes", "instagram")]
    assert eye["posts"] == 2 and eye["avg_views"] == 1807 and eye["reach"] == 3000
    assert eye["watch_s"] == 8.0 and eye["skip_rate"] == 45 and eye["best"]["post_id"] == "m1"
    story = kpi[("Story niches", "youtube")]
    assert story["posts"] == 1 and story["view_pct"] == 48.9   # the 30-day-old post is out

    msg = rpt._fmt_report(rpt._gather_stats(conn))
    assert "KPI (last 14 days)" in msg
    assert "Skillstotraineyes [IG]: 2 posts | avg 1,807 views | reach 3,000 | watch 8.0s | skip 45%" in msg
    assert "https://youtu.be/SCqGwqs5QPw" in msg


def test_kpi_block_tolerates_unmigrated_db():
    import run_progress_report as rpt
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE upload_schedule (id INTEGER, platform TEXT, niche_id TEXT, status TEXT)")
    assert rpt._kpi_14d(conn) == []
