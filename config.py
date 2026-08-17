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
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    CEREBRAS_API_KEY: str = os.getenv("CEREBRAS_API_KEY", "")
    PEXELS_API_KEY: str = os.getenv("PEXELS_API_KEY", "")
    KLING_API_KEY: str = os.getenv("KLING_API_KEY", "")
    HF_API_TOKEN: str = os.getenv("HF_API_TOKEN", "")

    @staticmethod
    def get_key(name: str) -> str:
        """Fetch any provider key from .env by name, e.g. cfg.get_key('NEW_PROVIDER_API_KEY')."""
        return os.getenv(name, "")

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
    script           = _settings.get("script", {})
    image_library    = _settings.get("image_library", {})
    pexels_library   = _settings.get("pexels_library", {})
    prompt_refiner   = _settings.get("prompt_refiner", {})
    image_gen        = _settings.get("image_gen", {})
    comfyui          = _settings.get("comfyui", {})
    scheduler        = _settings.get("scheduler", {})
    SOCIAL_CONFIG_PATH: str = str(Path(__file__).parent / "social_config.json")


cfg = Config()
