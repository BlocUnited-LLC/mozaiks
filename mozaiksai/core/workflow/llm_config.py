"""
Centralized LLM configuration & caching utilities.

Goals
-----
1. Single place for building the config_list used by AG2 OpenAI configuration.
2. Lightweight async cache (raw provider list + final llm_config variants) with TTL.
3. Support structured outputs (response_format) without rebuilding base pieces.
4. Gentle fallback when Mongo unavailable (env-only bootstrap) WITH price mapping.
5. Keep surface minimal (clean implementation) for maintainability.

Environment (optional)
----------------------
LLM_CONFIG_CACHE_TTL   Seconds to keep raw provider list (default 300)
DEFAULT_LLM_MODEL      Override fallback model name (default gpt-5-nano)
OPENAI_MODEL_FALLBACK  Comma-separated list of fallback model names

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

from mozaiksai.core.core_config import get_mongo_client, get_secret
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
                logger.debug("[LLM_CONFIG] Mongo fetch failed, will fallback: %s", e)

        if db_doc and isinstance(db_doc, dict):
            # Avoid dumping raw secrets from the DB document
            def _redact_val(v: Any) -> Any:
                if not isinstance(v, str):
                    return v
                if len(v) <= 8:
                    return "***"
                return v[:4] + "***REDACTED***" + v[-4:]

            def _redact_mapping(d: dict[str, Any]) -> dict[str, Any]:
                sens_keys = {"api_key", "apikey", "authorization", "secret", "password", "token", "clientsecret", "accountkey"}
                out: dict[str, Any] = {}
                for k, v in d.items():
                    if isinstance(v, dict):
                        out[k] = _redact_mapping(v)
                    elif any(s in k.lower() for s in sens_keys):
                        out[k] = _redact_val(v)
                    else:
                        out[k] = v
                return out

            try:
                logger.debug("[LLM_CONFIG] DB document found: %s", _redact_mapping(db_doc))
            except Exception:
                logger.debug("[LLM_CONFIG] DB document found (redaction failed to serialize)")
            # Expect a shape like: { model: 'gpt-5-nano', price: {...}, ... }
            # Support single or list of providers.
            providers = db_doc.get("providers") or db_doc.get("models") or [db_doc]
            if not isinstance(providers, list):
                providers = [providers]
            logger.debug("[LLM_CONFIG] Processing %s providers from DB", len(providers))
            for i, p in enumerate(providers):
                try:
                    logger.debug("[LLM_CONFIG] Processing provider %s: %s", i, _redact_mapping(p))
                except Exception:
                    logger.debug("[LLM_CONFIG] Processing provider %s: <redaction error>", i)
                # Extract model name with logging for debugging
                model_lowercase = p.get("model")
                model_capitalized = p.get("Model")
                model_name_field = p.get("name")
                env_default = os.getenv("DEFAULT_LLM_MODEL", "gpt-5-nano")
                model_name = model_lowercase or model_capitalized or model_name_field or env_default
                logger.debug(
                    "[LLM_CONFIG] Model extraction for provider %s: ", i)
                # First check if API key is in the DB document
                api_key = p.get("api_key") or p.get("ApiKey") or p.get("OPENAI_API_KEY")
                if not api_key:
                    # Fallback to secret/env
                    try:
                        api_key = get_secret("OpenAIApiKey")
                    except Exception:
                        api_key = os.getenv("OPENAI_API_KEY", "")
                entry = {"model": model_name, "api_key": api_key}
                if "price" in p:
                    entry["price"] = p["price"]
                else:
                    if model_name in PRICE_MAP:
                        entry["price"] = PRICE_MAP[model_name]
                safe_entry = {**entry, "api_key": "***REDACTED***" if entry.get("api_key") else entry.get("api_key")}
                logger.debug("[LLM_CONFIG] Created entry %s: %s", i, safe_entry)
                config_list.append(entry)

        # Fallback if empty
        if not config_list:
            try:
                api_key = get_secret("OpenAIApiKey")
            except Exception:
                api_key = os.getenv("OPENAI_API_KEY", "")
            fallback_models: list[str] = []
            if os.getenv("OPENAI_MODEL_FALLBACK"):
                fallback_models = [m.strip() for m in os.getenv("OPENAI_MODEL_FALLBACK", "").split(",") if m.strip()]
            if not fallback_models:
                fallback_models = [os.getenv("DEFAULT_LLM_MODEL", "gpt-5-nano")]
            for m in fallback_models:
                entry = {"model": m, "api_key": api_key}
                if m in PRICE_MAP:
                    # cast list to tuple for immutability (optional)
                    mapped = PRICE_MAP[m]
                    entry["price"] = mapped  # type: ignore[assignment]
                config_list.append(entry)

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
            price = e.get("price")
            sig_parts.append(json.dumps({"model": model, "k": api_key_hash, "price": price}, sort_keys=True))
        signature = hashlib.sha256("|".join(sorted(sig_parts)).encode()).hexdigest()

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
    # Debug: log each config entry
    for i, entry in enumerate(config_list):
        safe_entry = {**entry, "api_key": "***REDACTED***" if entry.get("api_key") else entry.get("api_key")}
        logger.debug("[LLM_CONFIG] config_list[%s]: %s", i, safe_entry)
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
        # Deterministic ordering (avoid giant keys; only include primitive scalars)
        norm_items = []
        for k in sorted(extra_config.keys()):
            v = extra_config[k]
            if isinstance(v, (str, int, float, bool)):
                norm_items.append(f"{k}={v}")
        if norm_items:
            parts.append("x:" + ",".join(norm_items))
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
    an AG2 1.0 OpenAIConfig.
    """
    cache_key = _build_llm_cache_key(
        response_format=response_format, extra_config=extra_config
    )
    if cache and cache_key in _LLM_CONFIG_CACHE:
        import copy
        cfg = copy.deepcopy(_LLM_CONFIG_CACHE[cache_key])
        return None, cfg

    # Ensure base provider list loaded
    config_list = await _load_raw_config_list()

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
            extra={"seed": selected_seed, "reason": "no per-chat override", "cache_key_fragment": cache_key[:60]},
        )
    else:
        logger.debug(
            "[LLM_CONFIG] Using per-chat cache seed override",
            extra={"seed": selected_seed, "cache_key_fragment": cache_key[:60]},
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
        "[LLM_CONFIG] Built config (rf=%s, extras=%s, cache_key=%s)", 'yes' if response_format else 'no', bool(extra_config), cache_key)
    logger.debug("[LLM_CONFIG] Final llm_config before return: %s", llm_config)
    
    # Additional safety check - validate the config_list entries before return
    config_list = llm_config.get('config_list', [])
    logger.debug("[LLM_CONFIG] Validating config_list with %s entries", len(config_list))
    
    for i, entry in enumerate(config_list):
        logger.debug("[LLM_CONFIG] Checking entry [%s]: %s", i, entry)
        if not isinstance(entry, dict):
            logger.error("[LLM_CONFIG] VALIDATION ERROR: Entry [%d] is not a dict: %s %s", i, type(entry), entry)
            raise ValueError(f"Config entry [{i}] is not a dict: {type(entry)}")
        elif not entry.get('model'):
            logger.error("[LLM_CONFIG] VALIDATION ERROR: Entry [%d] missing model field: %s", i, entry)
            raise ValueError(f"Config entry [{i}] missing required model field: {entry}")
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
        _LAST_PROVIDER_SIGNATURE = None
        _LAST_API_KEYS = set()
    if built:
        _LLM_CONFIG_CACHE.clear()
    logger.debug("[LLM_CONFIG] Caches cleared raw=%s built=%s", raw, built)


__all__ = [
    "get_llm_config",
    "PRICE_MAP",
    "clear_llm_caches",
]
