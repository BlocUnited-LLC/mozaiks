from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from mozaiksai.control_plane.app_context import (
    AppContextSummary,
    app_context_warnings,
    get_current_app_context_graph,
    get_current_app_context_summary,
)
from mozaiksai.control_plane.app_context_impact import (
    AppContextImpactHints,
    derive_app_context_impact_hints,
)
from mozaiksai.control_plane.app_context_override import AppContextPolicyOverride
from mozaiksai.control_plane.app_context_policy import (
    AppContextPolicyResult,
    enrich_app_context_policy_with_graph_hints,
    evaluate_app_context_policy,
)
from mozaiksai.control_plane.config import ControlPlaneConfig, load_control_plane_config
from mozaiksai.control_plane.implementations.change_classifier import LLMChangeClassifier
from mozaiksai.control_plane.implementations.refinement_router import (
    ChangeClass,
    RefinementTriggerRouteResolver,
)
from mozaiksai.control_plane.loader import load_refinement_harness

DRY_RUN_NOTICE = "No files were changed."
VALID_CHANGE_CLASSES = {change_class.value for change_class in ChangeClass}
SECRET_PATH_TERMS = (
    ".env",
    "apikey",
    "api_key",
    "credential",
    "credentials",
    "password",
    "secret",
    "token",
    "vault",
)


class RefinementDryRunProfiles(BaseModel):
    model_config = ConfigDict(extra="forbid")

    classifier: str
    planner_or_codegen: str | None = None
    reviewer_validator: str | None = None


class RefinementExecutionProfiles(BaseModel):
    model_config = ConfigDict(extra="forbid")

    classifier: str
    planner_replanner: str | None = None
    codegen: str | None = None
    reviewer_validator: str | None = None


class RefinementValidationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    required: bool
    reason: str


class RefinementValidationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[RefinementValidationItem] = Field(default_factory=list)


class RefinementExecutionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    request_id: str
    app_id: str | None = None
    request: str
    build_family: str = Field(alias="artifact_kind")
    change_class: str
    refinement_lane: str | None = None
    workflow_id: str
    workflow_sequence: str
    target_workflow: str
    affected_workflows: list[str] = Field(default_factory=list)
    affected_declarative_families: list[str] = Field(default_factory=list)
    affected_bundle_paths: list[str] = Field(default_factory=list)
    scope_summary: str
    profiles: RefinementExecutionProfiles
    validation_plan: RefinementValidationPlan
    execution_mode: Literal["dry_run", "staged"] = "dry_run"
    mutation_allowed: Literal[False] = False
    output_workspace: str | None = None
    staging_area: str | None = None
    generated_files_changed: Literal[False] = False
    human_review_required: bool = False
    next_step: str
    warnings: list[str] = Field(default_factory=list)
    app_context_summary: AppContextSummary | None = None
    context_policy_decision: AppContextPolicyResult | None = None
    app_context_policy_override: AppContextPolicyOverride | None = None
    app_context_impact_hints: AppContextImpactHints | None = None

    @property
    def artifact_kind(self) -> str:
        """Prior-api alias for build_family."""
        return self.build_family


class RefinementDryRunPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: str
    artifact_kind: str
    change_class: str
    refinement_lane: str | None = None
    workflow_id: str
    workflow_sequence: str
    target_workflow: str
    affected_workflows: list[str] = Field(default_factory=list)
    affected_declarative_families: list[str] = Field(default_factory=list)
    affected_bundle_paths: list[str] = Field(default_factory=list)
    scope_summary: str
    profiles: RefinementDryRunProfiles
    execution_mode: Literal["dry_run"] = "dry_run"
    mutation_allowed: Literal[False] = False
    generated_files_changed: Literal[False] = False
    next_step: str
    warnings: list[str] = Field(default_factory=list)
    app_context_summary: AppContextSummary | None = None
    context_policy_decision: AppContextPolicyResult | None = None
    app_context_policy_override: AppContextPolicyOverride | None = None
    app_context_impact_hints: AppContextImpactHints | None = None


