"""AG2 1.0 beta telemetry and metrics wiring for Mozaiks workflow agents.

AG2 owns agent turn, LLM-call, tool-call, and human-input telemetry through
``TelemetryMiddleware`` (OTEL spans) and ``MetricsMiddleware`` (Prometheus
counters + histograms). Mozaiks adds deterministic span attributes that tie
those spans back to the runtime run record stored in ``ChatSessions``.

Two observability planes — use both:
  TelemetryMiddleware  → OTEL spans: per-request traces, deep-link to Grafana Tempo/Jaeger.
  MetricsMiddleware    → Prometheus: aggregated counters + histograms, Grafana dashboards + alerts.

Environment variables
---------------------
AG2_OTEL_ENABLED          : Set to "true" to enable OTEL tracing (default: false).
AG2_OTEL_EXPORTER         : "console" prints spans to stdout; "otlp" ships to a
                            collector (default: console).
AG2_OTEL_SERVICE_NAME     : Service name stamped on every span (default: mozaiks-runtime).
AG2_OTEL_CAPTURE_MESSAGES : Set to "true" to include message content in spans
                            (default: false — safe for production).
OTEL_EXPORTER_OTLP_ENDPOINT : gRPC endpoint when AG2_OTEL_EXPORTER=otlp
                              (default: http://localhost:4317).
OTEL_EXPORTER_OTLP_HEADERS  : Comma-separated key=value auth headers for OTLP.
AG2_METRICS_ENABLED       : Set to "true" to enable Prometheus metrics (default: false).
                            Requires: pip install "ag2[metrics]"
AG2_METRICS_PATH          : URL path for the /metrics endpoint (default: /metrics).

Prometheus metrics exposed when AG2_METRICS_ENABLED=true:
  Counters  : ag2_agent_turns_total, ag2_llm_calls_total, ag2_llm_tokens_total,
              ag2_tool_calls_total, ag2_human_input_requests_total
  Histograms: ag2_agent_turn_duration_seconds, ag2_llm_call_duration_seconds,
              ag2_tool_duration_seconds, ag2_human_input_duration_seconds
  Labels    : agent, provider, model, outcome, error_type, token_type, tool, finish_reason
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
    """Runtime configuration for AG2 1.0 beta telemetry middleware."""

    enabled: bool = False
    capture_content: bool = False
    service_name: str = "mozaiks-runtime"
    environment: str = "development"

    @classmethod
    def from_env(cls) -> AG2TelemetryConfig:
        return cls(
            enabled=_env_bool("AG2_OTEL_ENABLED", default=False),
            capture_content=_env_bool("AG2_OTEL_CAPTURE_MESSAGES", default=False),
            service_name=os.getenv("AG2_OTEL_SERVICE_NAME", "mozaiks-runtime").strip()
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
    """Create AG2 ``TelemetryMiddleware`` wrapped as AG2 1.0 beta middleware.

    Returns ``None`` when disabled or when tracing extras are not installed.
    """

    cfg = config or AG2TelemetryConfig.from_env()
    if not cfg.enabled:
        return None

    try:
        from ag2.middleware.builtin import TelemetryMiddleware

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
    except Exception as exc:  # pragma: no cover - depends on optional AG2 extras
        logger.debug("AG2 TelemetryMiddleware unavailable: %s", exc)
        return None


def configure_otel_from_env() -> bool:
    """Configure the global OpenTelemetry TracerProvider from environment variables.

    Call once at host startup (before agents are created). Silently no-ops when
    ``AG2_OTEL_ENABLED`` is not ``true`` or when the optional opentelemetry-sdk
    package is not installed — self-hosters who do not opt in are unaffected.

    Returns True if a TracerProvider was configured, False otherwise.
    """
    if not _env_bool("AG2_OTEL_ENABLED", default=False):
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.debug("opentelemetry-sdk not installed — AG2_OTEL_ENABLED=true has no effect; install ag2[tracing]")
        return False

    service_name = os.getenv("AG2_OTEL_SERVICE_NAME", "mozaiks-runtime").strip() or "mozaiks-runtime"
    exporter_type = os.getenv("AG2_OTEL_EXPORTER", "console").strip().lower()

    resource = Resource(attributes={SERVICE_NAME: service_name})
    provider = TracerProvider(resource=resource)

    if exporter_type == "otlp":
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

            endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317").strip()
            headers_raw = os.getenv("OTEL_EXPORTER_OTLP_HEADERS", "").strip()
            headers = (
                dict(item.split("=", 1) for item in headers_raw.split(",") if "=" in item)
                if headers_raw
                else {}
            )
            exporter = OTLPSpanExporter(endpoint=endpoint, headers=headers)
            provider.add_span_processor(BatchSpanProcessor(exporter))
            logger.info("OTEL tracing: otlp endpoint=%s service=%s", endpoint, service_name)
        except ImportError:
            logger.warning(
                "AG2_OTEL_EXPORTER=otlp but opentelemetry-exporter-otlp-proto-grpc is not installed; "
                "falling back to console. Install with: pip install opentelemetry-exporter-otlp"
            )
            exporter_type = "console"

    if exporter_type == "console":
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        logger.info("OTEL tracing: console service=%s", service_name)

    trace.set_tracer_provider(provider)
    return True


_metrics_registry: Any = None
_metrics_middleware_instance: Any = None


def get_metrics_registry() -> Any:
    """Return the shared Prometheus CollectorRegistry, or None if metrics are disabled."""
    return _metrics_registry


def build_ag2_metrics_middleware() -> Any | None:
    """Return the shared AG2 ``MetricsMiddleware`` instance.

    One instance is created per process and shared across all agents in the
    same workflow — Prometheus requires a single registration per metric name
    per registry.

    Returns ``None`` when AG2_METRICS_ENABLED is not true or when ag2[metrics]
    is not installed.
    """
    global _metrics_registry, _metrics_middleware_instance

    if not _env_bool("AG2_METRICS_ENABLED", default=False):
        return None

    if _metrics_middleware_instance is not None:
        return _metrics_middleware_instance

    try:
        from ag2.middleware import MetricsMiddleware
        from prometheus_client import CollectorRegistry

        _metrics_registry = CollectorRegistry()
        _metrics_middleware_instance = MetricsMiddleware(registry=_metrics_registry)
        logger.info("AG2 MetricsMiddleware ready — scrape at %s", os.getenv("AG2_METRICS_PATH", "/metrics"))
        return _metrics_middleware_instance
    except Exception as exc:
        logger.debug("AG2 MetricsMiddleware unavailable: %s — install with: pip install 'ag2[metrics]'", exc)
        return None


def make_metrics_asgi_app() -> Any | None:
    """Return a Prometheus ASGI app exposing the metrics registry.

    Mount this on the FastAPI host at AG2_METRICS_PATH (default /metrics):

        from mozaiksai.core.observability.performance_manager import make_metrics_asgi_app
        metrics_app = make_metrics_asgi_app()
        if metrics_app:
            app.mount("/metrics", metrics_app)

    Returns None when metrics are disabled or ag2[metrics] is not installed.
    """
    registry = get_metrics_registry()
    if registry is None:
        return None
    try:
        from prometheus_client import make_asgi_app
        return make_asgi_app(registry=registry)
    except Exception as exc:
        logger.debug("prometheus_client.make_asgi_app unavailable: %s", exc)
        return None


__all__ = [
    "AG2TelemetryConfig",
    "build_ag2_span_attributes",
    "build_ag2_telemetry_middleware",
    "build_ag2_metrics_middleware",
    "make_metrics_asgi_app",
    "get_metrics_registry",
    "configure_otel_from_env",
]
