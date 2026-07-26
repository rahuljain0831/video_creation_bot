"""Verify all API keys are valid and reachable."""
import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

PASS = "PASS"
FAIL = "FAIL"

results = []


def check(label: str, ok: bool, detail: str = "") -> None:
    icon = PASS if ok else FAIL
    msg = f"[{icon}] {label}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    results.append(ok)


# ── Telegram ──────────────────────────────────────────────────────────────────
token = os.getenv("TELEGRAM_BOT_TOKEN", "")
try:
    r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
    data = r.json()
    if data.get("ok"):
        check("Telegram bot", True, f"@{data['result']['username']}")
    else:
        check("Telegram bot", False, data.get("description", "unknown error"))
except Exception as e:
    check("Telegram bot", False, str(e))


# ── Hugging Face ──────────────────────────────────────────────────────────────
hf_token = os.getenv("HF_API_TOKEN", "")
try:
    r = requests.get(
        "https://huggingface.co/api/whoami-v2",
        headers={"Authorization": f"Bearer {hf_token}"},
        timeout=10,
    )
    if r.status_code == 200:
        check("HuggingFace token", True, r.json().get("name", "authenticated"))
    else:
        check("HuggingFace token", False, f"HTTP {r.status_code}")
except Exception as e:
    check("HuggingFace token", False, str(e))


# ── Google AI Studio (Gemini) ─────────────────────────────────────────────────
google_key = os.getenv("GOOGLE_AI_STUDIO_API_KEY", "")
try:
    r = requests.get(
        f"https://generativelanguage.googleapis.com/v1beta/models?key={google_key}",
        timeout=10,
    )
    if r.status_code == 200:
        models = r.json().get("models", [])
        check("Google AI Studio", True, f"{len(models)} models accessible")
    else:
        check("Google AI Studio", False, f"HTTP {r.status_code} — {r.json().get('error', {}).get('message', '')}")
except Exception as e:
    check("Google AI Studio", False, str(e))


# ── Kling AI ──────────────────────────────────────────────────────────────────
# Kling uses JWT bearer — just check if the endpoint responds to auth
kling_key = os.getenv("KLING_API_KEY", "")
try:
    r = requests.get(
        "https://api.klingai.com/v1/videos/text2video",
        headers={"Authorization": f"Bearer {kling_key}"},
        timeout=10,
    )
    # 401 = bad key, 200/405/422 = key accepted
    if r.status_code == 401:
        check("Kling AI", False, "Invalid API key")
    else:
        check("Kling AI", True, f"HTTP {r.status_code} (key accepted)")
except Exception as e:
    check("Kling AI", False, str(e))


# ── Ollama (local) ────────────────────────────────────────────────────────────
with open("settings.json") as f:
    settings = json.load(f)
ollama_url = settings["ollama"]["base_url"]
try:
    r = requests.get(f"{ollama_url}/api/tags", timeout=5)
    if r.status_code == 200:
        models = [m["name"] for m in r.json().get("models", [])]
        check("Ollama (local)", True, f"Models: {', '.join(models) or 'none pulled'}")
    else:
        check("Ollama (local)", False, f"HTTP {r.status_code}")
except Exception as e:
    check("Ollama (local)", False, f"Not running? {e}")


# ── Summary ───────────────────────────────────────────────────────────────────
print(f"\n{sum(results)}/{len(results)} checks passed")
