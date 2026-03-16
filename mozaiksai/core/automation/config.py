from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

from mozaiksai.core.automation.contracts import AutomationConfigBundle


def get_platform_root() -> Path:
    raw = os.getenv("MOZAIKS_PLATFORM_PATH", "").strip()
    if raw:
        return Path(raw).resolve()
    return Path(__file__).resolve().parents[3] / "platform"


def get_automations_root() -> Path:
    return get_platform_root() / "automations"


def _load_json(path: Path, *, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return dict(default)
    with open(path, "r", encoding="utf-8") as handle:
        loaded = json.load(handle)
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} must contain a top-level object")
    return loaded


@lru_cache(maxsize=1)
def load_automation_config() -> AutomationConfigBundle:
    root = get_automations_root()
    events = _load_json(root / "event_catalog.json", default={"events": []})
    routes = _load_json(root / "routes.json", default={"routes": []})
    return AutomationConfigBundle.model_validate(
        {
            "events": events.get("events", []),
            "routes": routes.get("routes", []),
        }
    )


def reload_automation_config() -> AutomationConfigBundle:
    load_automation_config.cache_clear()
    return load_automation_config()


__all__ = [
    "get_automations_root",
    "get_platform_root",
    "load_automation_config",
    "reload_automation_config",
]
