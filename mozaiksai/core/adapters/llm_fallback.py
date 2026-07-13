# ==============================================================================
# FILE: mozaiksai/core/adapters/llm_fallback.py
# DESCRIPTION: LLM provider fallback config builder for AG2-based workflows.
#
# Purpose: build an AG2-compatible ``config_list`` that tries a primary model
# and a prioritized list of fallback models when the primary is unavailable.
# AG2 natively iterates config_list entries on failure, so the Mozaiks adapter
# only needs to construct the ordered list correctly.
#
# Integration points:
#   ag2_runner.build_llm_config()  — accepts fallback_models kwarg
#   ag2_network_runner             — callers pass llm_config built here
#
# Env vars (all optional; defaults keep existing behavior):
#   LLM_PRIMARY_MODEL            model id for primary provider (gpt-4o)
#   LLM_PRIMARY_API_KEY          api key for primary provider (OPENAI_API_KEY)
#   LLM_PRIMARY_BASE_URL         optional base URL override (Azure, local, etc.)
#   LLM_FALLBACK_MODELS          comma-separated model ids, in priority order
#   LLM_FALLBACK_API_KEYS        comma-separated api keys, parallel to models
#   LLM_FALLBACK_BASE_URLS       comma-separated base URLs, parallel to models
#   LLM_TEMPERATURE              shared temperature (0.0)
#   LLM_TIMEOUT_SECONDS          shared request timeout (120)
#   LLM_FALLBACK_ENABLED         set to "false" to disable fallback (true)
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

def build_fallback_config_list(
    *,
    primary_model: str | None = None,
    primary_api_key: str | None = None,
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
    resolved_primary = primary_model or _env("LLM_PRIMARY_MODEL", "gpt-4o")
    resolved_api_key = (
        primary_api_key
        or _env("LLM_PRIMARY_API_KEY")
        or _env("OPENAI_API_KEY")
    )
    resolved_base_url = primary_base_url or _env("LLM_PRIMARY_BASE_URL") or None

    primary_entry: dict[str, Any] = {"model": resolved_primary}
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
]
