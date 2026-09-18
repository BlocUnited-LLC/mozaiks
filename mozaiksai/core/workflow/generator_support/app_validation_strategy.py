from __future__ import annotations

import os
import shutil
from typing import Any

APP_VALIDATION_STRATEGIES: tuple[str, str, str, str] = ("e2b", "docker", "local", "skip")

_STRATEGY_LABELS = {
    "e2b": "E2B Sandbox",
    "docker": "Docker Sandbox",
    "local": "Local Host",
    "skip": "Skip Validation",
}

_STRATEGY_DESCRIPTIONS = {
    "e2b": "Run disposable pre-deploy validation in E2B. Interactive previews use separate Studio sessions; this is not production hosting.",
    "docker": "Run disposable build validation in Docker. Interactive previews use separate Studio sessions. Requires a running Docker daemon.",
    "local": "Run build validation on the current machine without requiring sandbox credentials.",
    "skip": "Do not execute build validation for this run. Integration checks still gate export.",
}


def normalize_app_validation_strategy(raw: Any) -> str | None:
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


def docker_app_validation_available() -> bool:
    """Return True if the Docker CLI is installed and the daemon is reachable."""
    from mozaiksai.core.adapters.docker_sandbox import docker_available
    return docker_available()


def default_app_validation_strategy(
    *,
    env: dict[str, str] | None = None,
    local_available: bool | None = None,
    docker_available: bool | None = None,
) -> tuple[str, str]:
    # A credential only makes the hosted provider available. It must never
    # silently turn a local build into a billable hosted validation run.
    if docker_available is None:
        docker_available = docker_app_validation_available()
    if docker_available:
        return "docker", "resolved from Docker daemon availability"
    if local_available is None:
        local_available = local_app_validation_available()
    if local_available:
        return "local", "resolved from local npm availability"
    return "skip", "resolved because no sandbox or local npm is available"


def resolve_app_validation_strategy(
    *,
    requested: Any = None,
    context_value: Any = None,
    env: dict[str, str] | None = None,
    local_available: bool | None = None,
    docker_available: bool | None = None,
) -> tuple[str, str]:
    env_map = os.environ if env is None else env
    env_strategy = str(env_map.get("MOZAIKS_APP_VALIDATION_STRATEGY", "")).strip() or None

    for candidate, source in (
        (env_strategy, "environment"),
        (requested, "tool argument"),
        (context_value, "context variable"),
    ):
        if candidate is None:
            continue
        normalized = normalize_app_validation_strategy(candidate)
        if normalized is None:
            raise ValueError(
                f"Unsupported app validation strategy '{candidate}'. "
                f"Allowed values: e2b | docker | local | skip."
            )
        return normalized, f"resolved from {source}"

    return default_app_validation_strategy(
        env=env_map,  # type: ignore[arg-type]
        local_available=local_available,
        docker_available=docker_available,
    )


def build_app_validation_strategy_summary(
    *,
    env: dict[str, str] | None = None,
    local_available: bool | None = None,
    docker_available: bool | None = None,
) -> dict[str, Any]:
    default_value, default_reason = resolve_app_validation_strategy(
        env=env,
        local_available=local_available,
        docker_available=docker_available,
    )
    options: list[dict[str, str]] = []
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
    "docker_app_validation_available",
    "local_app_validation_available",
    "normalize_app_validation_strategy",
    "resolve_app_validation_strategy",
]
