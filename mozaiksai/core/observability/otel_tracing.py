# ==============================================================================
# FILE: otel_tracing.py
# DESCRIPTION: AG2 OpenTelemetry tracing integration for MozaiksCore.
#
# Provides unified OTEL instrumentation for AG2 agents, patterns, and LLM calls.
# Supports Console (dev) and OTLP (production) exporters via environment config.
#
# Key Features:
# - Automatic instrumentation of AG2 patterns (GroupChat, AutoPattern, etc.)
# - Global LLM wrapper instrumentation for all OpenAI-compatible calls
# - Multi-tenant context propagation (app_id, user_id, chat_id)
# - Configurable export to Jaeger, Grafana Tempo, Datadog via OTLP
#
# Environment Variables:
# - AG2_OTEL_ENABLED: Enable OTEL tracing (default: "false")
# - AG2_OTEL_SERVICE_NAME: Service name for traces (default: "mozaiks-runtime")
# - AG2_OTEL_EXPORTER: Exporter type - "console", "otlp", or "both" (default: "console")
# - OTEL_EXPORTER_OTLP_ENDPOINT: OTLP endpoint URL (default: "http://localhost:4317")
# - OTEL_EXPORTER_OTLP_HEADERS: Optional headers for OTLP auth (e.g., "api-key=xxx")
# - AG2_OTEL_CAPTURE_MESSAGES: Capture full message content (default: "false" for security)
# ==============================================================================

from __future__ import annotations

import os
import logging
from contextlib import contextmanager
from typing import Any, Optional, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import TracerProvider
    from autogen import ConversableAgent

logger = logging.getLogger("core.observability.otel_tracing")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _is_otel_enabled() -> bool:
    """Check if OpenTelemetry tracing is enabled via environment."""
    value = os.getenv("AG2_OTEL_ENABLED", "false").strip().lower()
    return value in {"1", "true", "yes", "on", "enabled"}


def _get_service_name() -> str:
    """Get the service name for OTEL traces."""
    return os.getenv("AG2_OTEL_SERVICE_NAME", "mozaiks-runtime").strip()


def _get_exporter_type() -> str:
    """Get the exporter type: 'console', 'otlp', or 'both'."""
    return os.getenv("AG2_OTEL_EXPORTER", "console").strip().lower()


def _get_otlp_endpoint() -> str:
    """Get the OTLP exporter endpoint."""
    return os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317").strip()


def _get_otlp_headers() -> Optional[Dict[str, str]]:
    """Parse OTLP headers from environment (format: 'key1=val1,key2=val2')."""
    raw = os.getenv("OTEL_EXPORTER_OTLP_HEADERS", "").strip()
    if not raw:
        return None
    headers = {}
    for pair in raw.split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            headers[k.strip()] = v.strip()
    return headers if headers else None


def _capture_messages() -> bool:
    """Check if full message content capture is enabled (security consideration)."""
    value = os.getenv("AG2_OTEL_CAPTURE_MESSAGES", "false").strip().lower()
    return value in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# TracerProvider Factory
# ---------------------------------------------------------------------------

_tracer_provider: Optional["TracerProvider"] = None


def _build_tracer_provider() -> Optional["TracerProvider"]:
    """Build and configure an OpenTelemetry TracerProvider.

    Returns None if OTEL dependencies are not available.
    """
    try:
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor, BatchSpanProcessor
    except ImportError:
        logger.warning(
            "OpenTelemetry SDK not installed. Install with: pip install opentelemetry-sdk opentelemetry-exporter-otlp"
        )
        return None

    service_name = _get_service_name()
    resource = Resource.create(attributes={"service.name": service_name})
    provider = TracerProvider(resource=resource)

    exporter_type = _get_exporter_type()
    exporters_added = 0

    # Console exporter (for development/debugging)
    if exporter_type in {"console", "both"}:
        try:
            from opentelemetry.sdk.trace.export import ConsoleSpanExporter
            provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
            exporters_added += 1
            logger.info("OTEL: Console span exporter added")
        except Exception as e:
            logger.warning(f"Failed to add console exporter: {e}")

    # OTLP exporter (for production - Jaeger, Grafana Tempo, Datadog, etc.)
    if exporter_type in {"otlp", "both"}:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

            endpoint = _get_otlp_endpoint()
            headers = _get_otlp_headers()

            exporter_kwargs: Dict[str, Any] = {"endpoint": endpoint}
            if headers:
                exporter_kwargs["headers"] = headers

            otlp_exporter = OTLPSpanExporter(**exporter_kwargs)
            provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
            exporters_added += 1
            logger.info(f"OTEL: OTLP span exporter added (endpoint={endpoint})")
        except ImportError:
            logger.warning(
                "OTLP exporter not installed. Install with: pip install opentelemetry-exporter-otlp"
            )
        except Exception as e:
            logger.warning(f"Failed to add OTLP exporter: {e}")

    if exporters_added == 0:
        logger.warning("No OTEL exporters configured. Traces will not be exported.")
        return None

    return provider


