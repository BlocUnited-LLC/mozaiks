"""Structured observability hooks for the factory Refinement Engine.

Provides lightweight, zero-dependency instrumentation that emits structured
log records at refinement stage boundaries. These records can be forwarded
to any log aggregator (Datadog, CloudWatch, Loki, etc.) for dashboards and
alerting.

Hooks:
  ControlPlaneBuildTimer  — context manager; logs stage start/end/failure
                            with wall-clock duration in milliseconds.
  check_token_usage       — logs a WARNING when a stage exceeds the token
                            threshold; safe to call with None counts.

Environment variables:
  CONTROL_PLANE_TOKEN_ANOMALY_THRESHOLD — integer token count above which a
  warning is emitted. Defaults to 50_000. Set to 0 to disable.

Log record format (structured ``extra`` fields):
  cp_stage        — refinement stage name (e.g. "route_refinement")
  cp_request_id   — refinement request ID (when available)
  cp_app_id       — app being built (when available)
  cp_duration_ms  — wall-clock milliseconds for stage (on end/failure)
  cp_outcome      — "ok" | "error" | "timeout"
  cp_error        — error message on failure
  cp_token_count  — token count (for anomaly records)
  cp_threshold    — configured anomaly threshold (for anomaly records)
"""
from __future__ import annotations

import logging
import os
import time
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger("mozaiksai.control_plane.metrics")

_DEFAULT_TOKEN_ANOMALY_THRESHOLD = 50_000
_TOKEN_THRESHOLD_ENV = "CONTROL_PLANE_TOKEN_ANOMALY_THRESHOLD"


def _token_anomaly_threshold() -> int:
    """Read the configured token anomaly threshold from the environment."""
    raw = os.getenv(_TOKEN_THRESHOLD_ENV, "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return _DEFAULT_TOKEN_ANOMALY_THRESHOLD


@contextmanager
def ControlPlaneBuildTimer(
    stage: str,
    *,
    request_id: str | None = None,
    app_id: str | None = None,
) -> Generator[None, None, None]:
    """Context manager that times a refinement stage and logs the result.

    Logs ``cp_stage_start`` at entry, ``cp_stage_end`` on clean exit,
    and ``cp_stage_error`` if an exception is raised (the exception is
    always re-raised).

    Usage:
        with ControlPlaneBuildTimer("route_refinement", request_id=req.request_id):
            decision = await self._refinement_resolver.route(req)

    Structured log fields:
        cp_stage, cp_request_id, cp_app_id, cp_duration_ms, cp_outcome, cp_error
    """
    base: dict[str, Any] = {
        "cp_stage": stage,
        "cp_request_id": request_id,
        "cp_app_id": app_id,
    }
    logger.debug("cp_stage_start", extra=base)
    t0 = time.monotonic()
    try:
        yield
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.warning(
            "cp_stage_error: %s failed after %dms — %s",
            stage,
            elapsed_ms,
            exc,
            extra={**base, "cp_duration_ms": elapsed_ms, "cp_outcome": "error", "cp_error": str(exc)},
        )
        raise
    else:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.info(
            "cp_stage_end: %s completed in %dms",
            stage,
            elapsed_ms,
            extra={**base, "cp_duration_ms": elapsed_ms, "cp_outcome": "ok"},
        )


def check_token_usage(
    *,
    stage: str,
    token_count: int | None,
    request_id: str | None = None,
    app_id: str | None = None,
    threshold: int | None = None,
) -> None:
    """Emit a WARNING when token_count exceeds the configured threshold.

    Safe to call with None values — no-ops silently when token_count is None.

    Args:
        stage:       Refinement stage that consumed the tokens.
        token_count: Total tokens used. Skipped when None.
        request_id:  Refinement request ID.
        app_id:      App being built.
        threshold:   Override the env-configured threshold for this call.
    """
    if token_count is None:
        return
    effective_threshold = threshold if threshold is not None else _token_anomaly_threshold()
    if effective_threshold <= 0:
        return  # Anomaly detection disabled
    if token_count <= effective_threshold:
        return
    logger.warning(
        "cp_token_anomaly: stage=%s token_count=%d exceeds threshold=%d request=%s app=%s",
        stage,
        token_count,
        effective_threshold,
        request_id,
        app_id,
        extra={
            "cp_stage": stage,
            "cp_token_count": token_count,
            "cp_threshold": effective_threshold,
            "cp_request_id": request_id,
            "cp_app_id": app_id,
            "cp_outcome": "anomaly",
        },
    )


__all__ = [
    "ControlPlaneBuildTimer",
    "check_token_usage",
]
