"""Stale app-context policy for refinement planning."""

from __future__ import annotations

from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from mozaiksai.control_plane.app_context import (
    APP_CONTEXT_MISSING_WARNING,
    APP_CONTEXT_STALE_WARNING,
    AppContextSummary,
)
from mozaiksai.control_plane.app_context_impact import AppContextImpactHints

APP_CONTEXT_HIGH_RISK_BLOCK_WARNING = (
    "Current app context must be refreshed before high-risk refinement can proceed."
)


class AppContextPolicyDecision(StrEnum):
    ALLOW = "allow"
    WARN = "warn"
    BLOCK_REQUIRES_CONTEXT_REFRESH = "block_requires_context_refresh"
    BLOCK_REQUIRES_HUMAN_OVERRIDE = "block_requires_human_override"


class AppContextPolicyResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: AppContextPolicyDecision = AppContextPolicyDecision.ALLOW
    allowed: bool = True
    blocking: bool = False
    risk_level: str = "low"
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    graph_warnings: list[str] = Field(default_factory=list)
    graph_explanations: list[str] = Field(default_factory=list)
    risky_signals: list[str] = Field(default_factory=list)
    requires_context_refresh: bool = False
    requires_human_override: bool = False


def evaluate_app_context_policy(
    *,
    app_context_summary: AppContextSummary | dict[str, Any] | None,
    change_class: str,
    refinement_lane: str | None,
    affected_bundle_paths: list[str] | None = None,
    validation_warnings: list[str] | None = None,
    human_override_requested: bool = False,
) -> AppContextPolicyResult:
    summary = _normalize_summary(app_context_summary)
    paths = [_normalize_path(path) for path in affected_bundle_paths or []]
    warnings = _dedupe([*(validation_warnings or []), *_context_warnings(summary)])
    risky_signals = _risky_signals(
        summary=summary,
        change_class=change_class,
        refinement_lane=refinement_lane,
        affected_bundle_paths=paths,
    )
    risk_level = "high" if risky_signals else "low"
    context_state = _context_state(summary)

    if context_state == "fresh":
        return AppContextPolicyResult(
            decision=AppContextPolicyDecision.ALLOW,
            allowed=True,
            blocking=False,
            risk_level=risk_level,
            reasons=["Current app context is available and fresh."],
            warnings=[],
            risky_signals=risky_signals,
        )

    if risk_level == "low":
        return AppContextPolicyResult(
            decision=AppContextPolicyDecision.WARN,
            allowed=True,
            blocking=False,
            risk_level=risk_level,
            reasons=["Low-risk refinement may continue with stale or missing app-context evidence."],
            warnings=warnings,
            risky_signals=risky_signals,
        )

    if human_override_requested and context_state == "missing":
        return AppContextPolicyResult(
            decision=AppContextPolicyDecision.BLOCK_REQUIRES_HUMAN_OVERRIDE,
            allowed=False,
            blocking=True,
            risk_level=risk_level,
            reasons=["Missing app context on high-risk refinement requires explicit human override."],
            warnings=_dedupe([*warnings, APP_CONTEXT_HIGH_RISK_BLOCK_WARNING]),
            risky_signals=risky_signals,
            requires_human_override=True,
        )

    return AppContextPolicyResult(
        decision=AppContextPolicyDecision.BLOCK_REQUIRES_CONTEXT_REFRESH,
        allowed=False,
        blocking=True,
        risk_level=risk_level,
        reasons=["High-risk refinement requires current app-context evidence."],
        warnings=_dedupe([*warnings, APP_CONTEXT_HIGH_RISK_BLOCK_WARNING]),
        risky_signals=risky_signals,
        requires_context_refresh=True,
    )


def enrich_app_context_policy_with_graph_hints(
    policy_result: AppContextPolicyResult | dict[str, Any],
    impact_hints: AppContextImpactHints | dict[str, Any] | None,
) -> AppContextPolicyResult:
    """Attach advisory AppContextGraph warnings without changing the decision."""
    result = (
        policy_result
        if isinstance(policy_result, AppContextPolicyResult)
        else AppContextPolicyResult.model_validate(policy_result)
    )
    hints = _normalize_impact_hints(impact_hints)
    if hints is None:
        return result

    graph_warnings = _dedupe(
        [
            *hints.ownership_warnings,
            *hints.risk_warnings,
            hints.stale_graph_warning or "",
        ]
    )
    graph_explanations = _dedupe([f"AppContextGraph: {explanation}" for explanation in hints.explanations])
    if not graph_warnings and not graph_explanations:
        return result

    return result.model_copy(
        update={
            "warnings": _dedupe([*result.warnings, *graph_warnings, *graph_explanations]),
            "graph_warnings": _dedupe([*result.graph_warnings, *graph_warnings]),
            "graph_explanations": _dedupe([*result.graph_explanations, *graph_explanations]),
        }
    )