def get_tracer_provider() -> Optional["TracerProvider"]:
    """Get or create the global TracerProvider singleton."""
    global _tracer_provider

    if not _is_otel_enabled():
        return None

    if _tracer_provider is None:
        _tracer_provider = _build_tracer_provider()

    return _tracer_provider


# ---------------------------------------------------------------------------
# AG2 Instrumentation Functions
# ---------------------------------------------------------------------------

_llm_instrumented = False
_instrumented_patterns: set = set()


def instrument_llm_globally() -> bool:
    """Instrument all AG2 LLM wrapper calls globally.

    This should be called once at runtime startup to capture all LLM API calls.

    Returns:
        True if instrumentation was successful, False otherwise.
    """
    global _llm_instrumented

    if _llm_instrumented:
        logger.debug("LLM wrapper already instrumented")
        return True

    if not _is_otel_enabled():
        logger.debug("OTEL disabled; skipping LLM instrumentation")
        return False

    provider = get_tracer_provider()
    if provider is None:
        return False

    try:
        from autogen.otel import instrument_llm_wrapper

        capture = _capture_messages()
        instrument_llm_wrapper(tracer_provider=provider, capture_messages=capture)

        _llm_instrumented = True
        logger.info(f"OTEL: LLM wrapper instrumented (capture_messages={capture})")
        return True

    except ImportError:
        logger.warning(
            "AG2 tracing module not available. Install with: pip install 'ag2[tracing]'"
        )
        return False
    except Exception as e:
        logger.warning(f"Failed to instrument LLM wrapper: {e}")
        return False


def instrument_agent(agent: "ConversableAgent") -> bool:
    """Instrument a single AG2 agent for tracing.

    Captures conversation turns, tool execution, and code execution spans.

    Args:
        agent: The AG2 ConversableAgent to instrument.

    Returns:
        True if instrumentation was successful, False otherwise.
    """
    if not _is_otel_enabled():
        return False

    provider = get_tracer_provider()
    if provider is None:
        return False

    try:
        from autogen.otel import instrument_agent as ag2_instrument_agent

        ag2_instrument_agent(agent, tracer_provider=provider)
        logger.debug(f"OTEL: Agent '{agent.name}' instrumented")
        return True

    except ImportError:
        logger.debug("AG2 tracing module not available for agent instrumentation")
        return False
    except Exception as e:
        logger.warning(f"Failed to instrument agent '{getattr(agent, 'name', 'unknown')}': {e}")
        return False


def instrument_pattern(pattern: Any, pattern_id: Optional[str] = None) -> bool:
    """Instrument an AG2 Pattern (GroupChat) for tracing.

    This is the recommended method for group chats - it automatically instruments
    all agents in the pattern plus speaker selection.

    Args:
        pattern: The AG2 Pattern instance (AutoPattern, DefaultPattern, etc.)
        pattern_id: Optional identifier for deduplication.

    Returns:
        True if instrumentation was successful, False otherwise.
    """
    global _instrumented_patterns

    if not _is_otel_enabled():
        return False

    # Deduplicate by pattern id
    pid = pattern_id or id(pattern)
    if pid in _instrumented_patterns:
        logger.debug(f"Pattern {pid} already instrumented")
        return True

    provider = get_tracer_provider()
    if provider is None:
        return False

    try:
        from autogen.otel import instrument_pattern as ag2_instrument_pattern

        ag2_instrument_pattern(pattern, tracer_provider=provider)
        _instrumented_patterns.add(pid)
        logger.info(f"OTEL: Pattern instrumented (id={pid})")
        return True

    except ImportError:
        logger.debug("AG2 tracing module not available for pattern instrumentation")
        return False
    except Exception as e:
        logger.warning(f"Failed to instrument pattern: {e}")
        return False


