from __future__ import annotations

from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from mozaiksai.control_plane.dry_run import RefinementExecutionPlan
from mozaiksai.control_plane.review import RefinementReviewRecord
from mozaiksai.control_plane.scoped_execution import (
    ScopedRefinementChangedFile,
    ScopedRefinementResult,
)
from mozaiksai.control_plane.validation_evidence import (
    ValidationEvidence,
    normalize_validation_evidence,
)

PromotionPolicyMode = Literal[
    "direct_leaf_patch",
    "staged_generated_artifact",
    "artifact_version_promotion_required",
    "blocked_requires_replan",
    "blocked_requires_validation",
    "blocked_requires_upstream_artifact",
]

_UI_LEAF_PATTERNS = (
    "ui/pages/*.yaml",
    "ui/pages/custom/*.jsx",
    "ui/route_manifest.json",
    "ui/index.js",
    "config/shell.json",
)
_MODULE_GENERATED_PATTERNS = (
    "modules/*/module.yaml",
    "modules/*/contracts/*.yaml",
    "modules/*/backend/handler.py",
    "modules/*/backend/service.py",
    "modules/*/backend/schemas.py",
)
_DATA_MODEL_BACKEND_PATTERNS = (
    "modules/*/backend/repo.py",
    "modules/*/backend/policy.py",
    "modules/*/backend/schemas.py",
)
_INTEGRATION_PATTERNS = (
    "services/integrations/*_client.py",
    "services/adapters/**/*.py",
    "modules/*/backend/service.py",
    "modules/*/backend/schemas.py",
    "modules/*/module.yaml",
    "config/integrations*.json",
    "docs/integrations*.md",
)
_SOURCE_OF_TRUTH_GENERATED_PATTERNS = (
    "app.json",
    "brand/theme_config.json",
    "workflows/*/*.yaml",
)
_REPLAN_LANES = {"conceptual_reframe", "architecture_replan"}
_VALIDATION_ALIASES: dict[str, set[str]] = {
    "route_component_validation": {"route_component_validation"},
    "ui_theme_primitive_validation": {"ui_theme_primitive_validation"},
    "experience_spec_update": {"experience_spec_update", "experience_spec_validation", "experience_spec"},
    "experience_spec_validation": {"experience_spec_validation", "experience_spec_update", "experience_spec"},
    "app_bundle_validation": {"app_bundle_validation"},
    "data_contract_validation": {"data_contract_validation"},
    "migration_plan_validation": {"migration_plan_validation"},
    "integration_readiness_validation": {"integration_readiness_validation", "integration_readiness"},
    "hosted_facade_boundary_validation": {"hosted_facade_boundary_validation", "hosted_facade_validation"},
    "module_contract_validation": {"module_contract_validation"},
}


class PromotionPolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    allowed: bool
    mode: PromotionPolicyMode
    reason: str
    required_artifacts: list[str] = Field(default_factory=list)
    required_validation: list[str] = Field(default_factory=list)


def _normalize_path(path: str) -> str:
    normalized = str(path or "").replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    parts = [part for part in PurePosixPath(normalized).parts if part not in ("", ".")]
    return "/".join(parts)


def _normalized_path_set(paths: list[str]) -> set[str]:
    normalized: set[str] = set()
    for path in paths:
        candidate = _normalize_path(path)
        if candidate:
            normalized.add(candidate)
    return normalized


def _matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    pure_path = PurePosixPath(path)
    return any(pure_path.match(pattern) for pattern in patterns)


def _is_ui_leaf_path(path: str) -> bool:
    return _matches_any(path, _UI_LEAF_PATTERNS)


def _is_ui_only_scope(paths: list[str]) -> bool:
    normalized_paths = _normalized_path_set(paths)
    if not normalized_paths:
        return False
    allowed_paths = {"config/shell.json"}
    allowed_paths.update({path for path in normalized_paths if _is_ui_leaf_path(path)})
    return normalized_paths.issubset(allowed_paths)


def _is_module_internal_hosted_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    if len(parts) < 2 or parts[0] != "modules":
        return False
    module_id = parts[1].lower()
    return module_id.startswith("hosted_") or "provider" in module_id