class _DeterministicChangeClassifier:
    def __init__(self, *, change_class: str | None = None) -> None:
        self._change_class = change_class
        self.calls: list[dict[str, Any]] = []

    async def classify(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        request = str(kwargs.get("raw_user_request") or "")
        change_class = self._change_class or infer_change_class(request)
        return SimpleNamespace(
            change_class=change_class,
            rationale=f"Dry-run deterministic classification: {change_class}.",
            confidence=1.0,
            signals=[f"dry_run_{change_class}"],
        )


def neutral_manifest() -> list[dict[str, Any]]:
    return [
        {"path": "app.json"},
        {"path": "config/shell.json"},
        {"path": "data/contract.json"},
        {"path": "data/migrations/001_initial.json"},
        {"path": "config/integrations.json"},
        {"path": "docs/integrations.md"},
        {"path": "services/integrations/analytics_provider_client.py"},
        {"path": "services/integrations/managed_analytics_client.py"},
        {"path": "modules/projects/module.yaml"},
        {"path": "modules/projects/contracts/events.yaml"},
        {"path": "modules/projects/backend/handler.py"},
        {"path": "modules/projects/backend/service.py"},
        {"path": "modules/projects/backend/repo.py"},
        {"path": "modules/projects/backend/policy.py"},
        {"path": "modules/projects/backend/schemas.py"},
        {"path": "modules/reports/module.yaml"},
        {
            "path": "modules/reports/backend/service.py",
            "content": "CONNECTOR_ID = 'analytics_provider'\n",
        },
        {
            "path": "modules/reports/backend/schemas.py",
            "content": "connector_id = 'analytics_provider'\n",
        },
        {"path": "modules/analytics_dashboard/module.yaml"},
        {"path": "modules/analytics_dashboard/contracts/events.yaml"},
        {"path": "modules/analytics_dashboard/backend/handler.py"},
        {"path": "modules/analytics_dashboard/backend/service.py"},
        {"path": "modules/analytics_dashboard/backend/schemas.py"},
        {
            "path": "ui/pages/dashboard.yaml",
            "content": "api_endpoint: /api/modules/projects/list_projects\n",
        },
        {
            "path": "ui/pages/reports.yaml",
            "content": "api_endpoint: /api/modules/reports/get_reports\n",
        },
        {
            "path": "ui/pages/analytics.yaml",
            "content": "api_endpoint: /api/modules/analytics_dashboard/get_metrics\n",
        },
        {"path": "ui/route_manifest.json"},
        {"path": "ui/index.js"},
    ]


def load_manifest_file(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    raw_manifest = data.get("files_manifest") if isinstance(data, dict) else data
    if not isinstance(raw_manifest, list):
        raise ValueError("Manifest file must be a JSON list or an object with files_manifest.")
    entries: list[dict[str, Any]] = []
    for entry in raw_manifest:
        if isinstance(entry, str):
            entries.append({"path": entry})
        elif isinstance(entry, dict) and entry.get("path"):
            entries.append(dict(entry, path=str(entry["path"])))
        else:
            raise ValueError("Manifest entries must be strings or objects with a path field.")
    return entries


def infer_change_class(request: str) -> str:
    text = str(request or "").lower()
    if any(term in text for term in ("target customer", "business model", "value proposition", "reframe")):
        return ChangeClass.CORE.value
    if any(term in text for term in ("dashboard experience", "display", "layout", "navigation", "theme")):
        return ChangeClass.DESIGN.value
    if any(term in text for term in ("required", "migrate", "migration", "new field", "add a field")):
        return ChangeClass.FEATURE.value
    if any(term in text for term in ("add an", "add a", "new action", "api action", "module api")):
        return ChangeClass.FEATURE.value
    if any(term in text for term in ("connector", "sync behavior", "adapter")):
        return ChangeClass.PATCH.value
    return ChangeClass.PATCH.value


def infer_refinement_lane(
    *,
    request: str,
    change_class: str,
    workflow_sequence: str,
    affected_bundle_paths: list[str],
    scope_summary: str,
) -> str:
    path_text = " ".join(affected_bundle_paths).lower()
    request_text = str(request or "").lower()
    text = " ".join([request_text, scope_summary, path_text]).lower()
    if (
        "destructive changes require explicit review" in text
        or any(term in path_text for term in ("data_contract", "data_migrations"))
        or any(term in request_text for term in ("data model", "database", "migration", "migrate", "required field"))
    ):
        return "data_model_migration"
    if re.search(r"(?<![a-z0-9])managed(?![a-z0-9])", text) or "provider-backed" in text:
        return "managed_capability_change"
    if any(term in text for term in ("connector", "integration", "adapter")):
        return "integration"
    if change_class == ChangeClass.CORE.value:
        if "architecture" in text:
            return "architecture_replan"
        return "conceptual_reframe"
    if workflow_sequence == "app_surface_revision" or "experience_spec" in text:
        return "experience_design"
    if any(term in text for term in ("module", "api", "action")):
        return "feature_addition"
    return "ui_patch"


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


def _stable_request_id(
    *,
    app_id: str | None,
    request: str,
    artifact_kind: str,
    change_class: str,
    workflow_sequence: str,
    affected_bundle_paths: list[str],
) -> str:
    payload = {
        "app_id": app_id or "",
        "artifact_kind": artifact_kind,
        "change_class": change_class,
        "request": request,
        "workflow_sequence": workflow_sequence,
        "affected_bundle_paths": sorted(affected_bundle_paths),
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    return f"refine_{digest}"


def _staging_area_for(
    *,
    app_id: str | None,
    request_id: str,
    staging_base_path: Path | None,
) -> str:
    safe_app_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(app_id or "local_app")).strip("_") or "local_app"
    base = staging_base_path or Path("generated") / "refinement_staging"
    return (base / safe_app_id / request_id).as_posix()


def _path_text(paths: list[str]) -> str:
    return " ".join(str(path or "").replace("\\", "/").lower() for path in paths)


def _has_ui_surface_impact(*, lane: str | None, paths: list[str], families: list[str]) -> bool:
    text = _path_text(paths)
    return (
        lane in {"experience_design", "ui_patch", "managed_capability_change"}
        or "experience_spec" in families
        or "ui/" in text
        or "route_manifest" in text
        or "shell.json" in text
    )


def _has_module_contract_impact(*, lane: str | None, paths: list[str]) -> bool:
    text = _path_text(paths)
    return (
        lane in {"feature_addition", "managed_capability_change"}
        or "modules/" in text
        or "/contracts/" in text
        or "/backend/handler.py" in text
        or "/backend/service.py" in text
    )


def _has_integration_impact(*, lane: str | None, paths: list[str], scope_summary: str) -> bool:
    text = " ".join([_path_text(paths), str(scope_summary or "").lower()])
    return lane == "integration" or any(term in text for term in ("integration", "integrations", "connector", "adapter"))


def _has_database_review_impact(
    *,
    lane: str | None,
    request: str,
    paths: list[str],
    scope_summary: str,
) -> bool:
    request_text = str(request or "").lower()
    paths_text = _path_text(paths)
    scope_text = str(scope_summary or "").lower()
    return (
        lane == "data_model_migration"
        or any(term in paths_text for term in ("data_contract", "data_migrations"))
        or "destructive changes require explicit review" in scope_text
        or any(
            term in request_text
            for term in (
                "data model",
                "database",
                "required field",
                "migrate",
                "migration",
            )
        )
    )


def _has_managed_facade_impact(*, lane: str | None, paths: list[str], scope_summary: str) -> bool:
    text = " ".join([_path_text(paths), str(scope_summary or "").lower()])
    return (
        lane == "managed_capability_change"
        or re.search(r"(?<![a-z0-9])managed(?![a-z0-9])", text) is not None
        or "provider-backed" in text
    )


def build_validation_plan(
    *,
    request: str,
    refinement_lane: str | None,
    affected_bundle_paths: list[str],
    affected_declarative_families: list[str],
    scope_summary: str,
) -> RefinementValidationPlan:
    ui_required = _has_ui_surface_impact(
        lane=refinement_lane,
        paths=affected_bundle_paths,
        families=affected_declarative_families,
    )
    module_required = _has_module_contract_impact(lane=refinement_lane, paths=affected_bundle_paths)
    integration_required = _has_integration_impact(
        lane=refinement_lane,
        paths=affected_bundle_paths,
        scope_summary=scope_summary,
    )
    database_required = _has_database_review_impact(
        lane=refinement_lane,
        request=request,
        paths=affected_bundle_paths,
        scope_summary=scope_summary,
    )
    managed_facade_required = _has_managed_facade_impact(
        lane=refinement_lane,
        paths=affected_bundle_paths,
        scope_summary=scope_summary,
    )

    items = [
        RefinementValidationItem(
            id="route_component_validation",
            label="Route/component validation",
            required=ui_required,
            reason="Required when refinement touches UI routes, pages, or experience surfaces.",
        ),
        RefinementValidationItem(
            id="ui_theme_primitive_validation",
            label="UI theme/primitive validation",
            required=ui_required,
            reason="Required when refinement touches page schema, route manifest, or design surfaces.",
        ),
        RefinementValidationItem(
            id="module_contract_validation",
            label="Module contract validation",
            required=module_required,
            reason="Required when refinement touches module manifests, contracts, or backend handlers.",
        ),
    ]
    if integration_required:
        items.append(
            RefinementValidationItem(
                id="integration_readiness",
                label="Integration readiness validation",
                required=True,
                reason="Required when connector, adapter, or integration files are in scope.",
            )
        )
    if database_required:
        items.append(
            RefinementValidationItem(
                id="database_migration_review",
                label="Data migration review",
                required=True,
                reason="Required when data contract, migrations, or destructive data changes are in scope.",
            )
        )
    if managed_facade_required:
        items.append(
            RefinementValidationItem(
                id="managed_facade_validation",
                label="Managed capability facade validation",
                required=True,
                reason="Required when managed capability adapter, facade module, or page paths are in scope.",
            )
        )
    return RefinementValidationPlan(items=items)


def requires_human_review(
    *,
    change_class: str,
    refinement_lane: str | None,
    validation_plan: RefinementValidationPlan,
    scope_summary: str,
) -> bool:
    validation_ids = {item.id for item in validation_plan.items if item.required}
    return (
        change_class == ChangeClass.CORE.value
        or refinement_lane == "data_model_migration"
        or "database_migration_review" in validation_ids
        or "Destructive changes require explicit review." in scope_summary
    )


def resolve_execution_profiles(
    *,
    config: ControlPlaneConfig,
    requires_replanning: bool,
) -> RefinementExecutionProfiles:
    classifier = str(config.classifier.llm_profile or "raw_llm_config")
    planner_replanner = (
        "planner_replanner" if requires_replanning and "planner_replanner" in config.llm_profiles else None
    )
    codegen = str(config.coding.llm_profile or "raw_llm_config") if config.coding.enabled else None
    reviewer_validator = "reviewer_validator" if "reviewer_validator" in config.llm_profiles else None
    return RefinementExecutionProfiles(
        classifier=classifier,
        planner_replanner=planner_replanner,
        codegen=codegen,
        reviewer_validator=reviewer_validator,
    )


def resolve_dry_run_profiles(
    *,
    config: ControlPlaneConfig,
    requires_replanning: bool,
) -> RefinementDryRunProfiles:
    profiles = resolve_execution_profiles(config=config, requires_replanning=requires_replanning)
    return RefinementDryRunProfiles(
        classifier=profiles.classifier,
        planner_or_codegen=profiles.planner_replanner or profiles.codegen,
        reviewer_validator=profiles.reviewer_validator,
    )


def recommend_next_step(
    *,
    change_class: str,
    workflow_sequence: str,
    scope_summary: str,
) -> str:
    if "Destructive changes require explicit review." in scope_summary:
        return "needs_human_review"
    if change_class == ChangeClass.CORE.value or workflow_sequence in {"full_rebuild", "conceptual_replan"}:
        return "full_rebuild"
    if workflow_sequence == "app_surface_revision":
        return "app_surface_revision"
    if workflow_sequence == "app_revision" and change_class == ChangeClass.PATCH.value:
        return "scoped_patch_candidate"
    if workflow_sequence == "app_revision":
        return "app_revision"
    return workflow_sequence or "needs_human_review"


def path_has_secret_marker(path: str) -> bool:
    normalized = str(path or "").replace("\\", "/").lower()
    parts = [part for part in normalized.split("/") if part]
    return any(term in normalized for term in SECRET_PATH_TERMS) or any(part == ".env" for part in parts)


def _app_context_impact_warnings(hints: AppContextImpactHints | None) -> list[str]:
    if hints is None:
        return []
    warnings: list[str] = []
    if hints.stale_graph_warning:
        warnings.append(hints.stale_graph_warning)
    if not hints.available:
        warnings.extend(hints.explanations)
    return warnings


def build_refinement_execution_plan_from_route(
    *,
    request: str,
    artifact_kind: str,
    change_class: str,
    workflow_id: str,
    workflow_sequence: str | None,
    affected_workflows: list[str] | None = None,
    affected_declarative_families: list[str] | None = None,
    affected_bundle_paths: list[str] | None = None,
    scope_summary: str = "",
    requires_replanning: bool = False,
    config: ControlPlaneConfig | None = None,
    profiles: RefinementExecutionProfiles | dict[str, Any] | None = None,
    app_id: str | None = None,
    request_id: str | None = None,
    execution_mode: Literal["dry_run", "staged"] = "dry_run",
    staging_base_path: Path | None = None,
    warnings: list[str] | None = None,
    app_context_summary: AppContextSummary | dict[str, Any] | None = None,
    app_context_graph: Any | None = None,
    app_context_graph_warnings: list[str] | None = None,
    app_context_impact_hints: AppContextImpactHints | dict[str, Any] | None = None,
    attach_app_context_impact_hints: bool = False,
) -> RefinementExecutionPlan:
    normalized_change_class = str(change_class or "").strip().lower()
    if normalized_change_class not in VALID_CHANGE_CLASSES:
        allowed = ", ".join(sorted(VALID_CHANGE_CLASSES))
        raise ValueError(f"Invalid change_class '{change_class}'. Allowed: {allowed}.")
    if execution_mode not in {"dry_run", "staged"}:
        raise ValueError("execution_mode must be 'dry_run' or 'staged'.")

    resolved_workflow_sequence = str(workflow_sequence or "").strip()
    paths = _dedupe([str(path or "").replace("\\", "/") for path in affected_bundle_paths or []])
    families = _dedupe(list(affected_declarative_families or []))
    workflows = _dedupe(list(affected_workflows or []))
    refinement_lane = infer_refinement_lane(
        request=request,
        change_class=normalized_change_class,
        workflow_sequence=resolved_workflow_sequence,
        affected_bundle_paths=paths,
        scope_summary=scope_summary,
    )
    if isinstance(profiles, RefinementExecutionProfiles):
        resolved_profiles = profiles
    elif profiles is not None:
        resolved_profiles = RefinementExecutionProfiles.model_validate(profiles)
    else:
        resolved_profiles = resolve_execution_profiles(
            config=config or ControlPlaneConfig(),
            requires_replanning=requires_replanning,
        )
    validation_plan = build_validation_plan(
        request=request,
        refinement_lane=refinement_lane,
        affected_bundle_paths=paths,
        affected_declarative_families=families,
        scope_summary=scope_summary,
    )
    human_review_required = requires_human_review(
        change_class=normalized_change_class,
        refinement_lane=refinement_lane,
        validation_plan=validation_plan,
        scope_summary=scope_summary,
    )
    resolved_request_id = request_id or _stable_request_id(
        app_id=app_id,
        request=request,
        artifact_kind=artifact_kind,
        change_class=normalized_change_class,
        workflow_sequence=resolved_workflow_sequence,
        affected_bundle_paths=paths,
    )
    plan_warnings = _dedupe(list(warnings or []))
    if execution_mode == "dry_run" and DRY_RUN_NOTICE not in plan_warnings:
        plan_warnings.insert(0, DRY_RUN_NOTICE)
    if any(path_has_secret_marker(path) for path in paths):
        plan_warnings.append("Secret-bearing path hints were detected in the dry-run output.")
    if "Destructive changes require explicit review." in scope_summary:
        plan_warnings.append("Destructive changes require explicit review.")
    if any(item.id == "database_migration_review" and item.required for item in validation_plan.items):
        plan_warnings.append("Data migration review is required before mutation.")
    if human_review_required:
        plan_warnings.append("Human review is required before mutation.")
    resolved_app_context_summary = (
        app_context_summary
        if isinstance(app_context_summary, AppContextSummary)
        else AppContextSummary.model_validate(app_context_summary)
        if app_context_summary is not None
        else None
    )
    resolved_app_context_impact_hints = (
        app_context_impact_hints
        if isinstance(app_context_impact_hints, AppContextImpactHints)
        else AppContextImpactHints.model_validate(app_context_impact_hints)
        if app_context_impact_hints is not None
        else derive_app_context_impact_hints(
            app_context_graph=app_context_graph,
            app_context_summary=resolved_app_context_summary,
            request=request,
            affected_bundle_paths=paths,
            change_class=normalized_change_class,
            refinement_lane=refinement_lane,
        )
        if attach_app_context_impact_hints
        else None
    )
    plan_warnings.extend(app_context_warnings(resolved_app_context_summary))
    context_policy_decision = evaluate_app_context_policy(
        app_context_summary=resolved_app_context_summary,
        change_class=normalized_change_class,
        refinement_lane=refinement_lane,
        affected_bundle_paths=paths,
    )
    context_policy_decision = enrich_app_context_policy_with_graph_hints(
        context_policy_decision,
        resolved_app_context_impact_hints,
    )
    plan_warnings.extend(context_policy_decision.warnings)
    plan_warnings.extend(app_context_graph_warnings or [])
    plan_warnings.extend(_app_context_impact_warnings(resolved_app_context_impact_hints))
    staging_area = None
    if execution_mode == "staged":
        staging_area = _staging_area_for(
            app_id=app_id,
            request_id=resolved_request_id,
            staging_base_path=staging_base_path,
        )
        plan_warnings.append("Staged mode reserves a metadata path only; no app files are copied or mutated.")

    return RefinementExecutionPlan(
        request_id=resolved_request_id,
        app_id=app_id,
        request=request,
        artifact_kind=artifact_kind,
        change_class=normalized_change_class,
        refinement_lane=refinement_lane,
        workflow_id=workflow_id,
        workflow_sequence=resolved_workflow_sequence,
        target_workflow=workflow_id,
        affected_workflows=workflows,
        affected_declarative_families=families,
        affected_bundle_paths=paths,
        scope_summary=scope_summary,
        profiles=resolved_profiles,
        validation_plan=validation_plan,
        execution_mode=execution_mode,
        output_workspace=staging_area,
        staging_area=staging_area,
        generated_files_changed=False,
        human_review_required=human_review_required,
        next_step=recommend_next_step(
            change_class=normalized_change_class,
            workflow_sequence=resolved_workflow_sequence,
            scope_summary=scope_summary,
        ),
        warnings=_dedupe(plan_warnings),
        app_context_summary=resolved_app_context_summary,
        context_policy_decision=context_policy_decision,
        app_context_impact_hints=resolved_app_context_impact_hints,
    )


async def build_refinement_execution_plan(
    *,
    request: str,
    artifact_kind: str = "app_bundle",
    change_class: str | None = None,
    files_manifest: list[dict[str, Any]] | None = None,
    app_root: Path | None = None,
    app_id: str | None = None,
    request_id: str | None = None,
    execution_mode: Literal["dry_run", "staged"] = "dry_run",
    staging_base_path: Path | None = None,
    live_classifier: bool = False,
    source_surface: str = "manual_refinement_dry_run",
) -> RefinementExecutionPlan:
    normalized_change_class = str(change_class or "").strip().lower() or None
    if normalized_change_class is not None and normalized_change_class not in VALID_CHANGE_CLASSES:
        allowed = ", ".join(sorted(VALID_CHANGE_CLASSES))
        raise ValueError(f"Invalid change_class '{change_class}'. Allowed: {allowed}.")

    resolved_app_root = app_root.resolve() if app_root is not None else None
    control_plane_config = load_control_plane_config(resolved_app_root)

    def pack_loader():
        return load_refinement_harness(app_root=resolved_app_root)

    classifier = (
        LLMChangeClassifier(
            config_loader=lambda: control_plane_config,
            pack_loader=pack_loader,
        )
        if live_classifier
        else _DeterministicChangeClassifier(change_class=normalized_change_class)
    )
    resolver = RefinementTriggerRouteResolver(classifier=classifier, pack_loader=pack_loader)
    manifest = files_manifest if files_manifest is not None else neutral_manifest()
    refinement_request = resolver.request_from_payload(
        payload={
            "refinement_request": {
                "artifact_kind": artifact_kind,
                "artifact_key": artifact_kind,
                "raw_user_request": request,
                "source_surface": source_surface,
                "extra": {"files_manifest": manifest},
            }
        },
        app_id=app_id,
        requested_workflow_id="AppGenerator",
    )
    if refinement_request is None:
        raise RuntimeError("Could not build a refinement request for dry-run planning.")

    decision = await resolver.route(refinement_request)
    affected_paths = list(decision.impact_set.affected_bundle_paths)
    warnings = [DRY_RUN_NOTICE]
    if any(path_has_secret_marker(path) for path in affected_paths):
        warnings.append("Secret-bearing path hints were detected in the dry-run output.")
    if "Destructive changes require explicit review." in decision.impact_set.scope_summary:
        warnings.append("Destructive changes require explicit review.")
    if live_classifier:
        warnings.append("Live classifier was used for this dry run; no workflows were executed.")

    change_class_value = decision.change_intent.change_class.value
    workflow_sequence = decision.workflow_sequence or decision.impact_set.workflow_sequence or ""
    app_context_summary = await get_current_app_context_summary(app_id=app_id)
    app_context_graph_lookup = await get_current_app_context_graph(
        app_id=app_id,
        app_context_summary=app_context_summary,
    )
    return build_refinement_execution_plan_from_route(
        request=request,
        artifact_kind=refinement_request.artifact_kind,
        change_class=change_class_value,
        workflow_id=decision.workflow_id,
        workflow_sequence=workflow_sequence,
        affected_workflows=list(decision.impact_set.affected_workflows),
        affected_declarative_families=list(decision.impact_set.affected_declarative_families),
        affected_bundle_paths=affected_paths,
        scope_summary=decision.impact_set.scope_summary,
        requires_replanning=decision.impact_set.requires_replanning,
        config=control_plane_config,
        app_id=app_id,
        request_id=request_id,
        execution_mode=execution_mode,
        staging_base_path=staging_base_path,
        warnings=warnings,
        app_context_summary=app_context_summary,
        app_context_graph=app_context_graph_lookup.graph,
        app_context_graph_warnings=app_context_graph_lookup.warnings,
        attach_app_context_impact_hints=True,
    )


async def build_refinement_dry_run_plan(
    *,
    request: str,
    artifact_kind: str = "app_bundle",
    change_class: str | None = None,
    files_manifest: list[dict[str, Any]] | None = None,
    app_root: Path | None = None,
    app_id: str | None = None,
    live_classifier: bool = False,
    source_surface: str = "manual_refinement_dry_run",
) -> RefinementDryRunPlan:
    execution_plan = await build_refinement_execution_plan(
        request=request,
        artifact_kind=artifact_kind,
        change_class=change_class,
        files_manifest=files_manifest,
        app_root=app_root,
        app_id=app_id,
        live_classifier=live_classifier,
        source_surface=source_surface,
    )
    return RefinementDryRunPlan(
        request=request,
        artifact_kind=execution_plan.artifact_kind,
        change_class=execution_plan.change_class,
        refinement_lane=execution_plan.refinement_lane,
        workflow_id=execution_plan.workflow_id,
        workflow_sequence=execution_plan.workflow_sequence,
        target_workflow=execution_plan.target_workflow,
        affected_workflows=list(execution_plan.affected_workflows),
        affected_declarative_families=list(execution_plan.affected_declarative_families),
        affected_bundle_paths=list(execution_plan.affected_bundle_paths),
        scope_summary=execution_plan.scope_summary,
        profiles=RefinementDryRunProfiles(
            classifier=execution_plan.profiles.classifier,
            planner_or_codegen=execution_plan.profiles.planner_replanner or execution_plan.profiles.codegen,
            reviewer_validator=execution_plan.profiles.reviewer_validator,
        ),
        next_step=execution_plan.next_step,
        warnings=list(execution_plan.warnings),
        app_context_summary=execution_plan.app_context_summary,
        context_policy_decision=execution_plan.context_policy_decision,
        app_context_policy_override=execution_plan.app_context_policy_override,
        app_context_impact_hints=execution_plan.app_context_impact_hints,
    )


__all__ = [
    "DRY_RUN_NOTICE",
    "RefinementDryRunPlan",
    "RefinementDryRunProfiles",
    "RefinementExecutionPlan",
    "RefinementExecutionProfiles",
    "RefinementValidationItem",
    "RefinementValidationPlan",
    "build_refinement_execution_plan",
    "build_refinement_execution_plan_from_route",
    "build_validation_plan",
    "build_refinement_dry_run_plan",
    "infer_change_class",
    "infer_refinement_lane",
    "load_manifest_file",
    "neutral_manifest",
    "path_has_secret_marker",
    "recommend_next_step",
    "resolve_dry_run_profiles",
    "resolve_execution_profiles",
    "requires_human_review",
]