def _normalize_impact_hints(
    value: AppContextImpactHints | dict[str, Any] | None,
) -> AppContextImpactHints | None:
    if value is None:
        return None
    if isinstance(value, AppContextImpactHints):
        return value
    return AppContextImpactHints.model_validate(value)


def _normalize_summary(value: AppContextSummary | dict[str, Any] | None) -> AppContextSummary | None:
    if isinstance(value, AppContextSummary):
        return value
    if value is None:
        return None
    return AppContextSummary.model_validate(value)


def _context_state(summary: AppContextSummary | None) -> str:
    if summary is None or not summary.available:
        return "missing"
    status = str(summary.stale_status or "").strip().lower()
    if status in {"", "unknown", "stale", "partially_stale", "unsafe"}:
        return "stale"
    return "fresh"


def _context_warnings(summary: AppContextSummary | None) -> list[str]:
    if summary is None:
        return [APP_CONTEXT_MISSING_WARNING]
    if summary.warnings:
        return list(summary.warnings)
    if not summary.available:
        return [APP_CONTEXT_MISSING_WARNING]
    if _context_state(summary) == "stale":
        return [APP_CONTEXT_STALE_WARNING]
    return []


def _risky_signals(
    *,
    summary: AppContextSummary | None,
    change_class: str,
    refinement_lane: str | None,
    affected_bundle_paths: list[str],
) -> list[str]:
    signals: list[str] = []
    lane = str(refinement_lane or "").strip().lower()
    normalized_change_class = str(change_class or "").strip().lower()

    if lane in {"data_model_migration", "integration", "managed_capability_change"}:
        signals.append(lane)
    if lane in {"architecture_replan", "conceptual_reframe"} or normalized_change_class == "core":
        signals.append("conceptual_or_architecture_replan")
    if lane == "feature_addition" and _touches_module_or_backend(affected_bundle_paths):
        signals.append("module_backend_feature_addition")
    if _touches_sensitive_boundary(affected_bundle_paths):
        signals.append("sensitive_boundary_change")
    if _touches_read_only_discovered_boundary(summary, affected_bundle_paths):
        signals.append("read_only_discovered_boundary")
    if _is_brownfield_source_affecting(summary, affected_bundle_paths, lane):
        signals.append("brownfield_source_affecting_change")

    return _dedupe(signals)


def _touches_module_or_backend(paths: list[str]) -> bool:
    return any(path.startswith("modules/") or "/backend/" in f"/{path}" or path.startswith("services/") for path in paths)


def _touches_sensitive_boundary(paths: list[str]) -> bool:
    sensitive_terms = (
        ".env",
        ".github/workflows",
        "auth",
        "credential",
        "credentials",
        "deploy",
        "deployment",
        "docker",
        "permission",
        "permissions",
        "policy",
        "secret",
        "token",
        "vault",
    )
    return any(any(term in path for term in sensitive_terms) for path in paths)


def _touches_read_only_discovered_boundary(summary: AppContextSummary | None, paths: list[str]) -> bool:
    if summary is None:
        return False
    read_only_paths = [
        _normalize_path(boundary.path_or_artifact)
        for boundary in summary.ownership_boundaries
        if boundary.ownership == "read_only_discovered"
    ]
    return any(_paths_overlap(path, boundary_path) for path in paths for boundary_path in read_only_paths)


def _is_brownfield_source_affecting(
    summary: AppContextSummary | None,
    paths: list[str],
    lane: str,
) -> bool:
    if summary is None or summary.mode != "brownfield":
        return False
    if lane in {"ui_patch", "experience_design"} and not _touches_module_or_backend(paths):
        return False
    return bool(paths) or lane in {"data_model_migration", "integration", "feature_addition"}


def _paths_overlap(path: str, boundary_path: str) -> bool:
    if not path or not boundary_path:
        return False
    return (
        path == boundary_path
        or path.startswith(f"{boundary_path}/")
        or boundary_path.startswith(f"{path}/")
    )


def _normalize_path(path: str) -> str:
    normalized = str(path or "").replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    parts = [part for part in PurePosixPath(normalized).parts if part not in ("", ".")]
    return "/".join(parts).lower()


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
    "APP_CONTEXT_HIGH_RISK_BLOCK_WARNING",
    "AppContextPolicyDecision",
    "AppContextPolicyResult",
    "enrich_app_context_policy_with_graph_hints",
    "evaluate_app_context_policy",
]

