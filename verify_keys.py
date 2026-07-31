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


# ── Google AI Studio (Gemini — used for Vision analysis in ingest) ────────────
google_key = os.getenv("GOOGLE_AI_STUDIO_API_KEY", "")
try:
    r = requests.get(
        f"https://generativelanguage.googleapis.com/v1beta/models?key={google_key}",
        timeout=10,
    )
    if r.status_code == 200:
        models = r.json().get("models", [])
        check("Google AI Studio (Vision/LLM)", True, f"{len(models)} models accessible")
    else:
        check("Google AI Studio (Vision/LLM)", False,
              f"HTTP {r.status_code} — {r.json().get('error', {}).get('message', '')}")
except Exception as e:
    check("Google AI Studio (Vision/LLM)", False, str(e))


# ── Groq (LLM script generation) ──────────────────────────────────────────────
groq_key = os.getenv("GROQ_API_KEY", "")
try:
    r = requests.get(
        "https://api.groq.com/openai/v1/models",
        headers={"Authorization": f"Bearer {groq_key}"},
        timeout=10,
    )
    if r.status_code == 200:
        models = r.json().get("data", [])
        check("Groq (LLM)", True, f"{len(models)} models accessible")
    else:
        check("Groq (LLM)", False, f"HTTP {r.status_code}")
except Exception as e:
    check("Groq (LLM)", False, str(e))


# ── Cerebras (LLM fallback) ───────────────────────────────────────────────────
cerebras_key = os.getenv("CEREBRAS_API_KEY", "")
try:
    r = requests.get(
        "https://api.cerebras.ai/v1/models",
        headers={"Authorization": f"Bearer {cerebras_key}"},
        timeout=10,
    )
    if r.status_code == 200:
        models = r.json().get("data", [])
        check("Cerebras (LLM)", True, f"{len(models)} models accessible")
    else:
        check("Cerebras (LLM)", False, f"HTTP {r.status_code}")
except Exception as e:
    check("Cerebras (LLM)", False, str(e))


# ── Ollama (local LLM) ────────────────────────────────────────────────────────
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
