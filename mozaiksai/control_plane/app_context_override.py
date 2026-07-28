"""Human override records for app-context policy blocks."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mozaiksai.control_plane.app_context_policy import (
    AppContextPolicyDecision,
    AppContextPolicyResult,
)

APP_CONTEXT_POLICY_OVERRIDE_WARNING = (
    "Human app-context policy override recorded; validation and promotion gates still apply."
)
APP_CONTEXT_POLICY_OVERRIDE_ALLOW_WARNING = (
    "Human override allows planning or staging to continue with stale or missing app-context evidence."
)
APP_CONTEXT_POLICY_OVERRIDE_REFRESH_WARNING = (
    "Human override requires context refresh before planning or staging can proceed."
)
APP_CONTEXT_POLICY_OVERRIDE_REJECT_WARNING = (
    "Human override reviewer rejected proceeding with this app-context policy block."
)

OVERRIDABLE_APP_CONTEXT_POLICY_DECISIONS = {
    AppContextPolicyDecision.BLOCK_REQUIRES_CONTEXT_REFRESH,
}


class AppContextPolicyOverrideDecision(StrEnum):
    ALLOW_WITH_WARNING = "allow_with_warning"
    REQUIRE_REFRESH_FIRST = "require_refresh_first"
    REJECT = "reject"


class AppContextPolicyOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    override_id: str
    app_id: str
    request_id: str
    context_version_id: str | None = None
    original_policy_decision: AppContextPolicyDecision
    override_decision: AppContextPolicyOverrideDecision
    reason: str
    reviewer: str
    reviewed_at: datetime
    expires_at: datetime | None = None
    applies_to_paths: list[str] = Field(default_factory=list)
    applies_to_change_class: str | None = None
    applies_to_refinement_lane: str | None = None
    warnings: list[str] = Field(default_factory=list)
    mutation_allowed: Literal[False] = False

    @field_validator("override_id", "app_id", "request_id", "reason", "reviewer")
    @classmethod
    def _required_text(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("value must be non-empty")
        return normalized

    @field_validator("applies_to_paths")
    @classmethod
    def _normalize_paths(cls, value: list[str]) -> list[str]:
        return _dedupe([_normalize_path(path) for path in value])

    @field_validator("applies_to_change_class", "applies_to_refinement_lane")
    @classmethod
    def _normalize_optional_text(cls, value: str | None) -> str | None:
        normalized = str(value or "").strip().lower()
        return normalized or None


def create_app_context_policy_override(
    *,
    policy_result: AppContextPolicyResult | dict[str, Any],
    app_id: str,
    request_id: str,
    override_decision: AppContextPolicyOverrideDecision | str,
    reason: str,
    reviewer: str,
    context_version_id: str | None = None,
    applies_to_paths: list[str] | None = None,
    applies_to_change_class: str | None = None,
    applies_to_refinement_lane: str | None = None,
    reviewed_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> AppContextPolicyOverride:
    """Create an auditable, non-mutating override for a blocked context policy."""
    policy = _normalize_policy(policy_result)
    if policy.decision not in OVERRIDABLE_APP_CONTEXT_POLICY_DECISIONS:
        raise ValueError("App-context policy override requires a blocked policy decision")

    resolved_decision = AppContextPolicyOverrideDecision(override_decision)
    resolved_reviewed_at = reviewed_at or datetime.now(UTC)
    normalized_paths = _dedupe([_normalize_path(path) for path in applies_to_paths or []])
    payload = {
        "app_id": app_id,
        "request_id": request_id,
        "context_version_id": context_version_id,
        "original_policy_decision": policy.decision.value,
        "override_decision": resolved_decision.value,
        "reason": str(reason or "").strip(),
        "reviewer": str(reviewer or "").strip(),
        "applies_to_paths": normalized_paths,
        "applies_to_change_class": str(applies_to_change_class or "").strip().lower(),
        "applies_to_refinement_lane": str(applies_to_refinement_lane or "").strip().lower(),
        "reviewed_at": resolved_reviewed_at.isoformat(),
    }
    return AppContextPolicyOverride(
        override_id=_stable_override_id(payload),
        app_id=app_id,
        request_id=request_id,
        context_version_id=context_version_id,
        original_policy_decision=policy.decision,
        override_decision=resolved_decision,
        reason=reason,
        reviewer=reviewer,
        reviewed_at=resolved_reviewed_at,
        expires_at=expires_at,
        applies_to_paths=normalized_paths,
        applies_to_change_class=applies_to_change_class,
        applies_to_refinement_lane=applies_to_refinement_lane,
        warnings=_override_warnings(resolved_decision),
        mutation_allowed=False,
    )


def apply_app_context_policy_override(
    plan: Any,
    override: AppContextPolicyOverride | dict[str, Any],
) -> Any:
    """Attach a scoped override to a dry-run or execution plan without mutation side effects."""
    resolved_override = _normalize_override(override)
    _validate_override_scope(plan=plan, override=resolved_override)

    policy_result = _plan_policy_result(plan, resolved_override.original_policy_decision)
    if policy_result.decision not in OVERRIDABLE_APP_CONTEXT_POLICY_DECISIONS:
        raise ValueError("App-context policy override can only be applied to a blocked policy decision")
    if policy_result.decision is not resolved_override.original_policy_decision:
        raise ValueError("Override original_policy_decision does not match the plan policy decision")

    effective_policy = _apply_decision_to_policy(
        policy_result=policy_result,
        override=resolved_override,
    )
    warnings = _dedupe([*(getattr(plan, "warnings", []) or []), *resolved_override.warnings])
    return plan.model_copy(
        update={
            "app_context_policy_override": resolved_override,
            "context_policy_decision": effective_policy,
            "warnings": warnings,
            "mutation_allowed": False,
        }
    )


def _apply_decision_to_policy(
    *,
    policy_result: AppContextPolicyResult,
    override: AppContextPolicyOverride,
) -> AppContextPolicyResult:
    if override.override_decision is not AppContextPolicyOverrideDecision.ALLOW_WITH_WARNING:
        return policy_result.model_copy(
            update={
                "warnings": _dedupe([*policy_result.warnings, *override.warnings]),
            }
        )

    return policy_result.model_copy(
        update={
            "decision": AppContextPolicyDecision.WARN,
            "allowed": True,
            "blocking": False,
            "reasons": _dedupe(
                [
                    *policy_result.reasons,
                    "Scoped human override allowed planning or staging to continue.",
                ]
            ),
            "warnings": _dedupe([*policy_result.warnings, *override.warnings]),
            "requires_context_refresh": False,
            "requires_human_override": False,
        }
    )


def _validate_override_scope(
    *,
    plan: Any,
    override: AppContextPolicyOverride,
) -> None:
    plan_app_id = str(getattr(plan, "app_id", "") or "").strip()
    if plan_app_id and plan_app_id != override.app_id:
        raise ValueError("Override app_id does not match the plan")

    plan_request_id = str(getattr(plan, "request_id", "") or "").strip()
    if plan_request_id and plan_request_id != override.request_id:
        raise ValueError("Override request_id does not match the plan")

    summary = getattr(plan, "app_context_summary", None)
    plan_context_version_id = str(getattr(summary, "context_version_id", "") or "").strip()
    if override.context_version_id and plan_context_version_id and override.context_version_id != plan_context_version_id:
        raise ValueError("Override context_version_id does not match the plan context")

    plan_paths = _dedupe([_normalize_path(path) for path in getattr(plan, "affected_bundle_paths", []) or []])
    if override.applies_to_paths and override.applies_to_paths != plan_paths:
        raise ValueError("Override applies_to_paths does not match the plan affected paths")

    plan_change_class = str(getattr(plan, "change_class", "") or "").strip().lower()
    if override.applies_to_change_class and override.applies_to_change_class != plan_change_class:
        raise ValueError("Override applies_to_change_class does not match the plan")

    plan_lane = str(getattr(plan, "refinement_lane", "") or "").strip().lower()
    if override.applies_to_refinement_lane and override.applies_to_refinement_lane != plan_lane:
        raise ValueError("Override applies_to_refinement_lane does not match the plan")


def _plan_policy_result(
    plan: Any,
    original_decision: AppContextPolicyDecision,
) -> AppContextPolicyResult:
    policy = getattr(plan, "context_policy_decision", None)
    if isinstance(policy, AppContextPolicyResult):
        return policy
    if isinstance(policy, dict):
        return AppContextPolicyResult.model_validate(policy)
    return AppContextPolicyResult(
        decision=original_decision,
        allowed=False,
        blocking=True,
    )


def _normalize_policy(value: AppContextPolicyResult | dict[str, Any]) -> AppContextPolicyResult:
    if isinstance(value, AppContextPolicyResult):
        return value
    return AppContextPolicyResult.model_validate(value)


def _normalize_override(value: AppContextPolicyOverride | dict[str, Any]) -> AppContextPolicyOverride:
    if isinstance(value, AppContextPolicyOverride):
        return value
    return AppContextPolicyOverride.model_validate(value)


def _override_warnings(decision: AppContextPolicyOverrideDecision) -> list[str]:
    warnings = [APP_CONTEXT_POLICY_OVERRIDE_WARNING]
    if decision is AppContextPolicyOverrideDecision.ALLOW_WITH_WARNING:
        warnings.append(APP_CONTEXT_POLICY_OVERRIDE_ALLOW_WARNING)
    elif decision is AppContextPolicyOverrideDecision.REQUIRE_REFRESH_FIRST:
        warnings.append(APP_CONTEXT_POLICY_OVERRIDE_REFRESH_WARNING)
    else:
        warnings.append(APP_CONTEXT_POLICY_OVERRIDE_REJECT_WARNING)
    return warnings


def _stable_override_id(payload: dict[str, Any]) -> str:
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    return f"ctx_override_{digest}"


def _normalize_path(path: str) -> str:
    return str(path or "").replace("\\", "/").strip().lstrip("/").lower()


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
    "APP_CONTEXT_POLICY_OVERRIDE_ALLOW_WARNING",
    "APP_CONTEXT_POLICY_OVERRIDE_REFRESH_WARNING",
    "APP_CONTEXT_POLICY_OVERRIDE_REJECT_WARNING",
    "APP_CONTEXT_POLICY_OVERRIDE_WARNING",
    "AppContextPolicyOverride",
    "AppContextPolicyOverrideDecision",
    "apply_app_context_policy_override",
    "create_app_context_policy_override",
]
