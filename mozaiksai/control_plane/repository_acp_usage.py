"""Advisory usage receipts for isolated repository ACP turns.

Only a trusted host worker may attach identity and attempt provenance. The
container's token counts are bounded measurements, not wallet or billing
authority; they go directly to the canonical runtime usage ledger.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from mozaiksai.core.usage.context import AuxiliaryUsageContext, resolve_auxiliary_usage_context
from mozaiksai.core.usage.ledger import RuntimeUsageLedger, get_runtime_usage_ledger

from .contracts import CodingWorkerRequest

_MAX_TOKENS_PER_TURN = 10_000_000
_ATTEMPT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}\Z")
_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}\Z")
logger = logging.getLogger(__name__)


class IsolatedACPUsage(BaseModel):
    """The only measurement fields accepted from an isolated ACP result."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    prompt_tokens: int = Field(ge=0, le=_MAX_TOKENS_PER_TURN)
    completion_tokens: int = Field(ge=0, le=_MAX_TOKENS_PER_TURN)
    total_tokens: int = Field(ge=0, le=_MAX_TOKENS_PER_TURN)

    @model_validator(mode="after")
    def validate_total(self) -> IsolatedACPUsage:
        if self.total_tokens < self.prompt_tokens + self.completion_tokens:
            raise ValueError("ACP usage total is smaller than its parts")
        return self


def parse_isolated_acp_usage(value: Any) -> IsolatedACPUsage | None:
    """Drop missing, zero, or malformed container measurements without quoting them."""

    if value is None:
        return None
    try:
        usage = IsolatedACPUsage.model_validate(value)
    except (ValidationError, ValueError, TypeError):
        return None
    return usage if usage.total_tokens else None


async def record_isolated_acp_usage(
    usage: IsolatedACPUsage | None,
    *,
    request: CodingWorkerRequest,
    attempt_id: str,
    model_name: str | None = None,
    ledger: RuntimeUsageLedger | None = None,
) -> bool:
    """Record one host-attributed attempt idempotently in the existing ledger.

    ``request``, ``attempt_id``, and ``model_name`` are trusted-worker inputs.
    Never derive them from a container proposal. A retry of the same durable
    attempt has the same event ID and cannot double count in the ledger.
    """

    if not isinstance(request, CodingWorkerRequest) or not isinstance(request.usage_context, AuxiliaryUsageContext):
        raise TypeError("ACP usage requires a host-owned CodingWorkerRequest and AuxiliaryUsageContext")
    context = resolve_auxiliary_usage_context(
        app_id=request.app_id, user_id=request.user_id, context=request.usage_context,
        target_app_id=request.target_app_id, run_build_binding=request.run_build_binding,
    )
    if not isinstance(attempt_id, str) or not _ATTEMPT_ID.fullmatch(attempt_id):
        raise ValueError("ACP usage requires a stable host-owned attempt_id")
    if model_name is not None and (not isinstance(model_name, str) or not _MODEL_NAME.fullmatch(model_name)):
        raise ValueError("ACP usage model_name must be a host-owned model identifier")
    if usage is None:
        return False
    if not isinstance(usage, IsolatedACPUsage):
        raise TypeError("ACP usage must be validated before recording")
    usage = IsolatedACPUsage.model_validate(usage.model_dump(mode="python"))
    if usage.total_tokens == 0:
        return False

    identity = json.dumps(
        {
            "app_id": context.app_id,
            "user_id": context.user_id,
            "tenant_id": context.tenant_id,
            "workspace_id": context.workspace_id,
            "attempt_id": attempt_id,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    event_id = "acp:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
    payload = {
        "event_id": event_id,
        "execution_kind": "auxiliary",
        "app_id": context.app_id,
        "user_id": context.user_id,
        "tenant_id": context.tenant_id,
        "workspace_id": context.workspace_id,
        "chat_id": context.chat_id,
        "workflow_name": context.workflow_name,
        "build_id": context.run_build_binding.build_id if context.run_build_binding else None,
        "agent_name": "RepositoryACPCodingAgent",
        "model_name": model_name,
        **usage.model_dump(mode="python"),
    }
    try:
        await (ledger or get_runtime_usage_ledger()).record_usage_delta(payload, source="isolated_acp")
    except Exception:
        # Usage is advisory. A transient ledger outage must not invalidate an
        # otherwise valid patch; the trusted worker can retry this attempt.
        logger.warning("ISOLATED_ACP_USAGE_RECORD_FAILED event_id=%s", event_id)
        return False
    return True


__all__ = ["IsolatedACPUsage", "parse_isolated_acp_usage", "record_isolated_acp_usage"]
