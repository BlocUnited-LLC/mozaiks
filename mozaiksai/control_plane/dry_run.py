from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from mozaiksai.control_plane.config import ControlPlaneConfig, load_control_plane_config
from mozaiksai.control_plane.implementations.change_classifier import LLMChangeClassifier
from mozaiksai.control_plane.implementations.refinement_router import (
    ChangeClass,
    RefinementTriggerRouteResolver,
)
from mozaiksai.control_plane.loader import load_control_plane_pack

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
        {"path": "config/database_intent.json"},
        {"path": "config/database_migrations/001_initial.json"},
        {"path": "config/integrations.json"},
        {"path": "docs/integrations.md"},
        {"path": "backend/integrations/analytics_provider_client.py"},
        {"path": "backend/integrations/hosted_analytics_client.py"},
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
    text = " ".join([request, scope_summary, " ".join(affected_bundle_paths)]).lower()
    if "destructive changes require explicit review" in text or any(
        term in text for term in ("database_intent", "database_migrations", "migration")
    ):
        return "data_model_migration"
    if re.search(r"(?<![a-z0-9])hosted(?![a-z0-9])", text):
        return "hosted_capability_change"
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


def resolve_dry_run_profiles(
    *,
    config: ControlPlaneConfig,
    requires_replanning: bool,
) -> RefinementDryRunProfiles:
    classifier = str(config.classifier.llm_profile or "raw_llm_config")
    planner_or_codegen: str | None = None
    if requires_replanning and "planner_replanner" in config.llm_profiles:
        planner_or_codegen = "planner_replanner"
    elif config.coding.enabled:
        planner_or_codegen = str(config.coding.llm_profile or "raw_llm_config")
    reviewer_validator = "reviewer_validator" if "reviewer_validator" in config.llm_profiles else None
    return RefinementDryRunProfiles(
        classifier=classifier,
        planner_or_codegen=planner_or_codegen,
        reviewer_validator=reviewer_validator,
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


async def build_refinement_dry_run_plan(
    *,
    request: str,
    artifact_kind: str = "app_bundle",
    change_class: str | None = None,
    files_manifest: list[dict[str, Any]] | None = None,
    app_root: Path | None = None,
    live_classifier: bool = False,
    source_surface: str = "manual_refinement_dry_run",
) -> RefinementDryRunPlan:
    normalized_change_class = str(change_class or "").strip().lower() or None
    if normalized_change_class is not None and normalized_change_class not in VALID_CHANGE_CLASSES:
        allowed = ", ".join(sorted(VALID_CHANGE_CLASSES))
        raise ValueError(f"Invalid change_class '{change_class}'. Allowed: {allowed}.")

    resolved_app_root = app_root.resolve() if app_root is not None else None
    control_plane_config = load_control_plane_config(resolved_app_root)

    def pack_loader():
        return load_control_plane_pack(app_root=resolved_app_root)

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
    return RefinementDryRunPlan(
        request=request,
        artifact_kind=refinement_request.artifact_kind,
        change_class=change_class_value,
        refinement_lane=infer_refinement_lane(
            request=request,
            change_class=change_class_value,
            workflow_sequence=workflow_sequence,
            affected_bundle_paths=affected_paths,
            scope_summary=decision.impact_set.scope_summary,
        ),
        workflow_id=decision.workflow_id,
        workflow_sequence=workflow_sequence,
        target_workflow=decision.workflow_id,
        affected_workflows=list(decision.impact_set.affected_workflows),
        affected_declarative_families=list(decision.impact_set.affected_declarative_families),
        affected_bundle_paths=affected_paths,
        scope_summary=decision.impact_set.scope_summary,
        profiles=resolve_dry_run_profiles(
            config=control_plane_config,
            requires_replanning=decision.impact_set.requires_replanning,
        ),
        next_step=recommend_next_step(
            change_class=change_class_value,
            workflow_sequence=workflow_sequence,
            scope_summary=decision.impact_set.scope_summary,
        ),
        warnings=warnings,
    )


__all__ = [
    "DRY_RUN_NOTICE",
    "RefinementDryRunPlan",
    "RefinementDryRunProfiles",
    "build_refinement_dry_run_plan",
    "infer_change_class",
    "infer_refinement_lane",
    "load_manifest_file",
    "neutral_manifest",
    "path_has_secret_marker",
    "recommend_next_step",
    "resolve_dry_run_profiles",
]