def _required_validation_names(*, lane: str, path: str) -> list[str]:
    required: list[str] = []
    if lane == "experience_design":
        return [
            "experience_spec_update",
            "app_bundle_validation",
            "route_component_validation",
            "ui_theme_primitive_validation",
        ]
    if lane == "hosted_capability_change":
        if _is_ui_leaf_path(path):
            return [
                "hosted_facade_boundary_validation",
                "route_component_validation",
                "ui_theme_primitive_validation",
            ]
        return ["hosted_facade_boundary_validation"]
    if _matches_any(path, _DATA_MODEL_BACKEND_PATTERNS):
        return ["data_contract_validation", "migration_plan_validation"]
    if _matches_any(path, _INTEGRATION_PATTERNS):
        return ["integration_readiness_validation"]
    if _matches_any(path, _MODULE_GENERATED_PATTERNS):
        return ["module_contract_validation"]
    if _is_ui_leaf_path(path):
        return ["route_component_validation", "ui_theme_primitive_validation"]
    return required


def _required_validation_ids(plan: RefinementExecutionPlan) -> list[str]:
    required: list[str] = []
    for path in plan.affected_bundle_paths:
        required.extend(_required_validation_names(lane=str(plan.refinement_lane or "").strip(), path=_normalize_path(path)))
    if not required:
        required.extend(_required_validation_names(lane=str(plan.refinement_lane or "").strip(), path=""))
    return list(dict.fromkeys(required))


def _matches_validation_name(required_name: str, completed_names: set[str]) -> bool:
    aliases = _VALIDATION_ALIASES.get(required_name, {required_name})
    return bool(aliases & completed_names)


def _missing_validation_names(required_names: list[str], completed_names: set[str]) -> list[str]:
    return [name for name in required_names if not _matches_validation_name(name, completed_names)]


def _required_artifacts_for(*, lane: str, path: str) -> list[str]:
    if lane == "experience_design":
        return ["experience_spec"]
    if _matches_any(path, _DATA_MODEL_BACKEND_PATTERNS):
        return ["config/data.json", "config/data_migrations/*.json"]
    return []


def _has_required_artifact_evidence(required_artifacts: list[str], artifact_names: set[str]) -> bool:
    if not required_artifacts:
        return True
    for required in required_artifacts:
        required_lower = required.lower()
        if required_lower == "experience_spec":
            if any("experience_spec" in artifact for artifact in artifact_names):
                return True
            return False
        if required_lower == "config/data.json":
            if any("data_contract" in artifact or artifact == "config/data.json" for artifact in artifact_names):
                continue
            return False
        if required_lower == "config/data_migrations/*.json":
            if any("data_migrations/" in artifact for artifact in artifact_names):
                continue
            return False
    return True


def _build_blocked_decision(
    *,
    path: str,
    mode: PromotionPolicyMode,
    reason: str,
    required_artifacts: list[str] | None = None,
    required_validation: list[str] | None = None,
) -> PromotionPolicyDecision:
    return PromotionPolicyDecision(
        path=path,
        allowed=False,
        mode=mode,
        reason=reason,
        required_artifacts=required_artifacts or [],
        required_validation=required_validation or [],
    )


def _build_allowed_decision(
    *,
    path: str,
    mode: PromotionPolicyMode,
    reason: str,
    required_validation: list[str] | None = None,
) -> PromotionPolicyDecision:
    return PromotionPolicyDecision(
        path=path,
        allowed=True,
        mode=mode,
        reason=reason,
        required_artifacts=[],
        required_validation=required_validation or [],
    )


