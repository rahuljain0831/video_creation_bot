"""
LLM Router — auto-discover fallback chain.

Reads provider keys from llm_keys.json (top-to-bottom = priority). Every provider is
called through litellm's generic OpenAI-compatible passthrough with an explicit
api_key/api_base — no provider-prefix magic, no local processes. Ollama Cloud is just
another entry in the chain (via litellm's ollama_chat provider); there is no local-Ollama
fallback.

Usage:
    from llm_router import call_llm
    response = call_llm(prompt="Write a haiku about arteries.")

Standalone test:
    python llm_router.py
"""

import json
import logging
import sqlite3
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

log = logging.getLogger(__name__)

_ROOT = Path(__file__).parent
_KEYS_FILE = _ROOT / "llm_keys.json"


def _load_models_from_keys_file() -> list[tuple[str, str, str, str]]:
    """
    Read llm_keys.json, return [(provider, model_id, api_key, api_base), ...] in
    priority order. Every provider is called through litellm's generic OpenAI-compatible
    passthrough with an explicit api_key/api_base — no provider-prefix magic, no env-var
    injection. A provider with no api_key (and none in its "api_key" field) is skipped.
    """
    if not _KEYS_FILE.exists():
        return []

    try:
        with open(_KEYS_FILE) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.warning("llm_keys.json parse error: %s", e)
        return []

    result = []
    for provider, config in data.items():
        if provider.startswith("_"):
            continue
        if not isinstance(config, dict):
            continue
        api_key = config.get("api_key", "").strip()
        api_base = config.get("api_base", "").strip()
        if not api_key or not api_base:
            continue
        for model in config.get("models", []):
            result.append((provider, model, api_key, api_base))

    return result


def _get_model_list(cfg_router: dict | None) -> list[tuple[str, str, str, str]]:
    """Return ordered list of (provider, model_id, api_key, api_base) to try."""
    return _load_models_from_keys_file()


def _extract_text(response, model: str) -> str:
    """
    Pull the assistant text out of a completion, or raise so the caller falls
    through to the next provider.

    Reasoning models put their chain-of-thought in `reasoning_content` and leave
    `content` null when the answer budget runs out. Callers here want plain text
    (script_gen wants JSON), so a null content is a failed provider, not a
    result — without this it surfaced as "'NoneType' has no attribute 'strip'",
    which reads like a router bug rather than a model that answered wrong.
    """
    content = response.choices[0].message.content
    if not content or not content.strip():
        raise ValueError(f"{model} returned no content (reasoning-only response?)")
    return content.strip()


# ── DB connection ────────────────────────────────────────────────────────────

def _db_conn() -> sqlite3.Connection | None:
    """Return DB connection for quota tracking, or None if unavailable."""
    try:
        from config import cfg
        return sqlite3.connect(cfg.paths["db"])
    except Exception:
        return None


# ── Main entry point ─────────────────────────────────────────────────────────

def call_llm(
    prompt: str,
    system: str = "You are a helpful assistant.",
    cfg_router: dict | None = None,
    temperature: float = 0.7,
) -> tuple[str, str]:
    """
    Call LLM via auto-discovered fallback chain.

    Tries every provider in llm_keys.json in order, skipping any that are over
    their daily quota (pipeline/quota_tracker.py).

    Returns:
        (response_text, model_used)

    Raises:
        RuntimeError if all providers fail.
    """
    import litellm
    litellm.set_verbose = False

    logging.getLogger("LiteLLM").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if cfg_router is None:
        try:
            from config import cfg
            cfg_router = cfg.llm_router
        except Exception:
            cfg_router = {}

    timeout = cfg_router.get("timeout_seconds", 30) if cfg_router else 30
    models = _get_model_list(cfg_router)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]

    from pipeline.quota_tracker import check_and_log_quota
    conn = _db_conn()

    last_error = None

    # ── Try providers, one generic call path for all of them ────────────────
    for provider, model_id, api_key, api_base in models:
        if conn is not None:
            can_proceed, reason = check_and_log_quota(provider, conn, check_only=True)
            if not can_proceed:
                log.info("LLM: skipping %s/%s — %s", provider, model_id, reason)
                continue

        # Ollama Cloud isn't OpenAI-chat-completions shaped (/api/chat, not
        # /v1/chat/completions) — route it through litellm's native ollama_chat
        # provider instead of the generic openai/ passthrough everything else uses.
        model = (
            f"ollama_chat/{model_id}" if provider == "ollama_cloud"
            else f"openai/{model_id}"
        )
        log_name = f"{provider}/{model_id}"

        error_code: int | None = None
        success = False

        try:
            log.info("LLM call: model=%s", log_name)
            response = litellm.completion(
                model=model,
                messages=messages,
                temperature=temperature,
                timeout=timeout,
                api_key=api_key,
                api_base=api_base,
            )
            text = _extract_text(response, log_name)
            log.info("LLM success: model=%s chars=%d", log_name, len(text))
            success = True

            if conn is not None:
                check_and_log_quota(provider, conn, success=True)

            return text, log_name

        except Exception as e:
            log.warning("LLM provider %s failed: %s", log_name, e)
            last_error = e
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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    text, model = call_llm(
        prompt="Reply with exactly: ROUTER_OK",
        system="You are a test responder. Reply only with what is asked.",
    )
    print(f"\nModel used : {model}")
    print(f"Response   : {text}")
    assert "ROUTER_OK" in text, f"Unexpected response: {text}"
    print("\nPhase 0 verified: LLM router working.")
