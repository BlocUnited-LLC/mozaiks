"""Server-owned attribution for auxiliary calls and runtime usage receipts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

from mozaiksai.core.session.build_binding import RunBuildBinding

UsageIdentity = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]


class AuxiliaryUsageContext(BaseModel):
    """Attribution supplied by the host, never inferred from model/caller content."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    app_id: UsageIdentity
    user_id: UsageIdentity
    tenant_id: UsageIdentity | None = None
    workspace_id: UsageIdentity | None = None
    chat_id: UsageIdentity | None = None
    workflow_name: UsageIdentity | None = None
    run_build_binding: RunBuildBinding | None = None


def resolve_auxiliary_usage_context(
    *,
    app_id: str | None,
    user_id: str | None,
    context: AuxiliaryUsageContext | None = None,
    target_app_id: str | None = None,
    run_build_binding: RunBuildBinding | None = None,
) -> AuxiliaryUsageContext:
    """Validate attribution against explicit execution inputs at an internal boundary."""
    resolved = context or AuxiliaryUsageContext.model_validate({"app_id": app_id, "user_id": user_id})
    if resolved.app_id != app_id or resolved.user_id != user_id:
        raise ValueError("Auxiliary usage owner does not match execution owner")
    binding = run_build_binding or resolved.run_build_binding
    if binding is not None and binding.target_app_id != target_app_id:
        raise ValueError("Auxiliary usage binding does not match execution target")
    if run_build_binding is not None:
        resolved = resolved.model_copy(update={"run_build_binding": run_build_binding})
    return resolved


class UsageReceiptScope(BaseModel):
    """Workflow receipts need a run; auxiliary receipts may precede its allocation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_kind: Literal["workflow", "auxiliary"] = "workflow"
    app_id: UsageIdentity
    user_id: UsageIdentity
    chat_id: UsageIdentity | None = None
    workflow_name: UsageIdentity | None = None
    agent_name: UsageIdentity | None = None

    @model_validator(mode="after")
    def validate_execution_identity(self) -> UsageReceiptScope:
        if self.execution_kind == "workflow" and (self.chat_id is None or self.workflow_name is None):
            raise ValueError("Workflow usage requires chat_id and workflow_name")
        if self.execution_kind == "auxiliary" and self.agent_name is None:
            raise ValueError("Auxiliary usage requires agent_name")
        return self
