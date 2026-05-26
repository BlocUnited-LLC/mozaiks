from __future__ import annotations

import os
import shutil
from typing import Any, Callable, Dict, List, Optional, Tuple


APP_VALIDATION_STRATEGIES: tuple[str, str, str] = ("e2b", "local", "skip")

_STRATEGY_LABELS = {
    "e2b": "E2B Sandbox",
    "local": "Local Host",
    "skip": "Skip Validation",
}

_STRATEGY_DESCRIPTIONS = {
    "e2b": "Run pre-deploy build validation in the hosted sandbox and expose a preview URL when available. Not a production hosting runtime.",
    "local": "Run build validation on the current machine without requiring sandbox credentials.",
    "skip": "Do not execute build validation for this run. Integration checks still gate export.",
}


def normalize_app_validation_strategy(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    value = str(raw).strip().lower()
    if not value:
        return None
    if value not in APP_VALIDATION_STRATEGIES:
        return None
    return value


def local_app_validation_available() -> bool:
    return shutil.which("npm") is not None


def default_app_validation_strategy(
    *,
    env: Optional[Dict[str, str]] = None,
    local_available: Optional[bool] = None,
) -> Tuple[str, str]:
    env_map = env or os.environ
    if str(env_map.get("E2B_API_KEY", "")).strip():
        return "e2b", "resolved from E2B availability"
    if local_available is None:
        local_available = local_app_validation_available()
    if local_available:
        return "local", "resolved from local npm availability"
    return "skip", "resolved because neither E2B nor local npm is available"


def resolve_app_validation_strategy(
    *,
    requested: Any = None,
    context_value: Any = None,
    env: Optional[Dict[str, str]] = None,
    local_available: Optional[bool] = None,
) -> Tuple[str, str]:
    env_map = env or os.environ
    env_strategy = normalize_app_validation_strategy(env_map.get("MOZAIKS_APP_VALIDATION_STRATEGY"))

    for candidate, source in (
        (requested, "tool argument"),
        (context_value, "context variable"),
        (env_strategy, "environment"),
    ):
        if candidate is None:
            continue
        normalized = normalize_app_validation_strategy(candidate)
        if normalized is None:
            raise ValueError(
                f"Unsupported app validation strategy '{candidate}'. Allowed values: e2b | local | skip."
            )
        return normalized, f"resolved from {source}"

    return default_app_validation_strategy(env=env_map, local_available=local_available)


def build_app_validation_strategy_summary(
    *,
    env: Optional[Dict[str, str]] = None,
    local_available: Optional[bool] = None,
) -> Dict[str, Any]:
    default_value, default_reason = default_app_validation_strategy(
        env=env,
        local_available=local_available,
    )
    options: List[Dict[str, str]] = []
    for value in APP_VALIDATION_STRATEGIES:
        options.append(
            {
                "value": value,
                "label": _STRATEGY_LABELS[value],
                "description": _STRATEGY_DESCRIPTIONS[value],
            }
        )
    return {
        "allowed_values": list(APP_VALIDATION_STRATEGIES),
        "default_value": default_value,
        "default_reason": default_reason,
        "options": options,
    }


__all__ = [
    "APP_VALIDATION_STRATEGIES",
    "build_app_validation_strategy_summary",
    "default_app_validation_strategy",
    "local_app_validation_available",
    "normalize_app_validation_strategy",
    "resolve_app_validation_strategy",
]
