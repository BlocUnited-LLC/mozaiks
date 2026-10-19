"""Audit trail for agent replies rejected by structured-output validation."""

from __future__ import annotations

import asyncio

from logs.logging_config import get_core_logger
from mozaiksai.core.audit.audit_logger import get_audit_logger
from mozaiksai.core.tracing.context import get_trace_id

from .runtime_validation import StructuredOutputValidation

logger = get_core_logger("structured_output_audit")

# Auditing must not hold up reporting the rejection it describes.
_AUDIT_TIMEOUT_SECONDS = 5.0


async def audit_structured_output_rejection(
    validation: StructuredOutputValidation,
    *,
    source: str,
    workflow_name: str,
    chat_id: str | None,
    app_id: str | None,
    user_id: str | None = None,
) -> None:
    """Write one audit record for a rejected reply; never raises.

    Only the failure category, offending field paths and error types, and a
    digest of the reply are recorded. The reply and Pydantic's message text
    (which quotes input values) are deliberately left out. A failure here is
    logged and swallowed, so it can neither mask the validation error nor change
    the fail-closed outcome the caller applies.
    """

    if validation.validation_passed:
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
        logger.warning(
            "AGENT_OUTPUT_REJECTION_AUDIT_FAILED agent=%s source=%s error=%s",
            validation.agent_name,
            source,
            type(exc).__name__,
        )


__all__ = ["audit_structured_output_rejection"]
