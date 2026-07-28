# ==============================================================================
# FILE: mozaiksai/core/adapters/llm_fallback.py
# DESCRIPTION: LLM provider fallback config builder for AG2-based workflows.
#
# Purpose: build an AG2-compatible ``config_list`` that tries a primary model
# and a prioritized list of fallback models when the primary is unavailable.
# AG2 natively iterates config_list entries on failure, so the Mozaiks adapter
# only needs to construct the ordered list correctly.
#
# Also provides ``llm_config_to_ag2_config`` which converts a config_list dict
# to the typed AG2 ModelConfig subclass for the selected provider.
#
# Integration points:
#   ag2_runner.build_llm_config()  — accepts fallback_models kwarg
#   ag2_network_runner             — callers pass llm_config built here
#
# Env vars (all optional; defaults keep existing behavior):
#   LLM_PRIMARY_API_TYPE         provider type: openai | google | anthropic | ollama (openai)
#   LLM_PRIMARY_MODEL            model id for primary provider (gpt-4o)
#   LLM_PRIMARY_API_KEY          api key for primary provider (falls back to provider-specific var)
#   LLM_PRIMARY_BASE_URL         optional base URL override (Azure, Ollama host, etc.)
#   LLM_FALLBACK_MODELS          comma-separated model ids, in priority order
#   LLM_FALLBACK_API_KEYS        comma-separated api keys, parallel to models
#   LLM_FALLBACK_BASE_URLS       comma-separated base URLs, parallel to models
#   LLM_TEMPERATURE              shared temperature (0.0)
#   LLM_TIMEOUT_SECONDS          shared request timeout (120)
#   LLM_FALLBACK_ENABLED         set to "false" to disable fallback (true)
#
# Provider-specific API key env vars (resolved when LLM_PRIMARY_API_KEY is unset):
#   GEMINI_API_KEY / GOOGLE_API_KEY   — used when LLM_PRIMARY_API_TYPE=google
#   ANTHROPIC_API_KEY                 — used when LLM_PRIMARY_API_TYPE=anthropic
#   OPENAI_API_KEY                    — used when LLM_PRIMARY_API_TYPE=openai (default)
# ==============================================================================
from __future__ import annotations

import os
from typing import Any

from logs.logging_config import get_core_logger

logger = get_core_logger("llm_fallback")

# ---------------------------------------------------------------------------
# Config reader helpers
# ---------------------------------------------------------------------------

def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _env_list(key: str) -> list[str]:
    raw = _env(key)
    return [v.strip() for v in raw.split(",") if v.strip()] if raw else []


# ---------------------------------------------------------------------------
# Fallback config builder
# ---------------------------------------------------------------------------

def _resolve_api_key(api_type: str, explicit_key: str | None = None) -> str:
    """Resolve API key for ``api_type``, preferring an explicit value then
    provider-specific env vars then the generic ``LLM_PRIMARY_API_KEY``."""
    if explicit_key:
        return explicit_key
    if api_type == "google":
        return _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY") or _env("LLM_PRIMARY_API_KEY")
    if api_type == "anthropic":
        return _env("ANTHROPIC_API_KEY") or _env("LLM_PRIMARY_API_KEY")
    # openai / azure / default
    return _env("LLM_PRIMARY_API_KEY") or _env("OPENAI_API_KEY")


