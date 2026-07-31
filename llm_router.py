"""
LLM Router — design-v3 fallback chain.

Online priority: Groq → Cerebras → Google AI Studio
Local fallback:  Ollama (small model, no internet needed)

Quota is checked before each provider call and logged after.
Provider names in quota.json must match the prefix before "/" in model strings
(e.g. "groq/llama-3.3-70b-versatile" → provider "groq").

Usage:
    from llm_router import call_llm
    response = call_llm(prompt="Write a haiku about arteries.")

Standalone test:
    python llm_router.py
"""

import logging
import os
import sqlite3
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


def _db_conn() -> sqlite3.Connection | None:
    """Return DB connection for quota tracking, or None if config unavailable."""
    try:
        from config import cfg
        return sqlite3.connect(cfg.paths["db"])
    except Exception:
        return None


def call_llm(
    prompt: str,
    system: str = "You are a helpful assistant.",
    cfg_router: dict | None = None,
    temperature: float = 0.7,
) -> tuple[str, str]:
    """
    Call LLM via the fallback chain.

    Checks quota before each provider call and logs result after.
    Provider names are derived from the model string prefix (e.g. "groq/...").

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

    # Open one DB connection for all quota checks in this call
    from pipeline.quota_tracker import check_and_log_quota
    conn = _db_conn()

    last_error = None
    for model in models:
        if _provider_key_missing(model):
            log.debug("Skipping %s — API key not set", model)
            continue

        provider = model.split("/")[0]

        # ── Pre-call quota check ──────────────────────────────────────────────
        if conn is not None:
            can_proceed, reason = check_and_log_quota(provider, conn, check_only=True)
            if not can_proceed:
                log.info("LLM: skipping %s — %s", model, reason)
                continue

        error_code: int | None = None
        success = False

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
            success = True

            if conn is not None:
                check_and_log_quota(provider, conn, success=True)

            return text, model

        except Exception as e:
            log.warning("LLM provider %s failed: %s", model, e)
            last_error = e
            # Extract HTTP status code if available
            if hasattr(e, "status_code"):
                error_code = e.status_code
            elif hasattr(e, "response") and hasattr(e.response, "status_code"):
                error_code = e.response.status_code

        finally:
            if not success and conn is not None:
                check_and_log_quota(provider, conn, success=False, error_code=error_code)

    if conn is not None:
        conn.close()

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
