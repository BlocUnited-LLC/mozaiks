"""Refinement Engine helpers for planning app-context refreshes."""

from __future__ import annotations

from typing import Any

from mozaiksai.control_plane.app_context import AppContextSummary
from mozaiksai.control_plane.app_context_policy import (
    AppContextPolicyDecision,
    AppContextPolicyResult,
)
from mozaiksai.core.app_context.models import SourceRef, SourceRefKind
from mozaiksai.core.app_context.refresh import (
    BROWNFIELD_DISCOVERY_REFRESH_SEQUENCE,
    CONTEXT_REFRESH_EXPECTED_ARTIFACTS,
    ContextRefreshPlan,
    ContextRefreshRequest,
    ContextRefreshScope,
)

CONTEXT_REFRESH_REQUIRED_INPUTS = (
    "source_refs",
    "scan_policy_or_boundaries",
    "current_context_version_id_or_reason",
)


def build_context_refresh_request(
    *,
    app_id: str | None = None,
    app_context_summary: AppContextSummary | dict[str, Any] | None = None,
    policy_result: AppContextPolicyResult | dict[str, Any] | None = None,
    reason: str | None = None,
    source_refs: list[SourceRef] | None = None,
    requested_by: str | None = None,
    refresh_scope: ContextRefreshScope | str = ContextRefreshScope.DISCOVERY_INDEXING,
) -> ContextRefreshRequest:
    summary = _normalize_summary(app_context_summary)
    policy = _normalize_policy(policy_result)
    resolved_app_id = str(app_id or (summary.app_id if summary else "") or "").strip()
    if not resolved_app_id:
        raise ValueError("app_id is required to build a context refresh request")

    return ContextRefreshRequest(
        app_id=resolved_app_id,
        current_context_version_id=summary.context_version_id if summary else None,
        reason=_refresh_reason(reason=reason, policy_result=policy),
        source_refs=source_refs if source_refs is not None else _source_refs_from_summary(summary),
        requested_by=requested_by,
        refresh_scope=ContextRefreshScope(refresh_scope),
    )


def build_context_refresh_plan(
    *,
    policy_result: AppContextPolicyResult | dict[str, Any],
    app_context_summary: AppContextSummary | dict[str, Any] | None = None,
    request: ContextRefreshRequest | dict[str, Any] | None = None,
    app_id: str | None = None,
    requested_by: str | None = None,
    reason: str | None = None,
    source_refs: list[SourceRef] | None = None,
    refresh_scope: ContextRefreshScope | str = ContextRefreshScope.DISCOVERY_INDEXING,
) -> ContextRefreshPlan:
    """Build a non-mutating plan for refreshing stale app context."""
    policy = _normalize_policy(policy_result)
    if policy.decision is not AppContextPolicyDecision.BLOCK_REQUIRES_CONTEXT_REFRESH:  # type: ignore[union-attr]
        raise ValueError("Context refresh plan requires block_requires_context_refresh policy decision")

    refresh_request = (
        request
        if isinstance(request, ContextRefreshRequest)
        else ContextRefreshRequest.model_validate(request)
        if request is not None
        else build_context_refresh_request(
            app_id=app_id,
            app_context_summary=app_context_summary,
            policy_result=policy,
            reason=reason,
            source_refs=source_refs,
            requested_by=requested_by,
            refresh_scope=refresh_scope,
        )
    )
    warnings = _plan_warnings(refresh_request=refresh_request, policy_result=policy)  # type: ignore[arg-type]
    return ContextRefreshPlan(
        app_id=refresh_request.app_id,
        current_context_version_id=refresh_request.current_context_version_id,
        target_source_refs=refresh_request.source_refs,
        workflow_sequence=BROWNFIELD_DISCOVERY_REFRESH_SEQUENCE,
        required_inputs=list(CONTEXT_REFRESH_REQUIRED_INPUTS),
        expected_artifacts=list(CONTEXT_REFRESH_EXPECTED_ARTIFACTS),
        mutation_allowed=False,
        warnings=warnings,
    )


def _normalize_summary(value: AppContextSummary | dict[str, Any] | None) -> AppContextSummary | None:
    if isinstance(value, AppContextSummary):
        return value
    if value is None:
        return None
    return AppContextSummary.model_validate(value)


def _normalize_policy(value: AppContextPolicyResult | dict[str, Any] | None) -> AppContextPolicyResult | None:
    if isinstance(value, AppContextPolicyResult) or value is None:
        return value
    return AppContextPolicyResult.model_validate(value)


def _refresh_reason(
    *,
    reason: str | None,
    policy_result: AppContextPolicyResult | None,
) -> str:
    resolved_reason = str(reason or "").strip()
    if resolved_reason:
        return resolved_reason
    if policy_result is not None and policy_result.reasons:
        return " ".join(policy_result.reasons)
    return "Refresh App Intelligence context before retrying refinement."


def _source_refs_from_summary(summary: AppContextSummary | None) -> list[SourceRef]:
    if summary is None:
        return []
    refs: list[SourceRef] = []
    for ref in summary.source_refs:
        refs.append(
            SourceRef(
                source_ref_id=ref.ref_id,
                kind=SourceRefKind(ref.kind),
                uri=ref.target,
                metadata={"source": "app_context_summary"},
            )
        )
    return refs


def _plan_warnings(
    *,
    refresh_request: ContextRefreshRequest,
    policy_result: AppContextPolicyResult,
) -> list[str]:
    warnings = list(policy_result.warnings)
    if not refresh_request.current_context_version_id:
        warnings.append(
            "No current AppContextVersion is available; refresh will start from provided source refs."
        )
    if not refresh_request.source_refs:
        warnings.append("Context refresh plan has no source refs; scan boundaries must be supplied before execution.")
    return _dedupe(warnings)


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


__all__ = [
    "CONTEXT_REFRESH_REQUIRED_INPUTS",
    "build_context_refresh_plan",
    "build_context_refresh_request",
]
