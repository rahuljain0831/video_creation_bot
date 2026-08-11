"""One-time setup verification for the social media scheduler.

Usage: python scripts/scheduler_setup.py
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")


def check_env_var(name: str, required: bool = True) -> bool:
    val = os.getenv(name, "")
    status = "OK" if val else ("MISSING" if required else "optional, not set")
    print(f"  {name}: {status}")
    return bool(val) or not required


def main():
    print("=" * 60)
    print("SCHEDULER SETUP VERIFICATION")
    print("=" * 60)
    errors = []

    # 1. Environment variables
    print("\n1. Environment Variables")
    env_checks = [
        ("GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON", True),
        ("CRONJOB_API_KEY", True),
        ("GITHUB_DISPATCH_TOKEN", True),
        ("TELEGRAM_BOT_TOKEN", True),
        ("TELEGRAM_CHAT_ID", True),
        ("INSTAGRAM_ACCESS_TOKEN", False),
        ("FACEBOOK_PAGE_ACCESS_TOKEN", False),
    ]
    for name, required in env_checks:
        if not check_env_var(name, required) and required:
            errors.append(f"Missing required env var: {name}")

    # 2. Google Drive credentials
    print("\n2. Google Drive Credentials")
    creds_path = os.getenv("GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON", "")
    if creds_path and Path(creds_path).exists():
        print(f"  Credentials file: OK ({creds_path})")
        try:
            from pipeline.drive_storage import _build_service
            _build_service()
            print("  Authentication: OK")
        except Exception as e:
            print(f"  Authentication: FAILED -- {e}")
            errors.append(f"Drive auth failed: {e}")
    else:
        print(f"  Credentials file: MISSING ({creds_path})")
        errors.append("Drive credentials file not found")

    # 3. cron-job.org API
    print("\n3. cron-job.org API")
    api_key = os.getenv("CRONJOB_API_KEY", "")
    if api_key:
        try:
            import requests
            resp = requests.get(
                "https://api.cron-job.org/jobs",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10,
            )
            if resp.status_code == 200:
                jobs = resp.json().get("jobs", [])
                print(f"  API: OK ({len(jobs)} existing jobs)")
            else:
                print(f"  API: FAILED (status {resp.status_code})")
                errors.append(f"cron-job.org API returned {resp.status_code}")
        except Exception as e:
            print(f"  API: FAILED -- {e}")
            errors.append(f"cron-job.org API error: {e}")
    else:
        print("  API: SKIPPED (no key)")

    # 4. Database tables
    print("\n4. Database Tables")
    settings = json.loads((ROOT / "settings.json").read_text())
    db_path = ROOT / settings["paths"]["db"]
    if db_path.exists():
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        for t in ["upload_schedule", "time_performance", "platform_rotation"]:
            status = "OK" if t in tables else "MISSING (run: python db/init_db.py)"
            print(f"  {t}: {status}")
            if t not in tables:
                errors.append(f"Table {t} missing")
        conn.close()
    else:
        print(f"  DB not found: {db_path} (run: python db/init_db.py)")
        errors.append("Database not found")

    # 5. Settings config
    print("\n5. Scheduler Config")
    scheduler_cfg = settings.get("scheduler", {})
    if scheduler_cfg.get("enabled"):
        print("  scheduler.enabled: OK")
        repo = scheduler_cfg.get("github_repo", "NOT SET")
        print(f"  github_repo: {repo}")
        if repo == "your-username/video-creation-agent":
            errors.append("Update scheduler.github_repo in settings.json with your actual repo")
            print("  WARNING: Update github_repo to your actual repo!")
    else:
        print("  scheduler: NOT CONFIGURED (add to settings.json)")
        errors.append("Scheduler not configured in settings.json")

    # Summary
    print("\n" + "=" * 60)
    if errors:
        print(f"SETUP INCOMPLETE -- {len(errors)} issue(s):")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("ALL CHECKS PASSED -- scheduler ready!")


if __name__ == "__main__":
    main()