def build_fallback_config_list(
    *,
    primary_model: str | None = None,
    primary_api_key: str | None = None,
    primary_api_type: str | None = None,
    primary_base_url: str | None = None,
    fallback_models: list[str] | None = None,
    fallback_api_keys: list[str] | None = None,
    fallback_base_urls: list[str] | None = None,
    temperature: float | None = None,
    timeout: int | None = None,
) -> list[dict[str, Any]]:
    """Build an AG2-compatible ``config_list`` with a primary model and fallbacks.

    AG2 iterates entries in ``config_list`` in order when a model returns a
    rate-limit or availability error, so ordering here controls failover priority.

    Each entry includes an ``api_type`` field so downstream routing (via
    ``llm_config_to_ag2_config``) can select the right AG2 config class without
    inspecting model name patterns.

    Returns a list with at least one entry (the primary). Falls back to env
    vars for any kwarg not explicitly provided.

    Example AG2 usage::

        llm_config = {
            "config_list": build_fallback_config_list(),
            "temperature": 0.0,
            "timeout": 120,
        }
    """
    fallback_enabled = _env("LLM_FALLBACK_ENABLED", "true").lower() != "false"

    # ---- Primary ----
    resolved_api_type = (primary_api_type or _env("LLM_PRIMARY_API_TYPE", "openai")).lower()
    resolved_primary = primary_model or _env("LLM_PRIMARY_MODEL", "gpt-4o")
    resolved_api_key = _resolve_api_key(resolved_api_type, primary_api_key or None)
    resolved_base_url = primary_base_url or _env("LLM_PRIMARY_BASE_URL") or None

    primary_entry: dict[str, Any] = {"model": resolved_primary, "api_type": resolved_api_type}
    if resolved_api_key:
        primary_entry["api_key"] = resolved_api_key
    if resolved_base_url:
        primary_entry["base_url"] = resolved_base_url

    config_list: list[dict[str, Any]] = [primary_entry]

    if not fallback_enabled:
        return config_list

    # ---- Fallbacks ----
    resolved_fallback_models = fallback_models if fallback_models is not None else _env_list("LLM_FALLBACK_MODELS")
    resolved_fallback_keys = fallback_api_keys if fallback_api_keys is not None else _env_list("LLM_FALLBACK_API_KEYS")
    resolved_fallback_urls = fallback_base_urls if fallback_base_urls is not None else _env_list("LLM_FALLBACK_BASE_URLS")

    for idx, model in enumerate(resolved_fallback_models):
        entry: dict[str, Any] = {"model": model}
        if idx < len(resolved_fallback_keys) and resolved_fallback_keys[idx]:
            entry["api_key"] = resolved_fallback_keys[idx]
        elif resolved_api_key:
            # Reuse primary key when no per-fallback key is set
            entry["api_key"] = resolved_api_key
        if idx < len(resolved_fallback_urls) and resolved_fallback_urls[idx]:
            entry["base_url"] = resolved_fallback_urls[idx]
        config_list.append(entry)

    if len(config_list) > 1:
        model_names = [e["model"] for e in config_list]
        logger.debug(
            "LLM_FALLBACK_CONFIG primary=%s fallbacks=%s",
            model_names[0],
            model_names[1:],
        )

    return config_list


