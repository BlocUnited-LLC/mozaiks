"""Audit trail for agent replies rejected by structured-output validation."""

from __future__ import annotations

import asyncio
from collections import OrderedDict

from logs.logging_config import get_core_logger
from mozaiksai.core.audit.audit_logger import get_audit_logger
from mozaiksai.core.tracing.context import get_trace_id

from .runtime_validation import StructuredOutputValidation

logger = get_core_logger("structured_output_audit")

# Auditing must not hold up reporting the rejection it describes.
_AUDIT_TIMEOUT_SECONDS = 5.0

# The network path can validate one reply twice (when the packet is handled and
# again over the WAL), and the audit log is append-only. A rejection that carries
# a turn key is recorded once per process; the key names the packet, so a retried
# turn (new causation) still gets its own record. In-process only: it bounds
# duplicates within a run, it is not a cross-process uniqueness guarantee.
_MAX_RECORDED_TURNS = 4096
_recorded_turns: OrderedDict[str, None] = OrderedDict()


def packet_turn_key(chat_id: str | None, channel_id: str, agent_name: str, causation_id: str) -> str:
    """Identity of one agent packet; the same shape in every validation site."""

    return f"{chat_id}:{channel_id}:{agent_name}:{causation_id}"


def _claim(validation: StructuredOutputValidation, key: str | None) -> str | None:
    """Reserve the dedupe slot; ``None`` means already recorded."""

    if not key:
        return ""
    slot = f"{key}:{validation.output_digest}"
    if slot in _recorded_turns:
        return None
    _recorded_turns[slot] = None
    while len(_recorded_turns) > _MAX_RECORDED_TURNS:
        _recorded_turns.popitem(last=False)
    return slot


async def audit_structured_output_rejection(
    validation: StructuredOutputValidation,
    *,
    source: str,
    workflow_name: str,
    chat_id: str | None,
    app_id: str | None,
    user_id: str | None = None,
    turn_key: str | None = None,
) -> None:
    """Write one audit record for a rejected reply; never raises.

    ``user_id`` must be the verified initiating identity from the runtime, not a
    value read from model output or context variables. Only the failure
    category, declared field names with error types, and a digest of the reply
    are recorded; the reply and Pydantic's message text (which quotes input
    values) are deliberately left out. A failure here is logged and swallowed,
    so it can neither mask the validation error nor change the fail-closed
    outcome the caller applies.
    """

    if validation.validation_passed:
        return
    slot = _claim(validation, turn_key)
    if slot is None:
        return
    try:
        await asyncio.wait_for(
            get_audit_logger().log_agent_output_rejected(
                actor_id=user_id or "system",
                app_id=app_id,
                chat_id=chat_id,
                workflow_name=workflow_name,
                agent_name=validation.agent_name,
                output_digest=validation.output_digest,
                error=validation.failure_category or "validation_failed",
                extra={
                    "model_name": validation.model_name,
                    "failure_fields": list(validation.failure_fields),
                    "source": source,
                    "trace_id": get_trace_id() or None,
                },
            ),
            timeout=_AUDIT_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # CancelledError is a BaseException and still propagates
        if slot:
            _recorded_turns.pop(slot, None)
        logger.warning(
            "AGENT_OUTPUT_REJECTION_AUDIT_FAILED agent=%s source=%s error=%s",
            validation.agent_name,
            source,
            type(exc).__name__,
        )
    except BaseException:
        if slot:
            _recorded_turns.pop(slot, None)
        raise


__all__ = ["audit_structured_output_rejection", "packet_turn_key"]
