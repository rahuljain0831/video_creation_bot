"""Load settings.json and .env into a single config object."""
import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

with open(ROOT / "settings.json") as f:
    _settings = json.load(f)


class Config:
    # Telegram
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # Provider keys
    GOOGLE_AI_STUDIO_API_KEY: str = os.getenv("GOOGLE_AI_STUDIO_API_KEY", "")
    HF_API_TOKEN: str = os.getenv("HF_API_TOKEN", "")
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    CEREBRAS_API_KEY: str = os.getenv("CEREBRAS_API_KEY", "")
    PIXABAY_API_KEY: str = os.getenv("PIXABAY_API_KEY", "")

    # From settings.json — expose top-level sections
    volume         = _settings["volume"]
    video          = _settings["video"]
    tts            = _settings["tts"]
    review         = _settings["review"]
    storage        = _settings["storage"]
    ollama         = _settings["ollama"]
    paths          = _settings["paths"]
    llm_router     = _settings.get("llm_router", {})
    niches         = _settings.get("niches", [])
    image_provider = _settings.get("image_provider", {})
    script         = _settings.get("script", {})
    image_gen      = _settings.get("image_gen", {})
    background_audio = _settings.get("background_audio", {})


cfg = Config()
