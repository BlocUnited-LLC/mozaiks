# ==============================================================================
# FILE: mozaiksai/core/metrics/prometheus_exporter.py
# DESCRIPTION: Prometheus-compatible /metrics endpoint.
#              Exports runtime counters, workflow activity, token usage, and
#              error rates in Prometheus text exposition format.
#              No external dependency required — format is hand-built to avoid
#              adding prometheus_client as a mandatory dependency.
#
# Optional dependency: install prometheus-client for richer metric types.
#   pip install "mozaiksai[monitoring]"
#
# Configuration (env vars):
#   PROMETHEUS_METRICS_ENABLED   — set to "false" to disable (default: true)
#   PROMETHEUS_METRICS_TOKEN     — optional Bearer token to protect /metrics
# ==============================================================================
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from threading import Lock

from logs.logging_config import get_core_logger

logger = get_core_logger("prometheus_exporter")

_ENABLED = os.getenv("PROMETHEUS_METRICS_ENABLED", "true").lower() not in ("false", "0", "no")
_METRICS_TOKEN = os.getenv("PROMETHEUS_METRICS_TOKEN", "").strip()

# ---------------------------------------------------------------------------
# In-process metric store
# ---------------------------------------------------------------------------

@dataclass
class _Counter:
    name: str
    help: str
    labels: list[str] = field(default_factory=list)
    _values: dict[tuple, float] = field(default_factory=dict, init=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def inc(self, amount: float = 1.0, **label_values: str) -> None:
        key = tuple(label_values.get(lbl, "") for lbl in self.labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def render(self) -> str:
        lines = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} counter"]
        with self._lock:
            for key, val in self._values.items():
                label_str = _format_labels(self.labels, key)
                lines.append(f"{self.name}{label_str} {val}")
        return "\n".join(lines)


@dataclass
class _Gauge:
    name: str
    help: str
    labels: list[str] = field(default_factory=list)
    _values: dict[tuple, float] = field(default_factory=dict, init=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def set(self, value: float, **label_values: str) -> None:
        key = tuple(label_values.get(lbl, "") for lbl in self.labels)
        with self._lock:
            self._values[key] = value

    def inc(self, amount: float = 1.0, **label_values: str) -> None:
        key = tuple(label_values.get(lbl, "") for lbl in self.labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def dec(self, amount: float = 1.0, **label_values: str) -> None:
        key = tuple(label_values.get(lbl, "") for lbl in self.labels)
        with self._lock:
            self._values[key] = max(0.0, self._values.get(key, 0.0) - amount)

    def render(self) -> str:
        lines = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} gauge"]
        with self._lock:
            for key, val in self._values.items():
                label_str = _format_labels(self.labels, key)
                lines.append(f"{self.name}{label_str} {val}")
        return "\n".join(lines)


def _format_labels(names: list[str], values: tuple) -> str:
    if not names:
        return ""
    pairs = ",".join(f'{n}="{v}"' for n, v in zip(names, values, strict=False))
    return "{" + pairs + "}"


# ---------------------------------------------------------------------------
# Canonical runtime metrics
# ---------------------------------------------------------------------------

class RuntimeMetrics:
    """Process-wide Prometheus metric store for the Mozaiks runtime."""

    def __init__(self) -> None:
        # Workflow counters
        self.workflows_started = _Counter(
            "mozaiks_workflows_started_total",
            "Total number of workflow runs started",
            labels=["workflow_name"],
        )
        self.workflows_completed = _Counter(
            "mozaiks_workflows_completed_total",
            "Total number of workflow runs completed successfully",
            labels=["workflow_name"],
        )
        self.workflows_failed = _Counter(
            "mozaiks_workflows_failed_total",
            "Total number of workflow runs that failed",
            labels=["workflow_name"],
        )
        self.active_workflows = _Gauge(
            "mozaiks_active_workflows",
            "Number of workflow runs currently executing",
        )

        # Module action counters
        self.module_actions_total = _Counter(
            "mozaiks_module_actions_total",
            "Total module action dispatches",
            labels=["module_id", "action_id", "outcome"],
        )
        self.module_action_errors = _Counter(
            "mozaiks_module_action_errors_total",
            "Total module action errors",
            labels=["module_id", "action_id"],
        )

        # Token usage
        self.tokens_input = _Counter(
            "mozaiks_tokens_input_total",
            "Total input tokens consumed across all workflows",
            labels=["workflow_name"],
        )
        self.tokens_output = _Counter(
            "mozaiks_tokens_output_total",
            "Total output tokens generated across all workflows",
            labels=["workflow_name"],
        )

        # Auth failures
        self.auth_failures = _Counter(
            "mozaiks_auth_failures_total",
            "Total authentication failures",
            labels=["provider"],
        )

        # Circuit breaker
        self.circuit_breaker_opens = _Counter(
            "mozaiks_circuit_breaker_opens_total",
            "Total number of times a circuit breaker opened",
            labels=["circuit_name"],
        )
        self.circuit_breaker_rejections = _Counter(
            "mozaiks_circuit_breaker_rejections_total",
            "Total requests rejected by an open circuit",
            labels=["circuit_name"],
        )

        # HTTP request counters
        self.http_requests_total = _Counter(
            "mozaiks_http_requests_total",
            "Total HTTP requests handled",
            labels=["method", "path", "status"],
        )

        # Process uptime
        self._start_time = time.time()
        self.uptime_seconds = _Gauge(
            "mozaiks_uptime_seconds",
            "Seconds since the runtime process started",
        )

        self._all: list[_Counter | _Gauge] = [
            self.workflows_started,
            self.workflows_completed,
            self.workflows_failed,
            self.active_workflows,
            self.module_actions_total,
            self.module_action_errors,
            self.tokens_input,
            self.tokens_output,
            self.auth_failures,
            self.circuit_breaker_opens,
            self.circuit_breaker_rejections,
            self.http_requests_total,
            self.uptime_seconds,
        ]

    def render_text(self) -> str:
        """Render all metrics in Prometheus text exposition format."""
        self.uptime_seconds.set(time.time() - self._start_time)
        return "\n\n".join(m.render() for m in self._all) + "\n"


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_runtime_metrics: RuntimeMetrics | None = None


def get_runtime_metrics() -> RuntimeMetrics:
    """Return the process-wide RuntimeMetrics singleton."""
    global _runtime_metrics
    if _runtime_metrics is None:
        _runtime_metrics = RuntimeMetrics()
    return _runtime_metrics


# ---------------------------------------------------------------------------
# FastAPI route factory
# ---------------------------------------------------------------------------

def build_metrics_router():
    """Return a FastAPI APIRouter that exposes GET /metrics.

    Register in the runtime host:
        from mozaiksai.core.metrics.prometheus_exporter import build_metrics_router
        app.include_router(build_metrics_router())
    """
    if not _ENABLED:
        return None

    from fastapi import APIRouter, HTTPException, Request
    from fastapi.responses import PlainTextResponse

    router = APIRouter()

    @router.get("/metrics", include_in_schema=False)
    async def metrics_endpoint(request: Request) -> PlainTextResponse:
        # Optional bearer token protection
        if _METRICS_TOKEN:
            auth = request.headers.get("Authorization", "")
            if not auth.startswith("Bearer ") or auth[7:] != _METRICS_TOKEN:
                raise HTTPException(status_code=401, detail="Unauthorized")

        text = get_runtime_metrics().render_text()
        return PlainTextResponse(content=text, media_type="text/plain; version=0.0.4; charset=utf-8")

    return router


__all__ = [
    "RuntimeMetrics",
    "build_metrics_router",
    "get_runtime_metrics",
]
