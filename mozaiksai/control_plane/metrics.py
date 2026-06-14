"""Structured observability hooks for the factory build control plane.

Provides lightweight, zero-dependency instrumentation that emits structured
log records at control-plane stage boundaries. These records can be forwarded
to any log aggregator (Datadog, CloudWatch, Loki, etc.) for dashboards and
alerting.

Hooks:
  ControlPlaneBuildTimer  — context manager; logs stage start/end/failure
                            with wall-clock duration in milliseconds.
  log_build_outcome       — single call to log the terminal success/failure
                            of a full build or refinement request.
  check_token_usage       — logs a WARNING when a stage exceeds the token
                            threshold; safe to call with None counts.

Environment variables:
  CONTROL_PLANE_TOKEN_ANOMALY_THRESHOLD — integer token count above which a
  warning is emitted. Defaults to 50_000. Set to 0 to disable.

Log record format (structured ``extra`` fields):
  cp_stage        — control-plane stage name (e.g. "route_refinement")
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
from contextlib import contextmanager
from typing import Any, Generator

import logging

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
    """Context manager that times a control-plane stage and logs the result.

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


def log_build_outcome(
    *,
    outcome: str,
    request_id: str | None = None,
    app_id: str | None = None,
    change_class: str | None = None,
    workflow_sequence: str | None = None,
    duration_ms: int | None = None,
    error: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Log the terminal outcome of a full build or refinement request.

    Call once at the top of the control-plane flow after the final decision
    or failure is known.

    Args:
        outcome:          "ok", "error", or "skipped"
        request_id:       Refinement request ID.
        app_id:           App being built.
        change_class:     Classified change class (e.g. "patch", "feature").
        workflow_sequence: Resolved workflow sequence ID.
        duration_ms:      Total elapsed time in milliseconds.
        error:            Error description when outcome is "error".
        extra:            Additional structured fields to merge.
    """
    fields: dict[str, Any] = {
        "cp_outcome": outcome,
        "cp_request_id": request_id,
        "cp_app_id": app_id,
        "cp_change_class": change_class,
        "cp_workflow_sequence": workflow_sequence,
        "cp_duration_ms": duration_ms,
        "cp_error": error,
        **(extra or {}),
    }
    if outcome == "ok":
        logger.info(
            "cp_build_outcome: request=%s app=%s class=%s seq=%s %dms",
            request_id,
            app_id,
            change_class,
            workflow_sequence,
            duration_ms or 0,
            extra=fields,
        )
    elif outcome == "error":
        logger.warning(
            "cp_build_outcome_error: request=%s app=%s — %s",
            request_id,
            app_id,
            error,
            extra=fields,
        )
    else:
        logger.debug("cp_build_outcome: %s", outcome, extra=fields)


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
        stage:       Control-plane stage that consumed the tokens.
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
    "log_build_outcome",
]
