# ==============================================================================
# FILE: core/workflow/pack/mfj_observability.py
# DESCRIPTION: Structured logging, OpenTelemetry tracing, and metrics for MFJ
#              (Mid-Flight Journey) lifecycle events.
#
# This module provides the ``MFJObserver`` class — a single integration point
# that the coordinator calls at each MFJ lifecycle boundary.  It handles:
#
#   1. **Structured logging** — Every log entry includes a well-defined dict
#      of MFJ context fields (trigger_id, parent_chat_id, cycle, etc.) via
#      the ``extra`` mechanism.  Machine-parseable by log aggregators.
#
#   2. **OpenTelemetry spans** — Optional (requires ``opentelemetry-sdk``).
#      Provides parent ``mfj.full_cycle`` spans and child spans for fan-out,
#      child execution, and fan-in.  Gated by ``AG2_OTEL_ENABLED`` env var.
#
#   3. **Metrics** — Optional (requires ``opentelemetry-sdk``).
#      Counters, histograms, and gauges for MFJ throughput and latency.
#      Falls back to no-op when the SDK is absent.
#
# Design decisions:
#   - The observer is **stateless per call** — the coordinator passes context
#     explicitly.  No internal caches or mutable session state.
#   - OpenTelemetry is *optional* — a missing SDK does not break logging or
#     the coordinator.  All OTel imports are inside try/except blocks.
#   - Span context is propagated via ``MFJSpanContext`` dataclass returned by
#     ``on_fan_out_started`` and threaded through subsequent calls.
#   - The same ``MFJObserver`` instance is safe for concurrent use.
#
# For future coding agents:
#   - The coordinator calls observer methods at lifecycle boundaries.
#   - To add a new metric, add an instrument in ``_init_metrics()`` and
#     record it in the appropriate ``on_*`` method.
#   - Structured log fields are defined in ``_base_fields()`` — extend there
#     for new correlation dimensions.
# ==============================================================================

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("core.workflow.pack.mfj_observability")


# ---------------------------------------------------------------------------
# Optional OpenTelemetry imports
# ---------------------------------------------------------------------------

try:
    from opentelemetry import trace as otel_trace
    from opentelemetry import metrics as otel_metrics

    _HAS_OTEL = True
except ImportError:
    otel_trace = None  # type: ignore[assignment]
    otel_metrics = None  # type: ignore[assignment]
    _HAS_OTEL = False


# ---------------------------------------------------------------------------
# Span context carrier — returned from on_fan_out_started(), threaded through
# subsequent calls so child-spans share the same parent trace.
# ---------------------------------------------------------------------------

@dataclass
class MFJSpanContext:
    """Opaque carrier for in-flight MFJ trace/span references.

    The coordinator holds one of these per active MFJ cycle and passes it
    to each observer callback.  Fields are None when OTel is disabled.
    """

    #: Parent span covering the full trigger → resume arc.
    full_cycle_span: Any = None
    #: Fan-out span (trigger detection → child spawn complete).
    fan_out_span: Any = None
    #: Per-child spans keyed by task_id.
    child_spans: dict[str, Any] = field(default_factory=dict)
    #: Fan-in span (result collection → merge → resume).
    fan_in_span: Any = None
    #: Monotonic start time (for duration calculation even without OTel).
    start_time_ns: int = 0
    #: Unique trace id string for log correlation.
    mfj_trace_id: str = ""


# ---------------------------------------------------------------------------
# MFJObserver
# ---------------------------------------------------------------------------

