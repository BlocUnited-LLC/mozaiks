"""
Centralized LLM configuration & caching utilities.

Goals
-----
1. Single place for building the config_list used by Autogen's OpenAIWrapper.
2. Lightweight async cache (raw provider list + final llm_config variants) with TTL.
3. Support structured outputs (response_format) without rebuilding base pieces.
4. Gentle fallback when Mongo unavailable (env-only bootstrap) WITH price mapping.
5. Keep surface minimal (clean implementation) for maintainability.

Environment (optional)
----------------------
LLM_CONFIG_CACHE_TTL   Seconds to keep raw provider list (default 300)
DEFAULT_LLM_MODEL      Override fallback model name (default gpt-4o-mini)
OPENAI_MODEL_FALLBACK  Comma-separated list of fallback model names

Public API
----------z
async get_llm_config(response_format=None, stream=False, extra_config=None, cache=True)
"""
from __future__ import annotations

import os
import time
import asyncio
import logging
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type, Set
from pydantic import BaseModel


# Reuse existing secret + Mongo helpers (keeps KeyVault logic centralized)
try:  # pragma: no cover - defensive import
    from mozaiksai.core.core_config import get_secret, get_mongo_client  # type: ignore
except Exception:  # pragma: no cover
    get_secret = None  # type: ignore
    get_mongo_client = None  # type: ignore

logger = logging.getLogger(__name__)


def _attach_autogen_cache(llm_config: Dict[str, Any]) -> None:
    """Attach an Autogen disk cache rooted in a writable location.

    Autogen's legacy cache behavior uses cache_root='.cache', which can fail on
    Docker Desktop + bind mounts. Injecting a Cache object forces Autogen to use
    our explicit cache root instead.
    """

    seed = llm_config.get("cache_seed")
    if seed is None:
        return
    try:
        cache_seed = int(seed)
    except Exception:
        return

    try:
        from autogen.cache import Cache  # type: ignore
    except Exception as err:
        logger.debug(f"[LLM_CONFIG] Autogen cache unavailable; skipping cache attach: {err}")
        return

    cache_root = (
        os.getenv("MOZAIKS_AUTOGEN_CACHE_DIR")
        or os.getenv("AUTOGEN_CACHE_DIR")
        or os.path.join(tempfile.gettempdir(), "mozaiksai_autogen_cache")
    )

    try:
        Path(cache_root).mkdir(parents=True, exist_ok=True)
    except Exception as mk_err:
        logger.warning(f"[LLM_CONFIG] Autogen cache disabled (cannot create dir {cache_root!r}): {mk_err}")
        return

    try:
        cache_obj = Cache.disk(cache_seed, str(cache_root))
    except Exception as cache_err:
        logger.warning(f"[LLM_CONFIG] Autogen cache disabled (failed to init disk cache at {cache_root!r}): {cache_err}")
        return

    config_list = llm_config.get("config_list")
    if not isinstance(config_list, list):
        return
    for entry in config_list:
        if isinstance(entry, dict) and "cache" not in entry:
            entry["cache"] = cache_obj

# ---------------------------------------------------------------------------
# Cache Structures
# ---------------------------------------------------------------------------
_RAW_CONFIG_CACHE: Dict[str, Any] = {"config_list": None, "loaded_at": 0}
_LLM_CONFIG_CACHE: Dict[str, Dict[str, Any]] = {}
_RAW_LOCK = asyncio.Lock()
_LLM_LOCK = asyncio.Lock()

# Change detection state (in-memory only)
_LAST_PROVIDER_SIGNATURE: Optional[str] = None
_LAST_API_KEYS: Set[str] = set()

_CACHE_TTL = int(os.getenv("LLM_CONFIG_CACHE_TTL", "300"))

# Deterministic seed for Autogen caching layer.
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
logger.info(f"LLM_CONFIG_DEFAULT_CACHE_SEED_SELECTED seed={_DEFAULT_CACHE_SEED} randomized={_randomize} env_override={'yes' if _env_seed else 'no'}")

# Static price map (prompt, completion) USD per 1K tokens (example values)
PRICE_MAP: Dict[str, List[float]] = {
    "o3-mini": [0.0011, 0.0044],
    "gpt-4.1-nano": [0.0001, 0.0004],
    "gpt-4o-mini": [0.00015, 0.0006],
}


# ---------------------------------------------------------------------------
# Raw Provider Config Loader
# ---------------------------------------------------------------------------
# A provider entry may include 'price': List[float] -> [prompt_per_1k, completion_per_1k]
ProviderConfig = Dict[str, Any]