def evaluate_refinement_promotion_policy(
    *,
    plan: RefinementExecutionPlan,
    review_record: RefinementReviewRecord,
    execution_result: ScopedRefinementResult,
    change: ScopedRefinementChangedFile,
    validation_status: str | None = None,
    validation_evidence: ValidationEvidence | dict[str, object] | list[str] | None = None,
) -> PromotionPolicyDecision:
    normalized_path = _normalize_path(change.path)
    normalized_affected_paths = _normalized_path_set(list(plan.affected_bundle_paths))
    evidence = normalize_validation_evidence(validation_evidence)
    completed_names = evidence.completed_names()
    failed_names = evidence.failed_names()
    artifact_names = evidence.artifact_names()
    lane = str(plan.refinement_lane or "").strip()
    change_class = str(plan.change_class or "").strip().lower()
    evidence_failed = bool(failed_names) or validation_status == "failed"

    if review_record.request_id != plan.request_id or execution_result.request_id != plan.request_id:
        return _build_blocked_decision(
            path=normalized_path or str(change.path),
            mode="blocked_requires_replan",
            reason="Plan, review, and scoped execution request_ids must match before promotion.",
        )

    if not normalized_path:
        return _build_blocked_decision(
            path=str(change.path),
            mode="blocked_requires_replan",
            reason="Promotion requires a concrete relative path.",
        )

    if validation_status == "failed":
        return _build_blocked_decision(
            path=normalized_path,
            mode="blocked_requires_validation",
            reason="Promotion is blocked because validation failed.",
            required_validation=_required_validation_ids(plan),
        )

    if change.status != "updated":
        return _build_blocked_decision(
            path=normalized_path,
            mode="blocked_requires_validation",
            reason="Promotion only applies updated staged files.",
            required_validation=_required_validation_ids(plan),
        )

    if normalized_path not in normalized_affected_paths:
        return _build_blocked_decision(
            path=normalized_path,
            mode="blocked_requires_replan",
            reason="Change path is outside the affected_bundle_paths scope.",
        )

    if change_class == "core" or lane in _REPLAN_LANES:
        return _build_blocked_decision(
            path=normalized_path,
            mode="blocked_requires_replan",
            reason="Core and replan lanes must regenerate from upstream artifacts instead of direct promotion.",
        )

    if _is_module_internal_hosted_path(normalized_path):
        return _build_blocked_decision(
            path=normalized_path,
            mode="blocked_requires_replan",
            reason="Hosted/provider internal module paths are not directly promotable.",
        )

    if _matches_any(normalized_path, _SOURCE_OF_TRUTH_GENERATED_PATTERNS):
        return _build_blocked_decision(
            path=normalized_path,
            mode="artifact_version_promotion_required",
            reason="This generated artifact family should be promoted through an artifact-version path.",
        )

    if lane == "experience_design":
        required_artifacts = _required_artifacts_for(lane=lane, path=normalized_path)
        required_validation = _required_validation_names(lane=lane, path=normalized_path)
        if not _has_required_artifact_evidence(required_artifacts, artifact_names):
            return _build_blocked_decision(
                path=normalized_path,
                mode="blocked_requires_upstream_artifact",
                reason="Experience design changes require ExperienceSpec evidence before direct promotion.",
                required_artifacts=required_artifacts,
                required_validation=required_validation,
            )
        if evidence_failed:
            return _build_blocked_decision(
                path=normalized_path,
                mode="blocked_requires_validation",
                reason="Promotion is blocked because validation evidence reports a failure.",
                required_artifacts=required_artifacts,
                required_validation=required_validation,
            )
        missing_validations = _missing_validation_names(required_validation, completed_names)
        if missing_validations:
            return _build_blocked_decision(
                path=normalized_path,
                mode="blocked_requires_validation",
                reason="Promotion is missing required validation evidence.",
                required_artifacts=required_artifacts,
                required_validation=required_validation,
            )
        if not _is_ui_leaf_path(normalized_path) and normalized_path != "config/shell.json":
            return _build_blocked_decision(
                path=normalized_path,
                mode="blocked_requires_upstream_artifact",
                reason="Experience design promotion only applies to UI leaf, route, or shell paths.",
                required_artifacts=required_artifacts,
                required_validation=required_validation,
            )
        return _build_allowed_decision(
            path=normalized_path,
            mode="direct_leaf_patch",
            reason="ExperienceSpec evidence and validation evidence permit direct UI promotion.",
            required_validation=required_validation,
        )

    if _matches_any(normalized_path, _DATA_MODEL_BACKEND_PATTERNS) or lane == "data_model_migration":
        required_artifacts = _required_artifacts_for(lane="data_model_migration", path=normalized_path)
        required_validation = _required_validation_names(lane="data_model_migration", path=normalized_path)
        if not _has_required_artifact_evidence(required_artifacts, artifact_names):
            return _build_blocked_decision(
                path=normalized_path,
                mode="blocked_requires_upstream_artifact",
                reason="Database-backed module changes require data contract or migration artifacts.",
                required_artifacts=required_artifacts,
                required_validation=required_validation,
            )
        if evidence_failed:
            return _build_blocked_decision(
                path=normalized_path,
                mode="blocked_requires_validation",
                reason="Promotion is blocked because validation evidence reports a failure.",
                required_artifacts=required_artifacts,
                required_validation=required_validation,
            )
        if _missing_validation_names(required_validation, completed_names):
            return _build_blocked_decision(
                path=normalized_path,
                mode="blocked_requires_validation",
                reason="Promotion is missing required validation evidence.",
                required_artifacts=required_artifacts,
                required_validation=required_validation,
            )
        return _build_allowed_decision(
            path=normalized_path,
            mode="staged_generated_artifact",
            reason="Database-backed implementation can be promoted after database artifact evidence and validation evidence are present.",
            required_validation=required_validation,
        )

    if lane == "hosted_capability_change":
        required_validation = _required_validation_names(lane=lane, path=normalized_path)
        if evidence_failed:
            return _build_blocked_decision(
                path=normalized_path,
                mode="blocked_requires_validation",
                reason="Promotion is blocked because validation evidence reports a failure.",
                required_validation=required_validation,
            )
        if _missing_validation_names(required_validation, completed_names):
            return _build_blocked_decision(
                path=normalized_path,
                mode="blocked_requires_validation",
                reason="Promotion is missing required validation evidence.",
                required_validation=required_validation,
            )
        if _is_ui_leaf_path(normalized_path):
            return _build_allowed_decision(
                path=normalized_path,
                mode="direct_leaf_patch",
                reason="Hosted capability UI leaf changes are promotable with boundary validation evidence.",
                required_validation=required_validation,
            )
        return _build_allowed_decision(
            path=normalized_path,
            mode="staged_generated_artifact",
            reason="Hosted capability adapter and facade paths are promotable with boundary validation evidence.",
            required_validation=required_validation,
        )

    if _matches_any(normalized_path, _INTEGRATION_PATTERNS) or lane == "integration":
        required_validation = _required_validation_names(lane="integration", path=normalized_path)
        if evidence_failed:
            return _build_blocked_decision(
                path=normalized_path,
                mode="blocked_requires_validation",
                reason="Promotion is blocked because validation evidence reports a failure.",
                required_validation=required_validation,
            )
        if _missing_validation_names(required_validation, completed_names):
            return _build_blocked_decision(
                path=normalized_path,
                mode="blocked_requires_validation",
                reason="Promotion is missing required validation evidence.",
                required_validation=required_validation,
            )
        return _build_allowed_decision(
            path=normalized_path,
            mode="staged_generated_artifact",
            reason="Integration and adapter files are promotable when readiness validation evidence is present.",
            required_validation=required_validation,
        )

    if _matches_any(normalized_path, _MODULE_GENERATED_PATTERNS):
        required_validation = _required_validation_names(lane="feature_addition", path=normalized_path)
        if evidence_failed:
            return _build_blocked_decision(
                path=normalized_path,
                mode="blocked_requires_validation",
                reason="Promotion is blocked because validation evidence reports a failure.",
                required_validation=required_validation,
            )
        if _missing_validation_names(required_validation, completed_names):
            return _build_blocked_decision(
                path=normalized_path,
                mode="blocked_requires_validation",
                reason="Promotion is missing required validation evidence.",
                required_validation=required_validation,
            )
        return _build_allowed_decision(
            path=normalized_path,
            mode="staged_generated_artifact",
            reason="Generated module and backend files can be promoted when contract validation evidence is present.",
            required_validation=required_validation,
        )

    if _is_ui_leaf_path(normalized_path):
        required_validation = _required_validation_names(lane="ui_patch", path=normalized_path)
        if evidence_failed:
            return _build_blocked_decision(
                path=normalized_path,
                mode="blocked_requires_validation",
                reason="Promotion is blocked because validation evidence reports a failure.",
                required_validation=required_validation,
            )
        if _missing_validation_names(required_validation, completed_names):
            return _build_blocked_decision(
                path=normalized_path,
                mode="blocked_requires_validation",
                reason="Promotion is missing required validation evidence.",
                required_validation=required_validation,
            )
        if lane == "ui_patch" or (change_class == "patch" and _is_ui_only_scope(list(plan.affected_bundle_paths))):
            return _build_allowed_decision(
                path=normalized_path,
                mode="direct_leaf_patch",
                reason="UI leaf path is directly promotable for a narrow UI patch with validation evidence.",
                required_validation=required_validation,
            )
        return _build_blocked_decision(
            path=normalized_path,
            mode="blocked_requires_upstream_artifact",
            reason="UI leaf promotion requires a UI patch lane or upstream experience evidence.",
            required_validation=required_validation,
        )

    return _build_blocked_decision(
        path=normalized_path,
        mode="blocked_requires_upstream_artifact",
        reason="Direct promotion is not allowed for this artifact family.",
        required_validation=[],
    )


__all__ = [
    "PromotionPolicyDecision",
    "PromotionPolicyMode",
    "evaluate_refinement_promotion_policy",
]

