"""Adapter for authenticated approved-execution context.

Hosted products may own approval and retrieval of a managed execution context.
This module only converts that bounded context into the existing OSS coding
worker request; it does not approve plans, fetch credentials, mutate source, or
promote artifacts.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .contracts import CodingWorkerRequest, safe_artifact_relpath


class ApprovedExecutionContext(BaseModel):
    """Strict context projection supplied by an authenticated host boundary."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "managed_refinement.execution_context.v1"
    handoff_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    app_id: str = Field(min_length=1)
    build_registry_id: str = Field(min_length=1)
    repository_full_name: str = Field(min_length=1)
    baseline_commit_sha: str = Field(min_length=40, max_length=40)
    raw_request: str = Field(min_length=1)
    request_type: str = Field(min_length=1)
    approved_plan_digest: str = Field(min_length=64, max_length=64)
    snapshot_digest: str = Field(min_length=1)
    graph_identity_digest: str = Field(min_length=1)
    impact_report_digest: str = Field(min_length=1)
    execution_strategy_id: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    allowed_paths: list[str] = Field(default_factory=list)
    prohibited_paths: list[str] = Field(default_factory=list)
    read_only_paths: list[str] = Field(default_factory=list)
    required_validation_gates: list[str] = Field(default_factory=list)
    required_security_gates: list[str] = Field(default_factory=list)
    required_ci_checks: list[str] = Field(default_factory=list)

    @field_validator("baseline_commit_sha")
    @classmethod
    def _validate_sha(cls, value: str) -> str:
        if any(char not in "0123456789abcdefABCDEF" for char in value):
            raise ValueError("baseline_commit_sha must be hexadecimal")
        return value

    @field_validator("approved_plan_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if any(char not in "0123456789abcdefABCDEF" for char in value):
            raise ValueError("approved_plan_digest must be hexadecimal")
        return value

    @field_validator("allowed_paths", "prohibited_paths", "read_only_paths")
    @classmethod
    def _validate_paths(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            path = safe_artifact_relpath(value)
            if path is None:
                raise ValueError(f"unsafe execution path: {value!r}")
            normalized.append(path)
        return sorted(set(normalized))


def _path_is_allowed(path: str, allowed_paths: list[str], prohibited_paths: list[str]) -> bool:
    normalized = safe_artifact_relpath(path)
    if normalized is None:
        return False
    for denied in prohibited_paths:
        if normalized == denied or normalized.startswith(f"{denied.rstrip('/')}/"):
            return False
    return any(
        normalized == allowed or normalized.startswith(f"{allowed.rstrip('/')}/")
        for allowed in allowed_paths
    )


def build_coding_request_from_execution_context(
    context: ApprovedExecutionContext | dict[str, Any],
    *,
    baseline_files: dict[str, str],
) -> CodingWorkerRequest:
    """Build an explicit-scope coding request from a host-approved context.

    ``baseline_files`` must already be fetched by the host at the approved
    baseline SHA. Only files inside the approved path scope are passed to the
    worker, and an empty scope fails closed.
    """

    approved = (
        context
        if isinstance(context, ApprovedExecutionContext)
        else ApprovedExecutionContext.model_validate(context)
    )
    scoped_files = {
        path: content
        for path, content in baseline_files.items()
        if _path_is_allowed(path, approved.allowed_paths, approved.prohibited_paths)
    }
    if not scoped_files:
        raise ValueError("approved execution context produced no scoped baseline files")
    return CodingWorkerRequest(
        app_id=approved.app_id,
        target_app_id=approved.app_id,
        build_family="app_bundle",
        build_key=approved.build_registry_id,
        requested_workflow_id=None,
        raw_user_request=approved.raw_request,
        change_class="patch",
        files=scoped_files,
        baseline_files=dict(baseline_files),
        validation_strategy="local",
        context_seed={
            "execution_context": approved.model_dump(mode="python"),
        },
        metadata={
            "execution_handoff_id": approved.handoff_id,
            "execution_request_id": approved.request_id,
            "execution_plan_id": approved.plan_id,
            "approved_plan_digest": approved.approved_plan_digest,
            "baseline_commit_sha": approved.baseline_commit_sha,
            "repository_full_name": approved.repository_full_name,
            "selected_file_paths": sorted(scoped_files),
        },
    )