async def _load_raw_config_list(force: bool = False) -> List[ProviderConfig]:
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

        config_list: List[ProviderConfig] = []

        # Attempt DB fetch
        db_doc = None
        if get_mongo_client:
            try:
                db = get_mongo_client().autogen_ai_agents  # type: ignore[attr-defined]
                db_doc = await db.LLMConfig.find_one()
            except Exception as e:  # pragma: no cover
                logger.debug(f"[LLM_CONFIG] Mongo fetch failed, will fallback: {e}")

        if db_doc and isinstance(db_doc, dict):
            # Avoid dumping raw secrets from the DB document
            def _redact_val(v: Any) -> Any:
                if not isinstance(v, str):
                    return v
                if len(v) <= 8:
                    return "***"
                return v[:4] + "***REDACTED***" + v[-4:]

            def _redact_mapping(d: Dict[str, Any]) -> Dict[str, Any]:
                sens_keys = {"api_key", "apikey", "authorization", "secret", "password", "token", "clientsecret", "accountkey"}
                out: Dict[str, Any] = {}
                for k, v in d.items():
                    if isinstance(v, dict):
                        out[k] = _redact_mapping(v)
                    elif any(s in k.lower() for s in sens_keys):
                        out[k] = _redact_val(v)
                    else:
                        out[k] = v
                return out

            try:
                logger.debug(f"[LLM_CONFIG] DB document found: {_redact_mapping(db_doc)}")
            except Exception:
                logger.debug("[LLM_CONFIG] DB document found (redaction failed to serialize)")
            # Expect a shape like: { model: 'gpt-4o-mini', price: {...}, ... }
            # Support single or list of providers.
            providers = db_doc.get("providers") or db_doc.get("models") or [db_doc]
            if not isinstance(providers, list):
                providers = [providers]
            logger.info(f"[LLM_CONFIG] Processing {len(providers)} providers from DB")
            for i, p in enumerate(providers):
                try:
                    logger.info(f"[LLM_CONFIG] Processing provider {i}: {_redact_mapping(p)}")
                except Exception:
                    logger.info(f"[LLM_CONFIG] Processing provider {i}: <redaction error>")
                # Extract model name with logging for debugging
                model_lowercase = p.get("model")
                model_capitalized = p.get("Model")
                model_name_field = p.get("name")
                env_default = os.getenv("DEFAULT_LLM_MODEL", "gpt-4o-mini")
                model_name = model_lowercase or model_capitalized or model_name_field or env_default
                logger.info(
                    f"[LLM_CONFIG] Model extraction for provider {i}: "
                    f"model(lowercase)={model_lowercase!r} Model(cap)={model_capitalized!r} "
                    f"name={model_name_field!r} env_default={env_default!r} -> selected={model_name!r}"
                )
                # First check if API key is in the DB document
                api_key = p.get("api_key") or p.get("ApiKey") or p.get("OPENAI_API_KEY")
                if not api_key:
                    # Fallback to secret/env
                    try:
                        api_key = get_secret("OpenAIApiKey") if get_secret else os.getenv("OPENAI_API_KEY", "")
                    except Exception:
                        api_key = os.getenv("OPENAI_API_KEY", "")
                entry = {"model": model_name, "api_key": api_key}
                if "price" in p:
                    entry["price"] = p["price"]
                else:
                    if model_name in PRICE_MAP:
                        entry["price"] = PRICE_MAP[model_name]
                safe_entry = {**entry, "api_key": "***REDACTED***" if entry.get("api_key") else entry.get("api_key")}
                logger.info(f"[LLM_CONFIG] Created entry {i}: {safe_entry}")
                config_list.append(entry)

        # Fallback if empty
        if not config_list:
            try:
                api_key = get_secret("OpenAIApiKey") if get_secret else os.getenv("OPENAI_API_KEY", "")
            except Exception:
                api_key = os.getenv("OPENAI_API_KEY", "")
            fallback_models: List[str] = []
            if os.getenv("OPENAI_MODEL_FALLBACK"):
                fallback_models = [m.strip() for m in os.getenv("OPENAI_MODEL_FALLBACK", "").split(",") if m.strip()]
            if not fallback_models:
                fallback_models = [os.getenv("DEFAULT_LLM_MODEL", "gpt-4o-mini")]
            for m in fallback_models:
                entry = {"model": m, "api_key": api_key}
                if m in PRICE_MAP:
                    # cast list to tuple for immutability (optional)
                    mapped = PRICE_MAP[m]
                    entry["price"] = mapped  # type: ignore[assignment]
                config_list.append(entry)

    _RAW_CONFIG_CACHE["config_list"] = config_list
    _RAW_CONFIG_CACHE["loaded_at"] = time.time()
    logger.info(f"[LLM_CONFIG] Loaded provider list (count={len(config_list)})")

    # -----------------------------
    # Change detection / invalidation
    # -----------------------------
    global _LAST_PROVIDER_SIGNATURE, _LAST_API_KEYS
    try:
        # Build deterministic signature over provider entries (model, api_key hash, price)
        sig_parts: List[str] = []
        api_keys_now: Set[str] = set()
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
                f"[LLM_CONFIG] Provider list change detected (providers_changed={provider_changed} api_keys_changed={keys_changed}); cleared built llm_config cache"
            )
        _LAST_PROVIDER_SIGNATURE = signature
        _LAST_API_KEYS = api_keys_now
    except Exception as sig_err:  # pragma: no cover
        logger.debug(f"[LLM_CONFIG] Provider signature generation failed (non-fatal): {sig_err}")
    # Debug: log each config entry
    for i, entry in enumerate(config_list):
        safe_entry = {**entry, "api_key": "***REDACTED***" if entry.get("api_key") else entry.get("api_key")}
        logger.info(f"[LLM_CONFIG] config_list[{i}]: {safe_entry}")
    return config_list


