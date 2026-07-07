# ==============================================================================
# FILE: mozaiksai/core/tracing/context.py
# DESCRIPTION: Async-safe trace context propagation via Python contextvars.
#
# Purpose: carry the current trace/request ID through async call chains without
# explicit parameter threading. Python's asyncio automatically copies the active
# Context (and all ContextVars inside it) to child tasks created with
# asyncio.create_task(), so trace IDs set here are visible inside:
#   - Route handlers and their awaited callees
#   - asyncio.create_task() (audit logger, fire-and-forget)
#   - Background tasks launched within the request context
#
# Usage:
#   # In middleware — set once per request:
#   from mozaiksai.core.tracing.context import bind_trace_id
#   bind_trace_id(request_id)
#
#   # Anywhere downstream — read without passing as a parameter:
#   from mozaiksai.core.tracing.context import get_trace_id
#   logger.info("doing work trace_id=%s", get_trace_id())
#
# The ContextVar default is an empty string so get_trace_id() is always safe to
# call even outside a request context (background workers, tests, etc.).
# ==============================================================================
from __future__ import annotations

import contextvars
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Core context variable
# ---------------------------------------------------------------------------

_TRACE_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "mozaiks_trace_id",
    default="",
)

# Optional additional fields carried alongside the trace ID.
_SPAN_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "mozaiks_span_id",
    default="",
)
_WORKFLOW_NAME: contextvars.ContextVar[str] = contextvars.ContextVar(
    "mozaiks_trace_workflow",
    default="",
)
_CHAT_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "mozaiks_trace_chat_id",
    default="",
)
_APP_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "mozaiks_trace_app_id",
    default="",
)


# ---------------------------------------------------------------------------
# Public accessors
# ---------------------------------------------------------------------------

def get_trace_id() -> str:
    """Return the current trace ID, or an empty string if none is set."""
    return _TRACE_ID.get()


def get_span_id() -> str:
    """Return the current span ID, or an empty string if none is set."""
    return _SPAN_ID.get()


def get_workflow_name() -> str:
    """Return the workflow name bound to this trace context, if any."""
    return _WORKFLOW_NAME.get()


def get_chat_id() -> str:
    """Return the chat ID bound to this trace context, if any."""
    return _CHAT_ID.get()


def get_app_id() -> str:
    """Return the app ID bound to this trace context, if any."""
    return _APP_ID.get()


def bind_trace_id(
    trace_id: str,
    *,
    span_id: str | None = None,
    workflow_name: str | None = None,
    chat_id: str | None = None,
    app_id: str | None = None,
) -> None:
    """Set the trace ID (and optional fields) on the current async context.

    Call this once per request boundary (e.g. in middleware or a task entry
    point). Child tasks inherit the value automatically.
    """
    _TRACE_ID.set(trace_id)
    if span_id is not None:
        _SPAN_ID.set(span_id)
    if workflow_name is not None:
        _WORKFLOW_NAME.set(workflow_name)
    if chat_id is not None:
        _CHAT_ID.set(chat_id)
    if app_id is not None:
        _APP_ID.set(app_id)


# ---------------------------------------------------------------------------
# Snapshot dataclass for log enrichment
# ---------------------------------------------------------------------------

@dataclass
class TraceContext:
    """Snapshot of all trace fields at a point in time."""

    trace_id: str = field(default_factory=get_trace_id)
    span_id: str = field(default_factory=get_span_id)
    workflow_name: str = field(default_factory=get_workflow_name)
    chat_id: str = field(default_factory=get_chat_id)
    app_id: str = field(default_factory=get_app_id)

    def as_log_extra(self) -> dict[str, str]:
        """Return a dict suitable for ``logger.info(..., extra=ctx.as_log_extra())``."""
        extra: dict[str, str] = {}
        if self.trace_id:
            extra["trace_id"] = self.trace_id
        if self.span_id:
            extra["span_id"] = self.span_id
        if self.workflow_name:
            extra["workflow"] = self.workflow_name
        if self.chat_id:
            extra["chat_id"] = self.chat_id
        if self.app_id:
            extra["app_id"] = self.app_id
        return extra

    def as_headers(self) -> dict[str, str]:
        """Return trace headers for outbound HTTP calls."""
        headers: dict[str, str] = {}
        if self.trace_id:
            headers["X-Trace-ID"] = self.trace_id
        if self.span_id:
            headers["X-Span-ID"] = self.span_id
        return headers


# ---------------------------------------------------------------------------
# Context manager for scoped trace binding
# ---------------------------------------------------------------------------

@contextmanager
def trace_context(
    trace_id: str | None = None,
    *,
    span_id: str | None = None,
    workflow_name: str | None = None,
    chat_id: str | None = None,
    app_id: str | None = None,
) -> Generator[TraceContext, None, None]:
    """Context manager that binds a trace ID for the duration of a block.

    Automatically generates a UUID trace ID when none is provided. Restores
    the previous values on exit so nested usage is safe.

    Usage::

        with trace_context(workflow_name="AppGenerator", chat_id=chat_id) as ctx:
            logger.info("Starting workflow trace_id=%s", ctx.trace_id)
            ...
    """
    resolved_trace_id = trace_id or str(uuid.uuid4())

    # Save tokens so we can restore on exit
    token_trace = _TRACE_ID.set(resolved_trace_id)
    token_span = _SPAN_ID.set(span_id or "") if span_id is not None else None
    token_workflow = _WORKFLOW_NAME.set(workflow_name or "") if workflow_name is not None else None
    token_chat = _CHAT_ID.set(chat_id or "") if chat_id is not None else None
    token_app = _APP_ID.set(app_id or "") if app_id is not None else None

    try:
        yield TraceContext(
            trace_id=resolved_trace_id,
            span_id=span_id or "",
            workflow_name=workflow_name or "",
            chat_id=chat_id or "",
            app_id=app_id or "",
        )
    finally:
        _TRACE_ID.reset(token_trace)
        if token_span is not None:
            _SPAN_ID.reset(token_span)
        if token_workflow is not None:
            _WORKFLOW_NAME.reset(token_workflow)
        if token_chat is not None:
            _CHAT_ID.reset(token_chat)
        if token_app is not None:
            _APP_ID.reset(token_app)


__all__ = [
    "TraceContext",
    "bind_trace_id",
    "get_trace_id",
    "get_span_id",
    "get_workflow_name",
    "get_chat_id",
    "get_app_id",
    "trace_context",
]
