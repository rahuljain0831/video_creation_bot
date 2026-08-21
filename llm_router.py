"""
LLM Router — auto-discover fallback chain.

Reads provider keys from llm_keys.json (top-to-bottom = priority).
Falls back to .env keys if llm_keys.json is missing.
Ollama is always last resort — auto-started and stopped per call.

Usage:
    from llm_router import call_llm
    response = call_llm(prompt="Write a haiku about arteries.")

Standalone test:
    python llm_router.py
"""

import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

log = logging.getLogger(__name__)

_ROOT = Path(__file__).parent
_KEYS_FILE = _ROOT / "llm_keys.json"

# Fallback env vars per provider, used when llm_keys.json has no key for it.
# Several names per provider on purpose: .env in the wild uses NVIDIA_API_KEY while
# litellm wants NVIDIA_NIM_API_KEY, which silently skipped a provider that was in
# fact configured. First non-empty wins. Same contract as image_gen._PROVIDER_ENV_MAP.
_PROVIDER_ENV_MAP = {
    "groq":        ("GROQ_API_KEY",),
    "cerebras":    ("CEREBRAS_API_KEY",),
    "gemini":      ("GOOGLE_AI_STUDIO_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "xai":         ("XAI_API_KEY",),
    "perplexity":  ("PERPLEXITYAI_API_KEY", "PERPLEXITY_API_KEY"),
    "nvidia_nim":  ("NVIDIA_NIM_API_KEY", "NVIDIA_API_KEY"),
    "openrouter":  ("OPENROUTER_API_KEY",),
    "together_ai": ("TOGETHERAI_API_KEY", "TOGETHER_API_KEY"),
    "sambanova":   ("SAMBANOVA_API_KEY",),
    "ollama":      (),
}


def _key_from_env(provider: str) -> str:
    """First non-empty env var for this provider, or "" if none are set."""
    for env_var in _PROVIDER_ENV_MAP.get(provider, ()):
        key = os.getenv(env_var, "").strip()
        if key:
            return key
    return ""


def _load_models_from_keys_file() -> list[tuple[str, str]]:
    """
    Read llm_keys.json, return [(model_string, api_key), ...] in priority order.
    An empty api_key falls back to that provider's env vars (_PROVIDER_ENV_MAP)
    before the provider is skipped.
    """
    if not _KEYS_FILE.exists():
        return []

    try:
        with open(_KEYS_FILE) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.warning("llm_keys.json parse error: %s — falling back to .env", e)
        return []

    result = []
    for provider, config in data.items():
        if provider.startswith("_"):
            continue
        if not isinstance(config, dict):
            continue
        api_key = config.get("api_key", "").strip()
        if not api_key:
            api_key = _key_from_env(provider)
        if not api_key:
            continue
        models = config.get("models", [])
        for model in models:
            result.append((model, api_key))

    return result


def _load_models_from_env() -> list[tuple[str, str]]:
    """Legacy fallback: build model list from .env keys."""
    defaults = [
        ("groq/llama-3.3-70b-versatile",     "GROQ_API_KEY"),
        ("cerebras/llama-3.3-70b",            "CEREBRAS_API_KEY"),
        ("gemini/gemini-2.0-flash",           "GOOGLE_AI_STUDIO_API_KEY"),
    ]
    result = []
    for model, env_var in defaults:
        key = os.getenv(env_var, "").strip()
        if key:
            result.append((model, key))
    return result


def _get_model_list(cfg_router: dict | None) -> list[tuple[str, str]]:
    """
    Return ordered list of (model_string, api_key) to try.
    Priority: llm_keys.json > .env fallback.
    """
    models = _load_models_from_keys_file()
    if not models:
        models = _load_models_from_env()
    return models


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


def _inject_env_for_model(model: str, api_key: str) -> None:
    """Set provider-specific env vars that litellm reads."""
    provider = model.split("/")[0]

    if provider == "gemini":
        os.environ["GEMINI_API_KEY"] = api_key
        os.environ["GOOGLE_API_KEY"] = api_key
    elif provider == "groq":
        os.environ["GROQ_API_KEY"] = api_key
    elif provider == "cerebras":
        os.environ["CEREBRAS_API_KEY"] = api_key
    elif provider == "xai":
        os.environ["XAI_API_KEY"] = api_key
    elif provider == "perplexity":
        os.environ["PERPLEXITYAI_API_KEY"] = api_key
    elif provider == "nvidia_nim":
        os.environ["NVIDIA_NIM_API_KEY"] = api_key
    elif provider == "openrouter":
        os.environ["OPENROUTER_API_KEY"] = api_key
    elif provider == "together_ai":
        os.environ["TOGETHERAI_API_KEY"] = api_key
    elif provider == "sambanova":
        os.environ["SAMBANOVA_API_KEY"] = api_key


# ── Ollama lifecycle ─────────────────────────────────────────────────────────

def _get_ollama_config() -> dict:
    """Read ollama config from settings.json."""
    try:
        from config import cfg
        return getattr(cfg, "ollama", {})
    except Exception:
        return {}


def _ollama_is_running(base_url: str) -> bool:
    """Check if ollama is already serving."""
    try:
        resp = requests.get(f"{base_url}/api/tags", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


_OLLAMA_MODEL_CACHE: dict[str, str] = {}


def _param_size(details: dict) -> float:
    """'8.0B' / '2.7B' / '270M' → billions of parameters, for ranking."""
    raw = str(details.get("parameter_size", "")).strip().upper()
    match = re.match(r"([\d.]+)\s*([BM])", raw)
    if not match:
        return 0.0
    value = float(match.group(1))
    return value if match.group(2) == "B" else value / 1000.0


def _resolve_ollama_model(ollama_cfg: dict, base_url: str) -> str:
    """
    Pick the local model to use for generation.

    `model` set to anything other than "auto" is honoured verbatim — that is the
    pin escape hatch. On "auto" we ask the daemon what is installed, drop the
    models that are useless for prose (embedding, code, vision, safety), then
    rank by the `prefer` list and finally by parameter count.
    """
    pinned = str(ollama_cfg.get("model", "auto")).strip()
    fallback = ollama_cfg.get("fallback_model", "llama3.1:8b")

    if pinned and pinned.lower() != "auto":
        return pinned

    if base_url in _OLLAMA_MODEL_CACHE:
        return _OLLAMA_MODEL_CACHE[base_url]

    excludes = ollama_cfg.get(
        "exclude_patterns",
        ["embed", "coder", "code", "vision", "guard", "moderat", "rerank"],
    )
    prefer = ollama_cfg.get("prefer", ["llama3.1", "qwen2.5", "mistral", "gemma"])

    try:
        resp = requests.get(f"{base_url}/api/tags", timeout=5)
        resp.raise_for_status()
        models = resp.json().get("models", [])
    except Exception as e:
        log.warning("Ollama model auto-detect failed (%s) — using %s", e, fallback)
        return fallback

    candidates = [
        m for m in models
        if not any(pat in str(m.get("name", "")).lower() for pat in excludes)
    ]
    if not candidates:
        log.warning("No usable Ollama models installed — using %s", fallback)
        return fallback

    def rank(m: dict) -> tuple[int, float]:
        name = str(m.get("name", "")).lower()
        pref_idx = next(
            (i for i, p in enumerate(prefer) if p.lower() in name), len(prefer)
        )
        return (pref_idx, -_param_size(m.get("details", {})))

    best = str(min(candidates, key=rank)["name"])
    _OLLAMA_MODEL_CACHE[base_url] = best
    log.info(
        "Ollama auto-detect: %d installed, %d usable → %s",
        len(models), len(candidates), best,
    )
    return best


def _start_ollama(base_url: str, timeout: int = 30) -> subprocess.Popen | None:
    """
    Start 'ollama serve' as a background process.
    Wait until healthy or timeout. Returns process handle, or None if already running.
    """
    if _ollama_is_running(base_url):
        log.info("Ollama already running at %s", base_url)
        return None

    log.info("Starting ollama serve...")

    # Windows: use CREATE_NEW_PROCESS_GROUP so we can terminate cleanly
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    proc = subprocess.Popen(
        ["ollama", "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **kwargs,
    )

    # Poll until healthy
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _ollama_is_running(base_url):
            log.info("Ollama started (pid=%d)", proc.pid)
            return proc
        time.sleep(1)

    # Timeout — kill and give up
    log.warning("Ollama failed to start within %ds, killing", timeout)
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    return None


def _stop_ollama(proc: subprocess.Popen | None) -> None:
    """Terminate ollama serve process to free memory."""
    if proc is None:
        return
    log.info("Stopping ollama (pid=%d)...", proc.pid)
    proc.terminate()
    try:
        proc.wait(timeout=10)
        log.info("Ollama stopped")
    except subprocess.TimeoutExpired:
        proc.kill()
        log.warning("Ollama killed after timeout")


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

    Tries all cloud providers from llm_keys.json in order.
    If all fail and ollama.auto_start is true, starts ollama, tries local model,
    then stops ollama to free memory.

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

    # ── Try cloud providers ──────────────────────────────────────────────
    for model, api_key in models:
        provider = model.split("/")[0]

        if conn is not None:
            can_proceed, reason = check_and_log_quota(provider, conn, check_only=True)
            if not can_proceed:
                log.info("LLM: skipping %s — %s", model, reason)
                continue

        _inject_env_for_model(model, api_key)

        error_code: int | None = None
        success = False

        try:
            log.info("LLM call: model=%s", model)
            response = litellm.completion(
                model=model,
                messages=messages,
                temperature=temperature,
                timeout=timeout,
            )
            text = _extract_text(response, model)
            log.info("LLM success: model=%s chars=%d", model, len(text))
            success = True

            if conn is not None:
                check_and_log_quota(provider, conn, success=True)

            return text, model

        except Exception as e:
            log.warning("LLM provider %s failed: %s", model, e)
            last_error = e
            if hasattr(e, "status_code"):
                error_code = e.status_code
            elif hasattr(e, "response") and hasattr(e.response, "status_code"):
                error_code = e.response.status_code

        finally:
            if not success and conn is not None:
                check_and_log_quota(provider, conn, success=False, error_code=error_code)

    # ── Ollama last resort ───────────────────────────────────────────────
    ollama_cfg = _get_ollama_config()
    auto_start = ollama_cfg.get("auto_start", True)
    base_url = ollama_cfg.get("base_url", "http://localhost:11434")
    startup_timeout = ollama_cfg.get("startup_timeout_seconds", 30)

    if auto_start:
        log.info("All cloud providers failed. Trying Ollama...")
        proc = _start_ollama(base_url, timeout=startup_timeout)

        # proc=None means either already running or failed to start
        already_running = proc is None and _ollama_is_running(base_url)

        if proc is not None or already_running:
            # Resolve only once the daemon answers — auto-detect queries /api/tags.
            model_string = f"ollama/{_resolve_ollama_model(ollama_cfg, base_url)}"
            try:
                log.info("LLM call: model=%s", model_string)
                response = litellm.completion(
                    model=model_string,
                    messages=messages,
                    temperature=temperature,
                    timeout=timeout,
                    api_base=base_url,
                )
                text = _extract_text(response, model_string)
                log.info("LLM success: model=%s chars=%d", model_string, len(text))
                return text, model_string
            except Exception as e:
                log.warning("LLM provider %s failed: %s", model_string, e)
                last_error = e
            finally:
                # Only stop if WE started it
                if proc is not None:
                    _stop_ollama(proc)
        else:
            log.warning("Ollama could not be started")
    else:
        log.info("Ollama auto_start disabled, skipping local fallback")

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