# ---------------------------------------------------------------------------
# Key construction & Cache helpers
# ---------------------------------------------------------------------------
def _build_llm_cache_key(*, response_format: Optional[Type[BaseModel]], stream: bool, extra_config: Optional[Dict[str, Any]]) -> str:
    parts = ["stream" if stream else "no-stream"]
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
    response_format: Optional[Type[BaseModel]] = None,
    stream: bool = False,
    extra_config: Optional[Dict[str, Any]] = None,
    cache: bool = True,
) -> Tuple[Optional[Any], Dict[str, Any]]:
    """Build (or retrieve from cache) an LLM runtime config.

    Returns a tuple (wrapper_placeholder, llm_config). The first element is kept for backward
    compatibility with earlier callers but is always None; the second is the dict passed to
    ConversableAgent.
    """
    cache_key = _build_llm_cache_key(
        response_format=response_format, stream=stream, extra_config=extra_config
    )
    if cache and cache_key in _LLM_CONFIG_CACHE:
        import copy
        cfg = copy.deepcopy(_LLM_CONFIG_CACHE[cache_key])
        _attach_autogen_cache(cfg)
        return None, cfg

    # Ensure base provider list loaded
    config_list = await _load_raw_config_list()

    # Determine seed origin BEFORE constructing final config for clearer logging
    seed_from_extra = None
    if extra_config and "cache_seed" in extra_config:
        try:
            seed_from_extra = int(extra_config["cache_seed"])
        except Exception:
            logger.debug(f"[LLM_CONFIG] Provided extra_config.cache_seed not coercible to int: {extra_config.get('cache_seed')!r}; falling back to default")
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

    llm_config: Dict[str, Any] = {
        "timeout": extra_config.get("timeout") if extra_config and "timeout" in extra_config else 600,
        "cache_seed": selected_seed,
        "config_list": config_list,
        "tools": [],  # Required by AG2 for tool registration
    }
    if response_format is not None:
        llm_config["response_format"] = response_format
    if stream:
        # Autogen uses stream via runtime / event layer; we'll handle streaming at the transport level
        # DO NOT add _stream flag to llm_config as it confuses AG2 config validation
        logger.debug("[LLM_CONFIG] Stream mode requested but not adding _stream flag to avoid AG2 validation issues")
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
        f"[LLM_CONFIG] Built config (rf={'yes' if response_format else 'no'}, stream={stream}, extras={bool(extra_config)}, cache_key={cache_key})"
    )
    logger.debug(f"[LLM_CONFIG] Final llm_config before return: {llm_config}")
    
    # Additional safety check - validate the config_list entries before return
    config_list = llm_config.get('config_list', [])
    logger.debug(f"[LLM_CONFIG] Validating config_list with {len(config_list)} entries")
    
    for i, entry in enumerate(config_list):
        logger.debug(f"[LLM_CONFIG] Checking entry [{i}]: {entry}")
        if not isinstance(entry, dict):
            logger.error(f"[LLM_CONFIG] VALIDATION ERROR: Entry [{i}] is not a dict: {type(entry)} {entry}")
            raise ValueError(f"Config entry [{i}] is not a dict: {type(entry)}")
        elif not entry.get('model'):
            logger.error(f"[LLM_CONFIG] VALIDATION ERROR: Entry [{i}] missing model field: {entry}")
            raise ValueError(f"Config entry [{i}] missing required model field: {entry}")
        else:
            logger.debug(f"[LLM_CONFIG] Entry [{i}] validation OK: model={entry.get('model')}")
    
    # Final check - ensure we don't have any extra config modifications happening
    logger.debug(f"[LLM_CONFIG] About to return config with config_list length: {len(llm_config.get('config_list', []))}")
    _attach_autogen_cache(llm_config)
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
    logger.info(f"[LLM_CONFIG] Caches cleared raw={raw} built={built}")


__all__ = [
    "get_llm_config",
    "PRICE_MAP",
    "clear_llm_caches",
]
