# ==============================================================================
# FILE: mozaikscore/core/config_loader.py
# DESCRIPTION: Declarative config file loader with TTL caching.
#              Reads JSON files from the MOZAIKS_CONFIGS_PATH directory.
# ==============================================================================
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("mozaikscore.config_loader")

_CONFIG_CACHE: dict[str, tuple[Any, float]] = {}
_CACHE_TTL = 300  # 5 minutes


def get_config_path() -> Path:
    """Resolve the config directory from env or default to platform/config."""
    raw = os.getenv("MOZAIKS_CONFIGS_PATH", "").strip()
    if raw:
        return Path(raw).resolve()
    return Path(__file__).resolve().parent.parent.parent / "platform" / "config"


def _load_json(name: str) -> Optional[dict]:
    """Load a JSON config file with TTL caching."""
    now = time.time()
    if name in _CONFIG_CACHE:
        data, ts = _CONFIG_CACHE[name]
        if now - ts < _CACHE_TTL:
            return data

    path = get_config_path() / name
    if not path.exists():
        logger.warning("Config file not found: %s", path)
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        _CONFIG_CACHE[name] = (data, now)
        return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Error loading config %s: %s", name, exc)
        return None


def reload_configs():
    """Bust the cache so the next read hits disk."""
    _CONFIG_CACHE.clear()


# ---------------------------------------------------------------------------
# Typed accessors
# ---------------------------------------------------------------------------
def get_module_registry() -> dict:
    return _load_json("module_registry.json") or {"modules": []}


def get_navigation_config() -> dict:
    return _load_json("navigation_config.json") or {}


def get_theme_config() -> dict:
    return _load_json("theme_config.json") or {}


def get_settings_config() -> dict:
    return _load_json("settings_config.json") or {"profile_sections": []}


def get_notifications_config() -> dict:
    return _load_json("notifications_config.json") or {}


def get_subscription_config() -> dict:
    return _load_json("subscription_config.json") or {}
