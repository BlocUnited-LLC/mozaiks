"""AG2 beta telemetry wiring for Mozaiks workflow agents.

AG2 owns agent turn, LLM-call, tool-call, and human-input telemetry through
``TelemetryMiddleware``. Mozaiks adds only deterministic span attributes that
tie those spans back to the runtime run record stored in ``ChatSessions``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from logs.logging_config import get_workflow_logger

logger = get_workflow_logger("observability.telemetry")


def _env_bool(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AG2TelemetryConfig:
    """Runtime configuration for AG2 beta telemetry middleware."""

    enabled: bool = True
    capture_content: bool = False
    service_name: str = "mozaiks-runtime"
    environment: str = "development"

    @classmethod
    def from_env(cls) -> AG2TelemetryConfig:
        return cls(
            enabled=_env_bool("MOZAIKS_AG2_TELEMETRY_ENABLED", default=True),
            capture_content=_env_bool("MOZAIKS_AG2_TELEMETRY_CAPTURE_CONTENT", default=False),
            service_name=os.getenv("MOZAIKS_OTEL_SERVICE_NAME", "mozaiks-runtime").strip()
            or "mozaiks-runtime",
            environment=os.getenv("ENVIRONMENT", "development").strip() or "development",
        )


def _context_data(context_variables: Any) -> Mapping[str, Any]:
    if context_variables is None:
        return {}
    if hasattr(context_variables, "data") and isinstance(context_variables.data, dict):
        return context_variables.data
    if isinstance(context_variables, Mapping):
        return context_variables
    return {}


def _string_attr(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def build_ag2_span_attributes(
    *,
    agent_name: str,
    workflow_name: str,
    context_variables: Any,
    config: AG2TelemetryConfig | None = None,
) -> dict[str, str]:
    """Return stable span attributes for AG2 telemetry middleware."""

    cfg = config or AG2TelemetryConfig.from_env()
    data = _context_data(context_variables)
    candidates = {
        "service.name": cfg.service_name,
        "deployment.environment": cfg.environment,
        "mozaiks.workflow.name": workflow_name,
        "mozaiks.agent.name": agent_name,
        "mozaiks.app.id": data.get("app_id"),
        "mozaiks.chat.id": data.get("chat_id"),
        "mozaiks.user.id": data.get("user_id"),
        "mozaiks.workflow.run.id": data.get("chat_id") or data.get("run_id"),
        "mozaiks.session_router.session_id": data.get("session_router_session_id"),
        "mozaiks.journey.instance_id": data.get("journey_instance_id"),
    }
    return {
        key: value
        for key, raw_value in candidates.items()
        if (value := _string_attr(raw_value)) is not None
    }


def build_ag2_telemetry_middleware(
    *,
    agent_name: str,
    workflow_name: str,
    context_variables: Any,
    provider_name: str | None = None,
    model_name: str | None = None,
    config: AG2TelemetryConfig | None = None,
) -> Any | None:
    """Create AG2 ``TelemetryMiddleware`` wrapped as AG2 beta middleware.

    Returns ``None`` when disabled or when tracing extras are not installed.
    """

    cfg = config or AG2TelemetryConfig.from_env()
    if not cfg.enabled:
        return None

    try:
        from autogen.beta.middleware.builtin import TelemetryMiddleware
    except Exception as exc:  # pragma: no cover - depends on optional AG2 extras
        logger.debug("AG2 TelemetryMiddleware unavailable: %s", exc)
        return None

    return TelemetryMiddleware(
        capture_content=cfg.capture_content,
        agent_name=agent_name,
        provider_name=provider_name,
        model_name=model_name,
        span_attributes=build_ag2_span_attributes(
            agent_name=agent_name,
            workflow_name=workflow_name,
            context_variables=context_variables,
            config=cfg,
        ),
    )


__all__ = [
    "AG2TelemetryConfig",
    "build_ag2_span_attributes",
    "build_ag2_telemetry_middleware",
]