# ---------------------------------------------------------------------------
# Context Managers for Tracing Spans
# ---------------------------------------------------------------------------

@contextmanager
def traced_workflow(
    workflow_name: str,
    chat_id: str,
    app_id: Optional[str] = None,
    user_id: Optional[str] = None,
):
    """Context manager that wraps a workflow execution in a parent span.

    This adds MozaiksCore-specific attributes to the trace for multi-tenant
    correlation and filtering.

    Usage:
        with traced_workflow("my_workflow", chat_id, app_id, user_id):
            # Run AG2 pattern or agents here
            pass

    Args:
        workflow_name: Name of the workflow being executed.
        chat_id: Unique identifier for the chat session.
        app_id: Optional application identifier (multi-tenant).
        user_id: Optional user identifier.
    """
    if not _is_otel_enabled():
        yield
        return

    provider = get_tracer_provider()
    if provider is None:
        yield
        return

    try:
        tracer = provider.get_tracer("mozaiks.runtime")

        attributes = {
            "mozaiks.workflow_name": workflow_name,
            "mozaiks.chat_id": chat_id,
        }
        if app_id:
            attributes["mozaiks.app_id"] = app_id
        if user_id:
            attributes["mozaiks.user_id"] = user_id

        with tracer.start_as_current_span(
            name=f"workflow:{workflow_name}",
            attributes=attributes,
        ) as span:
            try:
                yield span
            except Exception as e:
                span.set_status(
                    status=_make_error_status(str(e))
                )
                span.record_exception(e)
                raise

    except Exception as e:
        logger.debug(f"Tracing context failed: {e}")
        yield


def _make_error_status(message: str):
    """Create an error status for a span."""
    try:
        from opentelemetry.trace import StatusCode, Status
        return Status(StatusCode.ERROR, message)
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# Initialization Helper
# ---------------------------------------------------------------------------

def initialize_otel_tracing() -> bool:
    """Initialize OpenTelemetry tracing for MozaiksCore runtime.

    Call this once at application startup (e.g., in shared_app.py or similar).

    This function:
    1. Checks if OTEL is enabled
    2. Builds the TracerProvider with configured exporters
    3. Instruments the global LLM wrapper

    Returns:
        True if OTEL tracing is active, False otherwise.
    """
    if not _is_otel_enabled():
        logger.info("OTEL tracing disabled (set AG2_OTEL_ENABLED=true to enable)")
        return False

    provider = get_tracer_provider()
    if provider is None:
        logger.warning("Failed to initialize OTEL TracerProvider")
        return False

    # Set as global provider
    try:
        from opentelemetry import trace
        trace.set_tracer_provider(provider)
        logger.info("OTEL: Global TracerProvider set")
    except Exception as e:
        logger.warning(f"Failed to set global TracerProvider: {e}")

    # Instrument LLM wrapper globally
    instrument_llm_globally()

    logger.info(
        f"OTEL tracing initialized (service={_get_service_name()}, "
        f"exporter={_get_exporter_type()})"
    )
    return True


def shutdown_otel_tracing() -> None:
    """Gracefully shutdown OTEL tracing, flushing any pending spans."""
    global _tracer_provider

    if _tracer_provider is None:
        return

    try:
        _tracer_provider.shutdown()
        logger.info("OTEL tracing shutdown complete")
    except Exception as e:
        logger.warning(f"Error during OTEL shutdown: {e}")
    finally:
        _tracer_provider = None


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

__all__ = [
    # Configuration
    "get_tracer_provider",
    # Instrumentation
    "instrument_llm_globally",
    "instrument_agent",
    "instrument_pattern",
    # Context managers
    "traced_workflow",
    # Lifecycle
    "initialize_otel_tracing",
    "shutdown_otel_tracing",
]