def build_fallback_llm_config(
    *,
    primary_model: str | None = None,
    primary_api_key: str | None = None,
    primary_api_type: str | None = None,
    primary_base_url: str | None = None,
    fallback_models: list[str] | None = None,
    fallback_api_keys: list[str] | None = None,
    fallback_base_urls: list[str] | None = None,
    temperature: float | None = None,
    timeout: int | None = None,
    seed: int | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a complete AG2 ``llm_config`` dict with fallback config_list.

    Build an AG2 ``llm_config`` with fallback model support via env-driven
    config_list construction.

    Usage in workflow tool code::

        from mozaiksai.core.adapters.llm_fallback import build_fallback_llm_config

        llm_config = build_fallback_llm_config(
            primary_model="gpt-4o",
            fallback_models=["gpt-4o-mini"],
        )
    """
    resolved_temp = temperature if temperature is not None else float(_env("LLM_TEMPERATURE", "0.0"))
    resolved_timeout = timeout if timeout is not None else int(_env("LLM_TIMEOUT_SECONDS", "120"))

    config: dict[str, Any] = {
        "config_list": build_fallback_config_list(
            primary_model=primary_model,
            primary_api_key=primary_api_key,
            primary_api_type=primary_api_type,
            primary_base_url=primary_base_url,
            fallback_models=fallback_models,
            fallback_api_keys=fallback_api_keys,
            fallback_base_urls=fallback_base_urls,
        ),
        "temperature": resolved_temp,
        "timeout": resolved_timeout,
    }
    if seed is not None:
        config["seed"] = seed
    if extra:
        config.update(extra)
    return config


# ---------------------------------------------------------------------------
# AG2 typed config factory
# ---------------------------------------------------------------------------

def llm_config_to_ag2_config(llm_config: dict[str, Any]) -> Any:
    """Convert an AG2 ``llm_config`` dict to a typed AG2 ``ModelConfig`` instance.

    Routes to the correct provider config class based on the ``api_type`` field
    in the first ``config_list`` entry.  Supported api_type values:

    - ``"openai"`` (default) → ``OpenAIConfig``
    - ``"google"``           → ``GeminiConfig``  (Google AI Studio free tier or Vertex AI)
    - ``"anthropic"``        → ``AnthropicConfig``
    - ``"ollama"``           → ``OllamaConfig``  (local inference)

    Any unrecognised api_type falls through to ``OpenAIConfig``.
    """
    config_list = llm_config.get("config_list") or []
    if not config_list:
        raise ValueError("llm_config has no config_list entries")
    entry = config_list[0]
    api_type = (entry.get("api_type") or "openai").lower()
    model = entry.get("model") or "gpt-4o-mini"
    api_key = entry.get("api_key") or None
    base_url = entry.get("base_url") or None
    temperature = llm_config.get("temperature")
    streaming_raw = llm_config.get("streaming")
    streaming = True if streaming_raw is None else bool(streaming_raw)
    timeout = llm_config.get("timeout")

    if api_type == "google":
        from ag2.config import GeminiConfig  # type: ignore[attr-defined]
        kwargs: dict[str, Any] = {
            "model": model,
            "api_key": api_key,
            "temperature": temperature,
            "streaming": streaming,
        }
        if llm_config.get("response_modalities"):
            kwargs["response_modalities"] = llm_config["response_modalities"]
        if llm_config.get("image_config") is not None:
            kwargs["image_config"] = llm_config["image_config"]
        return GeminiConfig(**kwargs)
    if api_type == "anthropic":
        from ag2.config import AnthropicConfig  # type: ignore[attr-defined]
        return AnthropicConfig(model=model, api_key=api_key, temperature=temperature, streaming=streaming)
    if api_type == "ollama":
        from ag2.config import OllamaConfig  # type: ignore[attr-defined]
        return OllamaConfig(
            model=model,
            host=base_url or "http://localhost:11434",
            temperature=temperature,
            streaming=streaming,
        )
    # openai / azure / default
    if api_type == "openai" and bool(llm_config.get("use_responses_api") or llm_config.get("responses_api")):
        from ag2.config import OpenAIResponsesConfig

        kwargs: dict[str, Any] = {
            "model": model,
            "api_key": api_key,
            "base_url": base_url,
            "temperature": temperature,
            "streaming": streaming,
        }
        if timeout is not None:
            kwargs["timeout"] = timeout
        for key in ("max_output_tokens", "max_tool_calls", "parallel_tool_calls", "store"):
            if key in llm_config:
                kwargs[key] = llm_config[key]
        return OpenAIResponsesConfig(**kwargs)

    from ag2.config import OpenAIConfig
    kwargs = {
        "model": model,
        "api_key": api_key,
        "base_url": base_url,
        "temperature": temperature,
        "streaming": streaming,
    }
    if timeout is not None:
        kwargs["timeout"] = timeout
    return OpenAIConfig(**kwargs)


# ---------------------------------------------------------------------------
# Provider health helpers (circuit-breaker aware)
# ---------------------------------------------------------------------------

def get_healthy_config_list(
    config_list: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Filter config_list to entries whose provider circuit breaker is CLOSED.

    Entries for providers with an OPEN circuit breaker are moved to the end so
    AG2 deprioritises them. If all are open, the original list is returned
    unchanged (caller must still handle failure).

    Uses the model name as the circuit breaker key (``llm::{model}``).
    """
    try:
        from mozaiksai.core.adapters.circuit_breaker import CircuitState, get_circuit_breaker_sync

        healthy: list[dict[str, Any]] = []
        degraded: list[dict[str, Any]] = []

        for entry in config_list:
            model = entry.get("model", "unknown")
            breaker = get_circuit_breaker_sync(f"llm::{model}")
            if breaker._state == CircuitState.OPEN:
                degraded.append(entry)
                logger.warning("LLM_CIRCUIT_OPEN model=%s — deprioritising in config_list", model)
            else:
                healthy.append(entry)

        return healthy + degraded if healthy else config_list

    except Exception:
        # Circuit breaker unavailable — return unfiltered list
        return config_list


__all__ = [
    "build_fallback_config_list",
    "build_fallback_llm_config",
    "get_healthy_config_list",
    "llm_config_to_ag2_config",
]
