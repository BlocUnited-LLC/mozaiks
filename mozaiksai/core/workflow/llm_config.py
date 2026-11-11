"""
Centralized LLM configuration & caching utilities.

Goals
-----
1. Single place for building the config_list used by AG2 provider configuration.
2. Lightweight async cache (raw provider list + final llm_config variants) with TTL.
3. Support structured outputs (response_format) without rebuilding base pieces.
4. Gentle fallback when Mongo unavailable (env-only bootstrap) WITH price mapping.
5. Keep surface minimal (clean implementation) for maintainability.

Environment (optional)
----------------------
LLM_CONFIG_CACHE_TTL   Seconds to keep raw provider list (default 300)
DEFAULT_LLM_MODEL      Override fallback model name (default gemini-3.8-flash)
OPENAI_MODEL_FALLBACK  OpenAI model order when LLM_PRIMARY_MODEL is absent
LLM_PRIMARY_API_TYPE  Selected provider: google (default), openai, anthropic, ollama
LLM_PRIMARY_MODEL     Explicit model for the selected provider
LLM_MAX_TOKENS        Optional positive output limit, mapped to the selected API
LLM_USE_RESPONSES_API Select OpenAI Responses instead of Chat Completions
LLM_REASONING_EFFORT  OpenAI reasoning effort, passed through in the API's shape
LLM_STRUCTURED_OUTPUT_MODE  AG2 schema transport: native (default) or prompted

Public API
----------
async get_llm_config(response_format=None, extra_config=None, cache=True)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from typing import Any

from pydantic import BaseModel

from mozaiksai.core.adapters.llm_fallback import (
    DEFAULT_LLM_API_TYPE,
    DEFAULT_MODELS_BY_API_TYPE,
    MODEL_API_KEY_ENV_NAMES,
    build_fallback_config_list,
    resolve_structured_output_mode,
)
from mozaiksai.core.core_config import get_mongo_client
from mozaiksai.core.data.persistence.namespaces import SYSTEM_DATABASE, BuilderCollections

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cache Structures
# ---------------------------------------------------------------------------
_RAW_CONFIG_CACHE: dict[str, Any] = {"config_list": None, "loaded_at": 0}
_LLM_CONFIG_CACHE: dict[str, dict[str, Any]] = {}
_RAW_LOCK = asyncio.Lock()
_LLM_LOCK = asyncio.Lock()

# Change detection state (in-memory only)
_LAST_PROVIDER_SIGNATURE: str | None = None
_LAST_API_KEYS: set[str] = set()

_CACHE_TTL = int(os.getenv("LLM_CONFIG_CACHE_TTL", "300"))

# Deterministic seed for AG2 model calls.
# Enhancement: allow optional environment override (LLM_DEFAULT_CACHE_SEED) OR
# randomized process seed when RANDOMIZE_DEFAULT_CACHE_SEED=1 (unless overridden per-chat).
_env_seed = os.getenv("LLM_DEFAULT_CACHE_SEED")
_randomize = os.getenv("RANDOMIZE_DEFAULT_CACHE_SEED", "0").lower() in ("1", "true", "yes", "on")
try:
    if _env_seed is not None:
        _DEFAULT_CACHE_SEED = int(_env_seed)
    elif _randomize:
        import random
        _DEFAULT_CACHE_SEED = random.randint(1, 2**31 - 1)
    else:
        _DEFAULT_CACHE_SEED = _env_seed 
except Exception:
    _DEFAULT_CACHE_SEED = _env_seed
logger.info("LLM_CONFIG_DEFAULT_CACHE_SEED_SELECTED seed=%s randomized=%s env_override=%s", _DEFAULT_CACHE_SEED, _randomize, 'yes' if _env_seed else 'no')

# Static price map (prompt, completion) USD per 1K tokens (example values)
PRICE_MAP: dict[str, list[float]] = {
    "o3-mini": [0.0011, 0.0044],
    "gpt-4.1-nano": [0.0001, 0.0004],
    "gpt-5-nano": [0.00015, 0.0006],
}


# ---------------------------------------------------------------------------
# Raw Provider Config Loader
# ---------------------------------------------------------------------------
# A provider entry may include 'price': List[float] -> [prompt_per_1k, completion_per_1k]
ProviderConfig = dict[str, Any]

async def _load_raw_config_list(force: bool = False) -> list[ProviderConfig]:
    """Load provider entries: first attempt Mongo -> fallback to env-defined list.

    Returns list of dicts each containing at least: {"model": <name>, "api_key": <secret>}.
    """
    now = time.time()
    if not force and _RAW_CONFIG_CACHE["config_list"] and (now - _RAW_CONFIG_CACHE["loaded_at"] < _CACHE_TTL):
        return _RAW_CONFIG_CACHE["config_list"]  # type: ignore

    async with _RAW_LOCK:
        # Re-check after awaiting
        now = time.time()
        if not force and _RAW_CONFIG_CACHE["config_list"] and (now - _RAW_CONFIG_CACHE["loaded_at"] < _CACHE_TTL):
            return _RAW_CONFIG_CACHE["config_list"]  # type: ignore

        config_list: list[ProviderConfig] = []

        # Attempt DB fetch
        db_doc = None
        skip_mongo = os.getenv("MOZAIKS_LLM_CONFIG_SKIP_MONGO", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if not skip_mongo:
            try:
                db = get_mongo_client()[SYSTEM_DATABASE]
                db_doc = await db[BuilderCollections.LLM_CONFIG].find_one()
            except Exception as e:  # pragma: no cover
                logger.debug("[LLM_CONFIG] Mongo fetch failed, will fallback: %s", type(e).__name__)

        if db_doc and isinstance(db_doc, dict):
            providers = db_doc.get("providers") or db_doc.get("models") or [db_doc]
            if not isinstance(providers, list):
                providers = [providers]
            configured_primary_api_type = os.getenv(
                "LLM_PRIMARY_API_TYPE", DEFAULT_LLM_API_TYPE,
            ).strip().lower()
            for provider in providers:
                if not isinstance(provider, dict):
                    raise ValueError("Stored LLM provider must be an object")
                api_type = str(provider.get("api_type") or "openai").strip().lower()
                model = provider.get("model") or provider.get("Model") or provider.get("name")
                model = model or os.getenv("DEFAULT_LLM_MODEL") or ("gpt-5-nano" if api_type == "openai" else None)
                if not model:
                    raise ValueError("Stored LLM provider requires an explicit model")
                key = provider.get("api_key") or provider.get("ApiKey")
                if not key:
                    key_env_names = MODEL_API_KEY_ENV_NAMES.get(api_type, ())
                    if api_type != configured_primary_api_type:
                        key_env_names = tuple(
                            name for name in key_env_names if name != "LLM_PRIMARY_API_KEY"
                        )
                    key = next(
                        (provider[name] for name in key_env_names if provider.get(name)),
                        None,
                    )
                entry = build_fallback_config_list(
                    primary_model=model, primary_api_type=api_type, primary_api_key=key,
                    primary_base_url=provider.get("base_url"), fallback_models=[],
                    include_primary_key=api_type == configured_primary_api_type,
                )[0]
                if not provider.get("base_url"):
                    entry.pop("base_url", None)
                if "price" in provider:
                    entry["price"] = provider["price"]
                for name in (
                    "max_tokens", "max_completion_tokens", "max_output_tokens", "max_retries",
                    "use_responses_api", "responses_api", "reasoning", "reasoning_effort",
                    "max_tool_calls", "parallel_tool_calls", "store",
                ):
                    if name in provider:
                        entry[name] = provider[name]
                config_list.append(entry)

        if not config_list:
            api_type = os.getenv("LLM_PRIMARY_API_TYPE", DEFAULT_LLM_API_TYPE).strip().lower()
            legacy_models = [model.strip() for model in os.getenv("OPENAI_MODEL_FALLBACK", "").split(",") if model.strip()] if api_type == "openai" else []
            primary_model = os.getenv("LLM_PRIMARY_MODEL", "").strip()
            if not primary_model:
                primary_model = (legacy_models[0] if legacy_models else os.getenv("DEFAULT_LLM_MODEL", "").strip())
            if not primary_model:
                primary_model = DEFAULT_MODELS_BY_API_TYPE.get(api_type)
            if not primary_model:
                raise ValueError("LLM_PRIMARY_MODEL is required for the selected provider")
            fallback_models = None
            if legacy_models and not os.getenv("LLM_FALLBACK_MODELS"):
                fallback_models = [model for model in legacy_models if model != primary_model]
            config_list = build_fallback_config_list(
                primary_model=primary_model, primary_api_type=api_type,
                fallback_models=fallback_models,
            )
            if api_type == "openai":
                raw_responses = os.getenv("LLM_USE_RESPONSES_API", "").strip().lower()
                if raw_responses and raw_responses not in {"true", "false", "1", "0"}:
                    raise ValueError("LLM_USE_RESPONSES_API must be true or false")
                effort = os.getenv("LLM_REASONING_EFFORT", "").strip()
                for entry in config_list:
                    if raw_responses:
                        entry["use_responses_api"] = raw_responses in {"true", "1"}
                    if effort:
                        if entry.get("use_responses_api"):
                            entry["reasoning"] = {"effort": effort}
                        else:
                            entry["reasoning_effort"] = effort
        for entry in config_list:
            if "price" not in entry and entry["model"] in PRICE_MAP:
                entry["price"] = PRICE_MAP[entry["model"]]

    _RAW_CONFIG_CACHE["config_list"] = config_list
    _RAW_CONFIG_CACHE["loaded_at"] = time.time()
    logger.debug("[LLM_CONFIG] Loaded provider list (count=%s)", len(config_list))

    # -----------------------------
    # Change detection / invalidation
    # -----------------------------
    global _LAST_PROVIDER_SIGNATURE, _LAST_API_KEYS
    try:
        # Build deterministic signature over provider entries (model, api_key hash, price)
        sig_parts: list[str] = []
        api_keys_now: set[str] = set()
        for e in config_list:
            model = e.get("model", "")
            api_key_val = e.get("api_key", "") or ""
            # Avoid logging raw key; hash it
            api_key_hash = hashlib.sha256(api_key_val.encode()).hexdigest() if api_key_val else ""
            api_keys_now.add(api_key_hash)
            sig_parts.append(json.dumps({**e, "api_key": api_key_hash}, sort_keys=True))
        signature = hashlib.sha256("|".join(sig_parts).encode()).hexdigest()

        provider_changed = _LAST_PROVIDER_SIGNATURE is not None and signature != _LAST_PROVIDER_SIGNATURE
        keys_changed = bool(_LAST_API_KEYS) and api_keys_now != _LAST_API_KEYS
        if provider_changed or keys_changed:
            # Invalidate built llm configs; raw list already replaced above
            async with _LLM_LOCK:
                _LLM_CONFIG_CACHE.clear()
            logger.info(
                "[LLM_CONFIG] Provider list change detected (providers_changed=%s api_keys_changed=%s); cleared built llm_config cache", provider_changed, keys_changed)
        _LAST_PROVIDER_SIGNATURE = signature
        _LAST_API_KEYS = api_keys_now
    except Exception as sig_err:  # pragma: no cover
        logger.debug("[LLM_CONFIG] Provider signature generation failed (non-fatal): %s", sig_err)
    return config_list


# ---------------------------------------------------------------------------
# Key construction & Cache helpers
# ---------------------------------------------------------------------------
def _build_llm_cache_key(*, response_format: type[BaseModel] | None, extra_config: dict[str, Any] | None) -> str:
    parts = ["base"]
    if response_format:
        # Include schema hash so structural changes to model invalidate cache automatically
        try:
            schema_json = json.dumps(response_format.model_json_schema(), sort_keys=True)
            schema_hash = hashlib.sha256(schema_json.encode()).hexdigest()[:10]
        except Exception:
            schema_hash = "unknown"
        parts.append(f"rf:{response_format.__name__}:{schema_hash}")
    if extra_config:
        encoded = json.dumps(extra_config, sort_keys=True, default=str)
        parts.append("x:" + hashlib.sha256(encoded.encode()).hexdigest())
    return "|".join(parts)


# ---------------------------------------------------------------------------
# Public Builders
# ---------------------------------------------------------------------------
async def get_llm_config(
    *,
    response_format: type[BaseModel] | None = None,
    extra_config: dict[str, Any] | None = None,
    cache: bool = True,
) -> tuple[Any | None, dict[str, Any]]:
    """Build (or retrieve from cache) an LLM runtime config.

    Returns a tuple (wrapper_placeholder, llm_config). The first element is
    reserved and currently always None; the second is the dict converted into
    the selected AG2 provider configuration.
    """
    extra_config = dict(extra_config or {})
    extra_config["structured_output_mode"] = resolve_structured_output_mode(extra_config)
    raw_limit = os.getenv("LLM_MAX_TOKENS", "").strip()
    limit = None
    if raw_limit:
        try:
            limit = int(raw_limit)
        except ValueError:
            raise ValueError("LLM_MAX_TOKENS must be a positive integer") from None
        if limit < 1:
            raise ValueError("LLM_MAX_TOKENS must be a positive integer")
    # Refresh raw providers before consulting derived variants so TTL and
    # explicit invalidation can replace a previously cached provider.
    config_list = await _load_raw_config_list()
    if limit is not None and not {"max_tokens", "max_output_tokens", "max_completion_tokens"}.intersection(extra_config):
        selected = (extra_config.get("config_list") or config_list)[0]
        api_type = selected.get("api_type", "openai")
        responses = extra_config.get(
            "use_responses_api", extra_config.get("responses_api", selected.get("use_responses_api", selected.get("responses_api", False))),
        )
        if not {"max_tokens", "max_output_tokens", "max_completion_tokens"}.intersection(selected):
            limit_name = "max_output_tokens" if api_type == "google" or (api_type == "openai" and responses) else "max_tokens"
            extra_config[limit_name] = limit
    cache_key = _build_llm_cache_key(
        response_format=response_format, extra_config=extra_config
    )
    if cache and cache_key in _LLM_CONFIG_CACHE:
        import copy
        cfg = copy.deepcopy(_LLM_CONFIG_CACHE[cache_key])
        return None, cfg

    # Determine seed origin BEFORE constructing final config for clearer logging
    seed_from_extra = None
    if extra_config and "cache_seed" in extra_config:
        try:
            seed_from_extra = int(extra_config["cache_seed"])
        except Exception:
            logger.debug("[LLM_CONFIG] Provided extra_config.cache_seed not coercible to int: %s; falling back to default", extra_config.get('cache_seed'))
    selected_seed = seed_from_extra if seed_from_extra is not None else _DEFAULT_CACHE_SEED
    seed_origin = "per-chat" if seed_from_extra is not None else "process-default"
    if seed_origin == "process-default":
        logger.debug(
            "[LLM_CONFIG] Using process-level default cache seed",
            extra={"seed": selected_seed, "reason": "no per-chat override"},
        )
    else:
        logger.debug(
            "[LLM_CONFIG] Using per-chat cache seed override",
            extra={"seed": selected_seed},
        )

    # NOTE: response_format is accepted as a parameter for cache-key differentiation
    # only. It is NOT stored in the returned dict because llm_config_to_ag2_config()
    # never reads it — actual structured-output enforcement is done via
    # Agent(response_schema=...) in factory.py, which resolves the schema
    # independently through get_structured_outputs_for_workflow().
    llm_config: dict[str, Any] = {
        "timeout": extra_config.get("timeout") if extra_config and "timeout" in extra_config else 600,
        "cache_seed": selected_seed,
        "config_list": config_list,
        "tools": [],  # Required by AG2 for tool registration
    }
    if extra_config:
        # Merge remaining extras without overwriting core entries already set unless user explicitly wants it
        for k, v in extra_config.items():
            if k not in ("timeout", "cache_seed"):
                llm_config[k] = v

    if cache:
        import copy
        async with _LLM_LOCK:
            _LLM_CONFIG_CACHE[cache_key] = copy.deepcopy(llm_config)

    logger.debug(
        "[LLM_CONFIG] Built config (rf=%s, extras=%s)", 'yes' if response_format else 'no', bool(extra_config))
    
    # Additional safety check - validate the config_list entries before return
    config_list = llm_config.get('config_list', [])
    logger.debug("[LLM_CONFIG] Validating config_list with %s entries", len(config_list))
    
    for i, entry in enumerate(config_list):
        if not isinstance(entry, dict):
            raise ValueError(f"Config entry [{i}] is not a dict: {type(entry)}")
        elif not entry.get('model'):
            raise ValueError(f"Config entry [{i}] missing required model field")
        else:
            logger.debug("[LLM_CONFIG] Entry [%s] validation OK: model=%s", i, entry.get('model'))
    
    # Final check - ensure we don't have any extra config modifications happening
    logger.debug("[LLM_CONFIG] About to return config with config_list length: %s", len(llm_config.get('config_list', [])))
    return None, llm_config


# ---------------------------------------------------------------------------
# Maintenance / Admin Helpers
# ---------------------------------------------------------------------------
def clear_llm_caches(raw: bool = True, built: bool = True) -> None:
    """Explicitly clear in-memory caches.

    Args:
        raw: Clear provider list cache (forces DB/env reload next request)
        built: Clear derived llm_config objects
    """
    global _LAST_PROVIDER_SIGNATURE, _LAST_API_KEYS
    if raw:
        _RAW_CONFIG_CACHE["config_list"] = None
        _RAW_CONFIG_CACHE["loaded_at"] = 0
    if built:
        _LAST_PROVIDER_SIGNATURE = None
        _LAST_API_KEYS = set()
        _LLM_CONFIG_CACHE.clear()
    logger.debug("[LLM_CONFIG] Caches cleared raw=%s built=%s", raw, built)


__all__ = [
    "get_llm_config",
    "PRICE_MAP",
    "clear_llm_caches",
]
