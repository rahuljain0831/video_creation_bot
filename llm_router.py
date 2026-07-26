"""
LLM Router — design-v3 fallback chain.

Online priority: Groq → Cerebras → Google AI Studio
Local fallback:  Ollama (small model, no internet needed)

Usage:
    from llm_router import call_llm
    response = call_llm(prompt="Write a haiku about arteries.")

Standalone test:
    python llm_router.py
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

log = logging.getLogger(__name__)

# Provider order tried in sequence; first success wins.
_PROVIDER_ENV = {
    "groq":     "GROQ_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
    "gemini":   "GOOGLE_AI_STUDIO_API_KEY",
    "ollama":   None,   # no key needed
}


def _model_list(cfg_router: dict) -> list[str]:
    """Return ordered model list from settings, falling back to defaults."""
    defaults = [
        "groq/llama-3.3-70b-versatile",
        "cerebras/llama-3.3-70b",
        "gemini/gemini-2.0-flash",
        "ollama/llama3.1:8b",
    ]
    if not cfg_router:
        return defaults
    return [
        cfg_router.get("primary",        defaults[0]),
        cfg_router.get("fallback_1",     defaults[1]),
        cfg_router.get("fallback_2",     defaults[2]),
        cfg_router.get("local_fallback", defaults[3]),
    ]


def _provider_key_missing(model: str) -> bool:
    """Return True if required env key is absent."""
    provider = model.split("/")[0]
    env_var = _PROVIDER_ENV.get(provider)
    if env_var is None:
        return False   # ollama or unknown — attempt anyway
    return not os.getenv(env_var, "").strip()


def call_llm(
    prompt: str,
    system: str = "You are a helpful assistant.",
    cfg_router: dict | None = None,
    temperature: float = 0.7,
) -> tuple[str, str]:
    """
    Call LLM via the fallback chain.

    Returns:
        (response_text, model_used)

    Raises:
        RuntimeError if all providers fail.
    """
    import litellm
    litellm.set_verbose = False

    # Suppress litellm noise
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if cfg_router is None:
        try:
            from config import cfg
            cfg_router = cfg.llm_router
        except Exception:
            cfg_router = {}

    timeout = cfg_router.get("timeout_seconds", 30) if cfg_router else 30
    models = _model_list(cfg_router)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]

    last_error = None
    for model in models:
        if _provider_key_missing(model):
            log.debug("Skipping %s — API key not set", model)
            continue

        try:
            log.info("LLM call: model=%s", model)

            # Set provider-specific API key env vars that litellm expects
            _inject_env(model)

            response = litellm.completion(
                model=model,
                messages=messages,
                temperature=temperature,
                timeout=timeout,
            )
            text = response.choices[0].message.content.strip()
            log.info("LLM success: model=%s chars=%d", model, len(text))
            return text, model

        except Exception as e:
            log.warning("LLM provider %s failed: %s", model, e)
            last_error = e

    raise RuntimeError(f"All LLM providers failed. Last error: {last_error}")


def _inject_env(model: str) -> None:
    """Set provider-specific env vars litellm reads."""
    provider = model.split("/")[0]
    if provider == "groq":
        os.environ["GROQ_API_KEY"] = os.getenv("GROQ_API_KEY", "")
    elif provider == "cerebras":
        os.environ["CEREBRAS_API_KEY"] = os.getenv("CEREBRAS_API_KEY", "")
    elif provider == "gemini":
        # litellm reads GEMINI_API_KEY or GOOGLE_API_KEY
        key = os.getenv("GOOGLE_AI_STUDIO_API_KEY", "")
        os.environ["GEMINI_API_KEY"] = key
        os.environ["GOOGLE_API_KEY"] = key


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    text, model = call_llm(
        prompt="Reply with exactly: ROUTER_OK",
        system="You are a test responder. Reply only with what is asked.",
    )
    print(f"\nModel used : {model}")
    print(f"Response   : {text}")
    assert "ROUTER_OK" in text, f"Unexpected response: {text}"
    print("\nPhase 0 verified: LLM router working.")
