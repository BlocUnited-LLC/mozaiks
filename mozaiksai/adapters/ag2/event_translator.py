"""AG2 → DomainEvent translation layer.

This module is the **single place** that imports AG2 event classes and
normalizes them into engine-agnostic :class:`DomainEvent` instances.
Downstream code (kernel pipeline, transport) never does
``isinstance(ev, TextEvent)`` — it switches on ``domain_event.kind``.

Design rules
------------
* Pure translation — no I/O, no persistence, no side-effects.
* Every field the rest of the pipeline needs is extracted here.
* ``raw`` always carries the original AG2 object for middleware that
  truly needs vendor-specific access (e.g. ``InputRequestEvent.respond``).
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

# ---------------------------------------------------------------------------
# AG2 event classes — ALL AG2 event imports are contained in THIS file.
# ---------------------------------------------------------------------------
from autogen.events.agent_events import (
    TextEvent,
    InputRequestEvent,
    SelectSpeakerEvent,
    RunCompletionEvent,
    FunctionCallEvent,
    ToolCallEvent,
)
from autogen.events.client_events import UsageSummaryEvent

try:
    from autogen.events.client_events import StreamEvent as _StreamEvent
except ImportError:  # pragma: no cover — AG2 builds without StreamEvent
    _StreamEvent = None  # type: ignore[assignment,misc]

from mozaiksai.engine.streaming.domain_event import DomainEvent, EventKind

# ---------------------------------------------------------------------------
# Agent name extraction — mirrors orchestration's _extract_agent_name
# but self-contained so the adapter has zero import from orchestration.
# ---------------------------------------------------------------------------


def _agent_name(ev: Any) -> Optional[str]:
    """Best-effort agent name from an AG2 event object."""
    # TextEvent / SelectSpeakerEvent carry `.source` (an agent object or str)
    source = getattr(ev, "source", None)
    if source is not None:
        if isinstance(source, str):
            return source
        name = getattr(source, "name", None)
        if name:
            return str(name)

    # Fallback: sender attribute (str or agent)
    sender = getattr(ev, "sender", None)
    if sender is not None:
        if isinstance(sender, str):
            return sender
        name = getattr(sender, "name", None)
        if name:
            return str(name)

    # SelectSpeakerEvent uses .agent
    agent = getattr(ev, "agent", None)
    if agent is not None:
        if isinstance(agent, str):
            return agent
        name = getattr(agent, "name", None)
        if name:
            return str(name)

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def translate(ev: Any, *, sequence: int = 0) -> DomainEvent:
    """Translate a single AG2 event object into a :class:`DomainEvent`.

    Parameters
    ----------
    ev : Any
        An AG2 event yielded by ``response.events``.
    sequence : int
        Monotonic counter from the event loop.

    Returns
    -------
    DomainEvent
        Normalized event with ``kind``, extracted fields, and ``raw`` preserved.
    """

    # -- TextEvent ----------------------------------------------------------
    if isinstance(ev, TextEvent):
        content = getattr(ev, "content", "")
        return DomainEvent(
            kind=EventKind.TEXT,
            agent=_agent_name(ev),
            content=content,
            raw=ev,
            sequence=sequence,
        )

    # -- SelectSpeakerEvent -------------------------------------------------
    if isinstance(ev, SelectSpeakerEvent):
        selected = getattr(ev, "agent", None)
        selected_name = (
            selected if isinstance(selected, str) else getattr(selected, "name", None)
        )
        candidates = getattr(ev, "agents", None)
        return DomainEvent(
            kind=EventKind.SELECT_SPEAKER,
            agent=_agent_name(ev),
            content=selected_name,
            metadata={
                "selected_agent": selected_name,
                "candidates": candidates,
            },
            raw=ev,
            sequence=sequence,
        )

    # -- InputRequestEvent --------------------------------------------------
    if isinstance(ev, InputRequestEvent):
        content_obj = getattr(ev, "content", None)

        # Resolve unique request ID
        request_id = (
            getattr(ev, "uuid", None)
            or getattr(ev, "id", None)
            or (getattr(content_obj, "uuid", None) if content_obj else None)
            or (getattr(content_obj, "id", None) if content_obj else None)
            or str(uuid.uuid4())
        )

        # Resolve callback
        respond_cb = getattr(ev, "respond", None) or (
            getattr(content_obj, "respond", None) if content_obj else None
        )

        # Resolve prompt hint
        prompt = (
            getattr(ev, "prompt", None)
            or (getattr(content_obj, "prompt", None) if content_obj else None)
            or (getattr(content_obj, "message", None) if content_obj else None)
        )

        return DomainEvent(
            kind=EventKind.INPUT_REQUEST,
            agent=_agent_name(ev),
            content=content_obj,
            metadata={
                "request_id": str(request_id),
                "respond": respond_cb,
                "prompt": prompt,
            },
            raw=ev,
            sequence=sequence,
        )

    # -- FunctionCallEvent / ToolCallEvent ----------------------------------
    if isinstance(ev, (FunctionCallEvent, ToolCallEvent)):
        tool_name = getattr(ev, "tool_name", None)
        return DomainEvent(
            kind=EventKind.TOOL_CALL,
            agent=_agent_name(ev),
            content=ev,
            metadata={"tool_name": tool_name},
            raw=ev,
            sequence=sequence,
        )

    # -- UsageSummaryEvent --------------------------------------------------
    if isinstance(ev, UsageSummaryEvent):
        content_obj = getattr(ev, "content", None)
        prompt_tokens = getattr(content_obj, "prompt_tokens", 0) if content_obj else 0
        completion_tokens = (
            getattr(content_obj, "completion_tokens", 0) if content_obj else 0
        )
        cost = getattr(content_obj, "cost", 0.0) if content_obj else 0.0
        return DomainEvent(
            kind=EventKind.USAGE_SUMMARY,
            agent=_agent_name(ev),
            content={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cost": cost,
            },
            raw=ev,
            sequence=sequence,
        )

    # -- StreamEvent (optional in some AG2 builds) --------------------------
    if _StreamEvent is not None and isinstance(ev, _StreamEvent):
        chunk = getattr(ev, "content", "")
        return DomainEvent(
            kind=EventKind.STREAM_CHUNK,
            agent=_agent_name(ev),
            content=chunk,
            raw=ev,
            sequence=sequence,
        )

    # -- AfterWorksTransitionEvent (swarm handoff) --------------------------
    # AG2 uses Pydantic wrap_event which makes isinstance unreliable across
    # import paths.  Use the deterministic ``type`` field instead.
    _ev_type_field = getattr(ev, "type", None)
    _ev_cls_name = type(ev).__name__
    if _ev_type_field == "after_works_transition" or _ev_cls_name == "AfterWorksTransitionEvent":
        # Pydantic wrapper: inspect both direct attrs and .content sub-object
        source_agent = getattr(ev, "source_agent", None)
        transition_target = getattr(ev, "transition_target", None)
        content_obj = getattr(ev, "content", None)
        if transition_target is None and content_obj is not None:
            source_agent = getattr(content_obj, "source_agent", source_agent)
            transition_target = getattr(content_obj, "transition_target", None)
        target_cls_name = type(transition_target).__name__ if transition_target else "Unknown"

        # Determine if this is a handoff back to user
        is_revert_to_user = target_cls_name == "RevertToUserTarget"

        return DomainEvent(
            kind=EventKind.HANDOFF_TO_USER if is_revert_to_user else EventKind.UNKNOWN,
            agent=_agent_name(ev) or (getattr(source_agent, "name", None) if source_agent else None),
            content=None,
            metadata={
                "source_agent": getattr(source_agent, "name", None) if source_agent else None,
                "transition_target": target_cls_name,
                "is_revert_to_user": is_revert_to_user,
            },
            raw=ev,
            sequence=sequence,
        )

    # -- RunCompletionEvent -------------------------------------------------
    if isinstance(ev, RunCompletionEvent):
        return DomainEvent(
            kind=EventKind.RUN_COMPLETE,
            agent=_agent_name(ev),
            content=None,
            raw=ev,
            sequence=sequence,
        )

    # -- Fallback for unknown event types -----------------------------------
    import logging as _logging
    _adapter_logger = _logging.getLogger(__name__)
    _adapter_logger.warning(
        "[AG2_ADAPTER] Unknown event: class=%s, type_field=%r, attrs=%s",
        type(ev).__name__,
        getattr(ev, "type", "NO_TYPE_ATTR"),
        [a for a in dir(ev) if not a.startswith("_")][:20],
    )
    return DomainEvent(
        kind=EventKind.UNKNOWN,
        agent=_agent_name(ev),
        content=getattr(ev, "content", None),
        raw=ev,
        sequence=sequence,
    )


__all__ = ["translate", "DomainEvent", "EventKind"]