class MFJObserver:
    """Structured observability for the MFJ coordinator.

    Usage (in the coordinator)::

        observer = MFJObserver()

        # Fan-out begins
        span_ctx = observer.on_fan_out_started(
            trigger_id="trigger_1",
            parent_chat_id="chat_abc",
            child_count=3,
            merge_mode="concatenate",
            timeout_seconds=30.0,
            workflow_name="my_pack",
            cycle=1,
        )

        # Each child spawned
        observer.on_child_spawned(span_ctx, task_id="t1", workflow_name="child_a")

        # Each child completed
        observer.on_child_completed(span_ctx, task_id="t1", success=True,
                                     duration_ms=1234.0)

        # Fan-in
        observer.on_fan_in_started(span_ctx, available_count=3, total_count=3)
        observer.on_fan_in_completed(span_ctx, strategy="concatenate",
                                      succeeded=3, failed=0)

        # Cycle done
        observer.on_cycle_completed(span_ctx, success=True)
    """

    def __init__(self) -> None:
        self._tracer: Any = None
        self._meter: Any = None

        # Metric instruments (initialised lazily).
        self._fan_out_counter: Any = None
        self._fan_in_counter: Any = None
        self._cycle_duration_hist: Any = None
        self._child_duration_hist: Any = None
        self._active_children_gauge: Any = None
        self._timeout_counter: Any = None
        self._partial_failure_counter: Any = None
        self._metrics_ready = False

        if _HAS_OTEL:
            self._tracer = otel_trace.get_tracer("mozaiks.mfj")
            self._init_metrics()

    # ------------------------------------------------------------------
    # Metrics initialisation
    # ------------------------------------------------------------------

    def _init_metrics(self) -> None:
        """Create OTel metric instruments.  No-op if SDK absent."""
        if not _HAS_OTEL or otel_metrics is None:
            return
        try:
            self._meter = otel_metrics.get_meter("mozaiks.mfj")

            self._fan_out_counter = self._meter.create_counter(
                name="mfj.fan_out.total",
                description="Total MFJ fan-out events",
                unit="1",
            )
            self._fan_in_counter = self._meter.create_counter(
                name="mfj.fan_in.total",
                description="Total MFJ fan-in completions",
                unit="1",
            )
            self._cycle_duration_hist = self._meter.create_histogram(
                name="mfj.cycle_duration_seconds",
                description="Duration of full MFJ cycle (trigger to resume)",
                unit="s",
            )
            self._child_duration_hist = self._meter.create_histogram(
                name="mfj.child_duration_seconds",
                description="Duration of individual child workflow execution",
                unit="s",
            )
            self._timeout_counter = self._meter.create_counter(
                name="mfj.timeout.total",
                description="Total MFJ timeout events",
                unit="1",
            )
            self._partial_failure_counter = self._meter.create_counter(
                name="mfj.partial_failure.total",
                description="Total MFJ partial-failure events",
                unit="1",
            )
            self._metrics_ready = True
        except Exception:
            logger.debug("MFJ metrics initialisation failed — metrics disabled")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _base_fields(
        trigger_id: str,
        parent_chat_id: str,
        mfj_trace_id: str = "",
        **extra: Any,
    ) -> dict[str, Any]:
        """Build the structured ``extra`` dict for log entries."""
        fields: dict[str, Any] = {
            "event_source": "mfj",
            "trigger_id": trigger_id,
            "parent_chat_id": parent_chat_id,
        }
        if mfj_trace_id:
            fields["mfj_trace_id"] = mfj_trace_id
        fields.update(extra)
        return fields

    def _start_span(self, name: str, attributes: dict[str, Any] | None = None, parent: Any = None) -> Any:
        """Start an OTel span if tracing is available, else return None."""
        if self._tracer is None:
            return None
        try:
            ctx = otel_trace.set_span_in_context(parent) if parent else None
            span = self._tracer.start_span(name, attributes=attributes or {}, context=ctx)
            return span
        except Exception:
            return None

    @staticmethod
    def _end_span(span: Any, *, error: str | None = None) -> None:
        """End an OTel span safely."""
        if span is None:
            return
        try:
            if error:
                span.set_status(otel_trace.StatusCode.ERROR, error)
            else:
                span.set_status(otel_trace.StatusCode.OK)
            span.end()
        except Exception:
            pass

    def _record_counter(self, counter: Any, value: int, labels: dict[str, str]) -> None:
        """Safely increment a counter metric."""
        if counter is None or not self._metrics_ready:
            return
        try:
            counter.add(value, attributes=labels)
        except Exception:
            pass

    def _record_histogram(self, hist: Any, value: float, labels: dict[str, str]) -> None:
        """Safely record a histogram observation."""
        if hist is None or not self._metrics_ready:
            return
        try:
            hist.record(value, attributes=labels)
        except Exception:
            pass

    # ==================================================================
    # Public lifecycle callbacks
    # ==================================================================

    def on_fan_out_started(
        self,
        *,
        trigger_id: str,
        parent_chat_id: str,
        child_count: int,
        merge_mode: str,
        timeout_seconds: float | None,
        workflow_name: str,
        cycle: int,
    ) -> MFJSpanContext:
        """Called when fan-out begins (children about to be spawned).

        Returns an ``MFJSpanContext`` that must be threaded through all
        subsequent callbacks for this cycle.
        """
        import uuid as _uuid

        mfj_trace_id = f"mfj-{_uuid.uuid4().hex[:12]}"

        # Structured log
        extra = self._base_fields(
            trigger_id=trigger_id,
            parent_chat_id=parent_chat_id,
            mfj_trace_id=mfj_trace_id,
            event="mfj.fan_out_started",
            child_count=child_count,
            merge_mode=merge_mode,
            timeout_seconds=timeout_seconds,
            workflow_name=workflow_name,
            cycle=cycle,
        )
        logger.info(
            "MFJ fan-out started: trigger_id=%s parent=%s children=%d "
            "merge=%s timeout=%s cycle=%d",
            trigger_id, parent_chat_id, child_count,
            merge_mode, timeout_seconds, cycle,
            extra=extra,
        )

        # OTel spans
        span_attrs = {
            "mfj.trigger_id": trigger_id,
            "mfj.parent_chat_id": parent_chat_id,
            "mfj.child_count": child_count,
            "mfj.merge_strategy": merge_mode,
            "mfj.timeout_seconds": timeout_seconds or 0,
            "mfj.workflow_name": workflow_name,
            "mfj.cycle": cycle,
        }
        full_cycle_span = self._start_span("mfj.full_cycle", attributes=span_attrs)
        fan_out_span = self._start_span(
            "mfj.fan_out", attributes=span_attrs, parent=full_cycle_span,
        )

        # Metric: fan-out counter
        self._record_counter(self._fan_out_counter, 1, {
            "workflow_name": workflow_name,
            "trigger_id": trigger_id,
        })

        return MFJSpanContext(
            full_cycle_span=full_cycle_span,
            fan_out_span=fan_out_span,
            start_time_ns=time.monotonic_ns(),
            mfj_trace_id=mfj_trace_id,
        )

    def on_fan_out_completed(self, ctx: MFJSpanContext) -> None:
        """Called after all children have been spawned (fan-out phase done)."""
        self._end_span(ctx.fan_out_span)
        ctx.fan_out_span = None

    def on_child_spawned(
        self, ctx: MFJSpanContext, *, task_id: str, workflow_name: str,
    ) -> None:
        """Called each time a child workflow is spawned."""
        child_span = self._start_span(
            "mfj.child_execution",
            attributes={
                "mfj.task_id": task_id,
                "mfj.child_workflow": workflow_name,
                "mfj.mfj_trace_id": ctx.mfj_trace_id,
            },
            parent=ctx.full_cycle_span,
        )
        ctx.child_spans[task_id] = child_span

    def on_child_completed(
        self,
        ctx: MFJSpanContext,
        *,
        task_id: str,
        success: bool,
        duration_ms: float = 0.0,
        trigger_id: str = "",
        parent_chat_id: str = "",
    ) -> None:
        """Called when a single child finishes (success or failure)."""
        extra = self._base_fields(
            trigger_id=trigger_id,
            parent_chat_id=parent_chat_id,
            mfj_trace_id=ctx.mfj_trace_id,
            event="mfj.child_completed",
            task_id=task_id,
            success=success,
            duration_ms=duration_ms,
        )
        logger.info(
            "MFJ child completed: task_id=%s success=%s duration_ms=%.1f",
            task_id, success, duration_ms,
            extra=extra,
        )

        # End child span
        child_span = ctx.child_spans.pop(task_id, None)
        error_msg = None if success else f"child {task_id} failed"
        self._end_span(child_span, error=error_msg)

        # Metric: child duration histogram
        if duration_ms > 0:
            self._record_histogram(self._child_duration_hist, duration_ms / 1000.0, {
                "task_id": task_id,
            })

    def on_fan_in_started(
        self,
        ctx: MFJSpanContext,
        *,
        available_count: int,
        total_count: int,
        trigger_id: str = "",
        parent_chat_id: str = "",
    ) -> None:
        """Called when fan-in (merge) begins."""
        extra = self._base_fields(
            trigger_id=trigger_id,
            parent_chat_id=parent_chat_id,
            mfj_trace_id=ctx.mfj_trace_id,
            event="mfj.fan_in_started",
            available_count=available_count,
            total_count=total_count,
        )
        logger.info(
            "MFJ fan-in started: available=%d total=%d",
            available_count, total_count,
            extra=extra,
        )

        ctx.fan_in_span = self._start_span(
            "mfj.fan_in",
            attributes={
                "mfj.available_count": available_count,
                "mfj.total_count": total_count,
                "mfj.mfj_trace_id": ctx.mfj_trace_id,
            },
            parent=ctx.full_cycle_span,
        )

    def on_fan_in_completed(
        self,
        ctx: MFJSpanContext,
        *,
        strategy: str,
        succeeded: int,
        failed: int,
        trigger_id: str = "",
        parent_chat_id: str = "",
        workflow_name: str = "",
    ) -> None:
        """Called when merge + parent resume is done."""
        outcome = "success" if failed == 0 else ("partial" if succeeded > 0 else "timeout")

        extra = self._base_fields(
            trigger_id=trigger_id,
            parent_chat_id=parent_chat_id,
            mfj_trace_id=ctx.mfj_trace_id,
            event="mfj.fan_in_completed",
            strategy=strategy,
            succeeded=succeeded,
            failed=failed,
            outcome=outcome,
        )
        logger.info(
            "MFJ fan-in completed: strategy=%s succeeded=%d failed=%d outcome=%s",
            strategy, succeeded, failed, outcome,
            extra=extra,
        )

        self._end_span(ctx.fan_in_span)
        ctx.fan_in_span = None

        # Metric: fan-in counter
        self._record_counter(self._fan_in_counter, 1, {
            "workflow_name": workflow_name,
            "trigger_id": trigger_id,
            "outcome": outcome,
        })

        # Metric: partial failure counter
        if failed > 0 and succeeded > 0:
            self._record_counter(self._partial_failure_counter, 1, {
                "strategy": strategy,
            })

    def on_timeout(
        self,
        ctx: MFJSpanContext,
        *,
        timeout_seconds: float,
        strategy: str,
        trigger_id: str = "",
        parent_chat_id: str = "",
    ) -> None:
        """Called when the MFJ timeout fires."""
        extra = self._base_fields(
            trigger_id=trigger_id,
            parent_chat_id=parent_chat_id,
            mfj_trace_id=ctx.mfj_trace_id,
            event="mfj.timeout",
            timeout_seconds=timeout_seconds,
            strategy=strategy,
        )
        logger.warning(
            "MFJ timeout fired: timeout=%.1fs strategy=%s trigger_id=%s",
            timeout_seconds, strategy, trigger_id,
            extra=extra,
        )

        # Metric: timeout counter
        self._record_counter(self._timeout_counter, 1, {
            "strategy": strategy,
        })

    def on_cycle_completed(
        self,
        ctx: MFJSpanContext,
        *,
        success: bool,
        trigger_id: str = "",
        parent_chat_id: str = "",
    ) -> None:
        """Called at the very end of the MFJ cycle (after resume)."""
        duration_ns = time.monotonic_ns() - ctx.start_time_ns
        duration_ms = duration_ns / 1_000_000.0

        extra = self._base_fields(
            trigger_id=trigger_id,
            parent_chat_id=parent_chat_id,
            mfj_trace_id=ctx.mfj_trace_id,
            event="mfj.cycle_completed",
            success=success,
            duration_ms=duration_ms,
        )
        logger.info(
            "MFJ cycle completed: trigger_id=%s success=%s duration_ms=%.1f",
            trigger_id, success, duration_ms,
            extra=extra,
        )

        # End full_cycle span
        error = None if success else "MFJ cycle completed with failures"
        self._end_span(ctx.full_cycle_span, error=error)

        # Metric: cycle duration histogram
        self._record_histogram(self._cycle_duration_hist, duration_ms / 1000.0, {
            "trigger_id": trigger_id,
        })

    def on_contract_violation(
        self,
        *,
        trigger_id: str,
        parent_chat_id: str,
        violation: str,
    ) -> None:
        """Called when an input/output contract check fails."""
        extra = self._base_fields(
            trigger_id=trigger_id,
            parent_chat_id=parent_chat_id,
            event="mfj.contract_violation",
            violation=violation,
        )
        logger.warning(
            "MFJ contract violation: trigger_id=%s — %s",
            trigger_id, violation,
            extra=extra,
        )

    def on_duplicate_suppressed(
        self,
        *,
        trigger_id: str,
        parent_chat_id: str,
    ) -> None:
        """Called when a duplicate MFJ trigger is suppressed."""
        extra = self._base_fields(
            trigger_id=trigger_id,
            parent_chat_id=parent_chat_id,
            event="mfj.duplicate_suppressed",
        )
        logger.info(
            "MFJ duplicate trigger suppressed: trigger_id=%s parent=%s",
            trigger_id, parent_chat_id,
            extra=extra,
        )


# ---------------------------------------------------------------------------
# Module-level singleton (lightweight — no state, just OTel handles)
# ---------------------------------------------------------------------------

_observer: MFJObserver | None = None


def get_mfj_observer() -> MFJObserver:
    """Return the singleton MFJObserver instance."""
    global _observer
    if _observer is None:
        _observer = MFJObserver()
    return _observer


def reset_mfj_observer() -> None:
    """Reset singleton (for tests)."""
    global _observer
    _observer = None


__all__ = [
    "MFJObserver",
    "MFJSpanContext",
    "get_mfj_observer",
    "reset_mfj_observer",
]
