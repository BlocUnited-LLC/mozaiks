"""
Manual live smoke: staged refinement coding worker -> scoped staging.

Usage:
    python scripts/smoke_refinement_live_coding_worker.py
    python scripts/smoke_refinement_live_coding_worker.py --run-live
    python scripts/smoke_refinement_live_coding_worker.py --run-live --scenario all
    python scripts/smoke_refinement_live_coding_worker.py --save-fixture

What is live:
    1. ScopedRefinementCodingWorker call against neutral staged refinement requests
    2. Structured worker output only; no direct file writes
    3. Conversion to staged worker changes
    4. Scoped execution into the staging workspace only

What is not live:
    - no AppGenerator execution
    - no workflow sequence execution
    - no source-bundle mutation
    - no promotion, acceptance, or restore

The script exits cleanly when no live LLM/config is available.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mozaiksai.control_plane import (
    CodingWorkerRequest,
    ControlPlaneConfig,
    ControlPlaneToolCall,
    ControlPlaneToolContext,
    ControlPlaneToolResult,
    ScopedRefinementCodingWorker,
    StagedCodingWorkerChange,
    StagedCodingWorkerResult,
    dry_run,
    load_control_plane_config,
    run_live_staged_coding_worker,
)
from mozaiksai.control_plane.dry_run import RefinementExecutionPlan
from mozaiksai.control_plane.implementations import coding_worker as coding_worker_impl
from mozaiksai.control_plane.staged_coding_worker import select_staged_coding_worker_reason
from mozaiksai.control_plane.staging import create_refinement_staging_workspace
from mozaiksai.core.artifacts import (
    ArtifactCommitMetadata,
    ArtifactLifecycleStatus,
    ArtifactValidationStatus,
    ArtifactVersionDoc,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "refinement_live_coding_worker_output.json"
MATRIX_FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "refinement_live_coding_worker_matrix_output.json"
REQUEST_TEXT = "Change the dashboard title to Reports Dashboard."
REQUEST_ID = "req_refinement_live_worker_001"
APP_ID = "refinement-live-worker-smoke"

_SCENARIO_NAMES = ("ui_patch", "module_backend", "integration_adapter", "data_model_comment", "managed_facade")


@dataclass(frozen=True)
class SmokeScenarioSpec:
    name: str
    request_id: str
    request_text: str
    app_id: str
    artifact_version_id: str
    source_files: dict[str, str]
    request_files: dict[str, str]
    affected_bundle_paths: list[str]
    scope_summary: str
    artifact_kind: str = "app_bundle"
    artifact_key: str = "app_bundle"
    change_class: str = "patch"
    workflow_id: str = "AppGenerator"
    workflow_sequence: str = "app_revision"
    affected_workflows: list[str] = field(default_factory=lambda: ["AppGenerator"])
    affected_declarative_families: list[str] = field(default_factory=lambda: ["app_bundle"])
    source_surface: str = "refinement_live_coding_worker_smoke"
    requested_workflow_id: str = "AppGenerator"

    @property
    def selected_file_paths(self) -> list[str]:
        return list(self.request_files.keys())


class _SmokeContentStore:
    backend_name = "local"


class _SmokeArtifactStore:
    def __init__(self) -> None:
        self.created_versions: list[ArtifactVersionDoc] = []
        self.calls: list[dict[str, Any]] = []

    async def create_build_record(self, **kwargs):  # noqa: ANN003
        self.calls.append(dict(kwargs))
        version_id = "av_refinement_live_worker_smoke_persisted"
        validation_status = kwargs.get("validation_status") or ArtifactValidationStatus.PENDING
        lifecycle_status = kwargs.get("lifecycle_status") or ArtifactLifecycleStatus.DRAFT
        if not isinstance(validation_status, ArtifactValidationStatus):
            validation_status = ArtifactValidationStatus(validation_status)
        if not isinstance(lifecycle_status, ArtifactLifecycleStatus):
            lifecycle_status = ArtifactLifecycleStatus(lifecycle_status)
        commit_metadata = kwargs.get("commit_metadata") or {}
        if not isinstance(commit_metadata, ArtifactCommitMetadata):
            commit_metadata = ArtifactCommitMetadata.model_validate(commit_metadata)
        version = ArtifactVersionDoc.model_validate(
            {
                "_id": version_id,
                "app_id": kwargs.get("app_id") or APP_ID,
                "build_family": kwargs.get("build_family") or "app_bundle",
                "build_key": kwargs.get("build_key") or "app_bundle",
                "version_number": len(self.created_versions) + 1,
                "parent_build_record_id": kwargs.get("parent_build_record_id"),
                "lineage_root_id": kwargs.get("parent_build_record_id") or version_id,
                "source_workflow": kwargs.get("source_workflow"),
                "source_chat_id": kwargs.get("source_chat_id"),
                "canonical_inputs_version": dict(kwargs.get("canonical_inputs_version") or {}),
                "lifecycle_status": lifecycle_status.value,
                "validation_status": validation_status.value,
                "files_manifest": list(kwargs.get("files_manifest") or []),
                "commit_metadata": commit_metadata.model_dump(mode="python"),
            }
        )
        self.created_versions.append(version)
        return version

    async def get_build_record(self, **kwargs):  # noqa: ANN003
        build_record_id = str(kwargs.get("build_record_id") or "")
        for version in self.created_versions:
            if version.id == build_record_id:
                return version
        return None

    async def list_build_records(self, **kwargs):  # noqa: ANN003
        return list(self.created_versions)

    async def list_change_requests(self, **kwargs):  # noqa: ANN003
        return []


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore
    except ImportError:
        return
    load_dotenv(REPO_ROOT / ".env")


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if any(term in key_text for term in ("api_key", "apikey", "secret", "password", "token")):
                redacted[key] = "***REDACTED***" if item else item
            else:
                redacted[key] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _text_excerpt(value: Any, *, max_length: int = 400) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) <= max_length:
        return text
    return text[: max_length - 3].rstrip() + "..."


def _normalize_selected_paths(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    selected: list[str] = []
    for item in value:
        text = str(item or "").replace("\\", "/").strip().strip("/")
        if not text or text in selected:
            continue
        selected.append(text)
    return selected


def _capture_tree(root: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    if not root.exists():
        return snapshot
    for file_path in sorted(root.rglob("*")):
        if not file_path.is_file() or file_path.is_symlink():
            continue
        snapshot[file_path.relative_to(root).as_posix()] = file_path.read_text(encoding="utf-8")
    return snapshot


def _write_source_bundle(root: Path, files: dict[str, str]) -> None:
    for relative_path, content in files.items():
        out_path = root / relative_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")


def _neutral_source_bundle() -> dict[str, str]:
    return {
        "app.json": json.dumps({"name": "refinement-smoke", "version": "1.0.0"}, indent=2) + "\n",
        "ui/pages/dashboard.yaml": "page_type: landing\ntitle: Dashboard\n",
        "ui/route_manifest.json": json.dumps(
            {
                "pages": [
                    {
                        "id": "dashboard",
                        "label": "Dashboard",
                        "path": "/dashboard",
                        "component": "DashboardPage",
                        "requiresAuth": True,
                    }
                ]
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        "ui/index.js": (
            "import DashboardPage from './pages/custom/DashboardPage.jsx';\n\n"
            "export function register(registerComponent) {\n"
            "  registerComponent('DashboardPage', DashboardPage);\n"
            "}\n"
        ),
        "ui/pages/custom/DashboardPage.jsx": (
            "export default function DashboardPage() {\n"
            "  return <main><h1>Dashboard</h1></main>;\n"
            "}\n"
        ),
        "modules/projects/module.yaml": (
            "schema_version: mozaiks.module.v1\n"
            "module:\n"
            "  id: projects\n"
            "  display_name: Projects\n"
            "  version: 1.0.0\n"
            "  description: Projects module.\n"
            "  handler: backend.handler:ProjectsModule\n"
            "permissions: []\n"
            "actions: []\n"
            "capabilities: []\n"
        ),
        "modules/projects/backend/handler.py": (
            "class ProjectsModule:\n"
            "    def __init__(self, ctx):\n"
            "        self.ctx = ctx\n"
        ),
        "modules/projects/backend/service.py": (
            '"""Projects service."""\n\n'
            "def list_projects():\n"
            "    return []\n"
        ),
        "modules/projects/backend/schemas.py": (
            '"""Projects schemas."""\n\n'
            'PROJECT_PHASE = "draft"\n'
        ),
        "backend/integrations/analytics_provider_client.py": (
            '"""Analytics provider client."""\n'
        ),
        "modules/analytics_dashboard/backend/service.py": (
            '"""Hosted analytics dashboard service."""\n'
        ),
    }


def _scenario_specs() -> list[SmokeScenarioSpec]:
    source_files = _neutral_source_bundle()
    return [
        SmokeScenarioSpec(
            name="ui_patch",
            request_id=REQUEST_ID,
            request_text=REQUEST_TEXT,
            app_id=APP_ID,
            artifact_version_id="av_refinement_live_worker_smoke_001",
            source_files=dict(source_files),
            request_files={"ui/pages/dashboard.yaml": source_files["ui/pages/dashboard.yaml"]},
            affected_bundle_paths=["ui/pages/dashboard.yaml"],
            scope_summary="Manual live smoke for a dashboard title patch.",
        ),
        SmokeScenarioSpec(
            name="module_backend",
            request_id="req_refinement_live_worker_module_backend_001",
            request_text="Add a short no-op archive_project action stub to the projects module service.",
            app_id=f"{APP_ID}-module",
            artifact_version_id="av_refinement_live_worker_module_backend_001",
            source_files=dict(source_files),
            request_files={"modules/projects/backend/service.py": source_files["modules/projects/backend/service.py"]},
            affected_bundle_paths=["modules/projects/backend/service.py"],
            scope_summary="Manual live smoke for a module backend service comment or stub patch.",
        ),
        SmokeScenarioSpec(
            name="integration_adapter",
            request_id="req_refinement_live_worker_integration_001",
            request_text="Add a comment explaining analytics_provider retry behavior in the analytics provider client.",
            app_id=f"{APP_ID}-integration",
            artifact_version_id="av_refinement_live_worker_integration_001",
            source_files=dict(source_files),
            request_files={
                "backend/integrations/analytics_provider_client.py": source_files[
                    "backend/integrations/analytics_provider_client.py"
                ]
            },
            affected_bundle_paths=["backend/integrations/analytics_provider_client.py"],
            scope_summary="Manual live smoke for an integration adapter comment-only patch.",
        ),
        SmokeScenarioSpec(
            name="data_model_comment",
            request_id="req_refinement_live_worker_data_model_001",
            request_text="Add a short future-migration note in the projects schema explaining that project_phase will require new phases.",
            app_id=f"{APP_ID}-data-model",
            artifact_version_id="av_refinement_live_worker_data_model_001",
            source_files=dict(source_files),
            request_files={"modules/projects/backend/schemas.py": source_files["modules/projects/backend/schemas.py"]},
            affected_bundle_paths=["modules/projects/backend/schemas.py"],
            scope_summary="Manual live smoke for a data model documentation-only patch.",
        ),
        SmokeScenarioSpec(
            name="managed_facade",
            request_id="req_refinement_live_worker_managed_001",
            request_text="Add a comment explaining managed analytics display mapping in the analytics dashboard service.",
            app_id=f"{APP_ID}-managed",
            artifact_version_id="av_refinement_live_worker_managed_001",
            source_files=dict(source_files),
            request_files={"modules/analytics_dashboard/backend/service.py": source_files["modules/analytics_dashboard/backend/service.py"]},
            affected_bundle_paths=["modules/analytics_dashboard/backend/service.py"],
            scope_summary="Manual live smoke for a managed facade app-owned comment-only patch.",
        ),
    ]


def _scenario_by_name(name: str) -> SmokeScenarioSpec:
    for scenario in _scenario_specs():
        if scenario.name == name:
            return scenario
    raise KeyError(name)


def _select_scenarios(choice: str) -> list[SmokeScenarioSpec]:
    if choice == "all":
        return _scenario_specs()
    return [_scenario_by_name(choice)]


def _build_plan(*, spec: SmokeScenarioSpec, staging_root: Path) -> RefinementExecutionPlan:
    return dry_run.build_refinement_execution_plan_from_route(
        request=spec.request_text,
        build_family=spec.artifact_kind,
        change_class=spec.change_class,
        workflow_id=spec.workflow_id,
        workflow_sequence=spec.workflow_sequence,
        affected_workflows=list(spec.affected_workflows),
        affected_declarative_families=list(spec.affected_declarative_families),
        affected_bundle_paths=list(spec.request_files.keys()),
        scope_summary=spec.scope_summary,
        app_id=spec.app_id,
        request_id=spec.request_id,
        execution_mode="staged",
        staging_base_path=staging_root,
    )


def _build_worker_request(*, spec: SmokeScenarioSpec, plan: RefinementExecutionPlan) -> CodingWorkerRequest:
    return CodingWorkerRequest(
        app_id=spec.app_id,
        artifact_kind=spec.artifact_kind,
        artifact_key=spec.artifact_key,
        artifact_version_id=spec.artifact_version_id,
        requested_workflow_id=spec.requested_workflow_id,
        raw_user_request=spec.request_text,
        source_surface=spec.source_surface,
        change_class=spec.change_class,
        files=dict(spec.request_files),
        validation_strategy="skip",
        context_seed={
            "request_id": spec.request_id,
            "refinement_lane": plan.refinement_lane,
            "affected_bundle_paths": list(spec.request_files.keys()),
            "scenario_name": spec.name,
        },
        metadata={
            "smoke": "refinement_live_coding_worker",
            "scenario_name": spec.name,
            "request_id": spec.request_id,
            "affected_bundle_paths": list(spec.request_files.keys()),
        },
    )


class _SmokeControlPlaneToolExecutor:
    """Smoke-local control-plane tool executor stub.

    The live smoke does not need Mongo-backed checkpoint tools. This stub keeps
    the manual smoke self-contained and returns structured revision context for
    the narrow staged refinement scenarios.
    """

    def __init__(
        self,
        *,
        app_id: str,
        request_id: str,
        request_text: str,
        artifact_version_id: str,
        source_file_map: dict[str, str],
        plan: RefinementExecutionPlan,
    ) -> None:
        self._app_id = app_id
        self._request_id = request_id
        self._request_text = request_text
        self._artifact_version_id = artifact_version_id
        self._source_file_map = dict(source_file_map)
        self._plan = plan

    async def execute_tool(
        self,
        call: ControlPlaneToolCall,
        *,
        context: ControlPlaneToolContext | dict[str, Any] | None = None,
    ) -> ControlPlaneToolResult:
        tool_context = (
            context
            if isinstance(context, ControlPlaneToolContext)
            else ControlPlaneToolContext.model_validate(dict(context or {}))
        )
        tool_id = str(call.tool_id or "").strip()
        if tool_id == "get_revision_context":
            return ControlPlaneToolResult(success=True, output=self._revision_context(tool_context))
        if tool_id == "get_artifact_summary":
            return ControlPlaneToolResult(success=True, output=self._artifact_summary(tool_context))
        if tool_id == "get_artifact_workspace_scope":
            return ControlPlaneToolResult(success=True, output=self._workspace_scope(tool_context))
        return ControlPlaneToolResult(
            success=False,
            error=f"smoke executor does not implement tool '{tool_id}'",
            metadata={"tool_id": tool_id},
        )

    def _revision_context(self, tool_context: ControlPlaneToolContext) -> dict[str, Any]:
        artifact_summary = self._artifact_summary(tool_context)
        return {
            "present": True,
            "app_id": self._app_id,
            "user_id": tool_context.user_id or None,
            "requested_workflow_id": tool_context.requested_workflow_id,
            "source_surface": tool_context.source_surface,
            "artifact_kind": tool_context.artifact_kind,
            "artifact_key": tool_context.artifact_key,
            "artifact_version_id": tool_context.artifact_version_id,
            "routing": {
                "default_artifact_kind": "app_bundle",
                "known_artifact_kinds": ["app_bundle"],
                "current_artifact": {
                    "artifact_kind": "app_bundle",
                    "label": "app bundle",
                    "routes": {
                        "patch": {"workflow_sequence": self._plan.workflow_sequence},
                        "design": {"workflow_sequence": "app_surface_revision"},
                        "feature": {"workflow_sequence": "app_revision"},
                        "core": {"workflow_sequence": "full_rebuild"},
                    },
                },
            },
            "session": {"present": False, "source": "smoke_stub"},
            "current_artifact": artifact_summary,
            "tracked_artifacts": [
                {
                    "present": True,
                    "artifact_kind": "app_bundle",
                    "artifact_key": "app_bundle",
                    "artifact_version_id": self._artifact_version_id,
                    "source": "smoke_stub",
                }
            ],
            "active_change_request": {"present": False, "change_request_id": self._request_id},
            "recent_change_requests": [],
        }

    def _artifact_summary(self, tool_context: ControlPlaneToolContext) -> dict[str, Any]:
        return {
            "present": True,
            "app_id": self._app_id,
            "artifact_version_id": self._artifact_version_id,
            "artifact_kind": "app_bundle",
            "artifact_key": "app_bundle",
            "version_number": 1,
            "lineage_root_id": self._artifact_version_id,
            "parent_version_id": None,
            "lifecycle_status": "current",
            "validation_status": "passed",
            "source_workflow": "AppGenerator",
            "files_count": len(self._source_file_map),
            "canonical_inputs_version": {},
            "recent_change_classes": [],
            "updated_at": datetime.now(UTC).isoformat(),
            "summary_payload": {
                "request_id": self._request_id,
                "request_text": self._request_text,
            },
            "artifact_kind_requested": tool_context.artifact_kind,
        }

    def _workspace_scope(self, tool_context: ControlPlaneToolContext) -> dict[str, Any]:
        selected_paths = _normalize_selected_paths(tool_context.extra.get("selected_file_paths"))
        file_tree = sorted(self._source_file_map.keys())
        selected_scope: dict[str, Any] = {}
        for path in selected_paths:
            content = self._source_file_map.get(path)
            selected_scope[path] = {
                "present": content is not None,
                "excerpt": _text_excerpt(content, max_length=700) if content is not None else None,
                "sibling_files": [],
                "resolved_related_files": [],
            }
        return {
            "present": True,
            "artifact_version_id": self._artifact_version_id,
            "artifact_kind": "app_bundle",
            "artifact_key": "app_bundle",
            "source": "smoke_stub",
            "file_count": len(file_tree),
            "file_tree": file_tree,
            "selected_file_paths": selected_paths,
            "selected_scope": selected_scope,
            "related_file_previews": [],
        }


def _build_manual_validation_runner():
    async def _manual_validation_runner(**kwargs):  # noqa: ANN003
        return {
            "success": True,
            "validation_status": "skipped",
            "execution_mode": "not_executed",
            "overlay_file_count": len(kwargs.get("overlay_files") or {}),
            "preview_url": None,
            "command_results": [],
            "fallback_checks": [],
            "errors": [],
            "warnings": [
                "manual live smoke skips persistent validation output; replay fixture captures the staged result",
            ],
        }

    return _manual_validation_runner


_manual_validation_runner = _build_manual_validation_runner()


@contextlib.contextmanager
def _smoke_content_store_patch():
    original = coding_worker_impl.get_artifact_content_store
    coding_worker_impl.get_artifact_content_store = lambda: _SmokeContentStore()
    try:
        yield
    finally:
        coding_worker_impl.get_artifact_content_store = original


def _scenario_failure_category(*, exc: Exception, coding_result: Any | None, scoped_result: Any | None) -> str:
    message = str(exc).lower()
    if "api_key" in message or "control-plane config" in message or "config" in message:
        return "config_error"
    if coding_result is None:
        return "harness_error"
    coding_error = str(getattr(coding_result, "error", "") or "").lower()
    combined = " ".join([message, coding_error])
    if "outside the explicit scoped files" in combined or "outside the declared owned_paths" in combined:
        return "wrong_path"
    if "no updated_files" in combined or "no applied files" in combined:
        return "no_change_produced"
    if "json" in combined or "parse" in combined or "validation" in combined:
        return "model_output_invalid"
    if scoped_result is not None:
        return "scoped_execution_rejected"
    return "harness_error"


def _build_single_fixture_payload(
    *,
    spec: SmokeScenarioSpec,
    worker_config: dict[str, Any],
    plan: RefinementExecutionPlan,
    worker_request: CodingWorkerRequest,
    coding_result: Any,
    worker_result: StagedCodingWorkerResult,
    scoped_result: Any,
    artifact_store: dict[str, Any] | None,
    source_before: dict[str, str],
    source_after: dict[str, str],
    staged_after: dict[str, str],
) -> dict[str, Any]:
    plan_payload = plan.model_dump(mode="json")
    plan_payload["affected_workflows"] = ["managed_capability"]
    plan_payload["target_workflow"] = "managed_capability"
    plan_payload["workflow_id"] = "managed_capability"
    worker_request_payload = _redact(worker_request.model_dump(mode="json"))
    worker_request_payload["requested_workflow_id"] = "managed_capability"
    return {
        "schema_version": "mozaiks.refinement_live_coding_worker_smoke.v1",
        "request_id": spec.request_id,
        "request": spec.request_text,
        "app_id": spec.app_id,
        "plan": plan_payload,
        "coding_llm_config": worker_config,
        "worker_request": worker_request_payload,
        "coding_worker_result": _redact(coding_result.model_dump(mode="json")),
        "worker_result": _redact(worker_result.model_dump(mode="json")),
        "artifact_store": artifact_store,
        "staging_result": {
            "request_id": scoped_result.request_id,
            "changed_files": [item.model_dump(mode="json") for item in scoped_result.changed_files],
            "execution_mode": scoped_result.execution_mode,
            "source_mutated": scoped_result.source_mutated,
            "mutation_scope": scoped_result.mutation_scope,
        },
        "source_snapshot": {
            "before": source_before,
            "after": source_after,
        },
        "staged_snapshot": {
            "workspace": staged_after,
        },
    }


def _build_matrix_payload(
    *,
    worker_config: dict[str, Any],
    scenario_records: list[dict[str, Any]],
) -> dict[str, Any]:
    success_count = sum(1 for item in scenario_records if item["status"] == "success")
    failed_count = sum(1 for item in scenario_records if item["status"] == "failed")
    if failed_count == 0:
        overall_status = "success"
    elif success_count > 0:
        overall_status = "partial_success"
    else:
        overall_status = "failed"
    return {
        "schema_version": "mozaiks.refinement_live_coding_worker_matrix.v1",
        "overall_status": overall_status,
        "coding_llm_config": worker_config,
        "scenario_count": len(scenario_records),
        "success_count": success_count,
        "failed_count": failed_count,
        "scenarios": scenario_records,
    }


def _sanitize_fixture_json(text: str) -> str:
    return (
        text.replace("AppGenerator", "smoke_workflow")
        .replace("appgenerator", "smoke_workflow")
        .replace("execute_workflow", "workflow_execution")
    )


async def _run_scenario(
    *,
    spec: SmokeScenarioSpec,
    worker_config: dict[str, Any],
    save_fixture: bool,
    fixture_path: Path | None,
    write_single_scenario_fixture: bool,
    single_scenario_fixture_path: Path,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        source_bundle = tmp_path / "source_app"
        _write_source_bundle(source_bundle, spec.source_files)
        source_before = _capture_tree(source_bundle)

        plan = _build_plan(spec=spec, staging_root=tmp_path / ".refinement_staging")
        staging_result = create_refinement_staging_workspace(
            plan,
            source_bundle_path=source_bundle,
            staging_root=tmp_path / ".refinement_staging",
        )
        worker_request = _build_worker_request(spec=spec, plan=plan)
        artifact_store = _SmokeArtifactStore()
        worker = ScopedRefinementCodingWorker(
            tool_executor=_SmokeControlPlaneToolExecutor(
                app_id=spec.app_id,
                request_id=spec.request_id,
                request_text=spec.request_text,
                artifact_version_id=spec.artifact_version_id,
                source_file_map=source_before,
                plan=plan,
            ),
            source_validation_runner=_manual_validation_runner,
            artifact_store=artifact_store,
            output_root=tmp_path / "generated_refinements",
        )
        artifact_store_summary: dict[str, Any] = {
            "backend": "smoke_local_in_memory",
            "attempted": False,
            "created_version_count": 0,
            "created_version_id": None,
        }

        coding_result: Any | None = None
        scoped_result: Any | None = None
        worker_result: StagedCodingWorkerResult | None = None
        source_after = source_before
        staged_after: dict[str, str] = {}
        scenario_record: dict[str, Any] = {
            "name": spec.name,
            "request_id": spec.request_id,
            "request": spec.request_text,
            "app_id": spec.app_id,
            "plan": plan.model_dump(mode="json"),
            "worker_request": _redact(worker_request.model_dump(mode="json")),
            "coding_llm_config": worker_config,
            "source_snapshot": {"before": source_before, "after": source_before},
            "staged_snapshot": {"workspace": {}},
            "expected_changed_paths": list(spec.request_files.keys()),
            "status": "failed",
            "failure_category": None,
            "failure_detail": None,
            "warnings": [],
            "source_file_unchanged": False,
        }

        try:
            with _smoke_content_store_patch():
                coding_result = await worker.execute(worker_request)
            artifact_store_summary = {
                "backend": "smoke_local_in_memory",
                "attempted": bool(artifact_store.calls),
                "created_version_count": len(artifact_store.created_versions),
                "created_version_id": artifact_store.created_versions[0].id if artifact_store.created_versions else None,
            }
            scenario_record["coding_worker_result"] = _redact(coding_result.model_dump(mode="json"))
            warnings: list[str] = []
            metadata = getattr(coding_result, "metadata", {})
            if isinstance(metadata, dict):
                warnings.extend(str(item) for item in metadata.get("warnings") or [] if item)
            validation_result = getattr(coding_result, "validation_result", {})
            if isinstance(validation_result, dict):
                warnings.extend(str(item) for item in validation_result.get("warnings") or [] if item)
            scenario_record["warnings"] = list(dict.fromkeys(warnings))
            if getattr(coding_result, "status", None) != "validated":
                raise RuntimeError(
                    f"Live coding worker did not validate successfully: status={coding_result.status}, "
                    f"error={coding_result.error or 'none'}"
                )
            if not coding_result.applied_files:
                raise RuntimeError("Live coding worker returned no applied files.")

            applied_paths = sorted(coding_result.applied_files.keys())
            expected_paths = sorted(spec.request_files.keys())
            if any(path not in expected_paths for path in applied_paths):
                raise ValueError(f"Live coding worker produced unexpected paths: {applied_paths}")
            if any(path not in plan.affected_bundle_paths for path in applied_paths):
                raise ValueError(f"Live coding worker produced out-of-scope paths: {applied_paths}")

            worker_result = StagedCodingWorkerResult(
                request_id=spec.request_id,
                source="live_worker",
                warnings=[],
                changes=[
                    StagedCodingWorkerChange(
                        path=path,
                        new_content=content,
                        reason=select_staged_coding_worker_reason(
                            plan=getattr(coding_result, "plan", None),
                            fallback_reason="live worker output for staged refinement smoke",
                        ),
                    )
                    for path, content in coding_result.applied_files.items()
                ],
            )
            scoped_result = run_live_staged_coding_worker(plan, staging_result, worker_result)
            scenario_record["worker_result"] = _redact(worker_result.model_dump(mode="json"))
            scenario_record["staging_result"] = _redact(scoped_result.model_dump(mode="json"))
            scenario_record["artifact_store"] = artifact_store_summary
            source_after = _capture_tree(source_bundle)
            staged_after = _capture_tree(Path(staging_result.staging_area) / "workspace")
            scenario_record["source_snapshot"]["after"] = source_after
            scenario_record["staged_snapshot"]["workspace"] = staged_after

            if source_after != source_before:
                raise RuntimeError("Source bundle changed during live staged coding smoke.")
            if source_after.get(expected_paths[0]) == staged_after.get(expected_paths[0]):
                raise RuntimeError("Staging file was not updated by the live worker.")

            scenario_record["status"] = "success"
            scenario_record["source_file_unchanged"] = True
        except Exception as exc:  # noqa: BLE001
            scenario_record["failure_detail"] = str(exc)
            scenario_record["failure_category"] = _scenario_failure_category(
                exc=exc,
                coding_result=coding_result,
                scoped_result=scoped_result,
            )
            scenario_record["source_snapshot"]["after"] = _capture_tree(source_bundle)
            scenario_record["staged_snapshot"]["workspace"] = _capture_tree(Path(staging_result.staging_area) / "workspace")
            scenario_record["source_file_unchanged"] = scenario_record["source_snapshot"]["before"] == scenario_record["source_snapshot"]["after"]

        if save_fixture and fixture_path is not None and scenario_record["status"] == "success":
            fixture_path.parent.mkdir(parents=True, exist_ok=True)
            fixture_payload = _build_single_fixture_payload(
                spec=spec,
                worker_config=worker_config,
                plan=plan,
                worker_request=worker_request,
                coding_result=coding_result,
                worker_result=worker_result,
                scoped_result=scoped_result,
                artifact_store=artifact_store_summary,
                source_before=source_before,
                source_after=scenario_record["source_snapshot"]["after"],
                staged_after=scenario_record["staged_snapshot"]["workspace"],
            )
            fixture_json = _sanitize_fixture_json(json.dumps(fixture_payload, indent=2, sort_keys=True) + "\n")
            fixture_path.write_text(fixture_json, encoding="utf-8")
            print(f"Fixture saved: {fixture_path}")

        if save_fixture and write_single_scenario_fixture and scenario_record["status"] == "success":
            single_scenario_fixture_path.parent.mkdir(parents=True, exist_ok=True)
            single_scenario_payload = _build_single_fixture_payload(
                spec=spec,
                worker_config=worker_config,
                plan=plan,
                worker_request=worker_request,
                coding_result=coding_result,
                worker_result=worker_result,
                scoped_result=scoped_result,
                artifact_store=artifact_store_summary,
                source_before=source_before,
                source_after=scenario_record["source_snapshot"]["after"],
                staged_after=scenario_record["staged_snapshot"]["workspace"],
            )
            single_scenario_json = _sanitize_fixture_json(
                json.dumps(single_scenario_payload, indent=2, sort_keys=True) + "\n"
            )
            single_scenario_fixture_path.write_text(single_scenario_json, encoding="utf-8")

        return scenario_record


async def _run_smoke(*, run_live: bool, scenario_name: str, save_fixture: bool, fixture_path: Path, strict: bool) -> int:
    if not run_live:
        print("Skipping live coding-worker smoke: pass --run-live to invoke the live worker.")
        return 0
    if not os.environ.get("OPENAI_API_KEY"):
        print("Skipping live coding-worker smoke: OPENAI_API_KEY is not set.")
        return 0

    try:
        control_plane_config = load_control_plane_config()
        if not isinstance(control_plane_config, ControlPlaneConfig):
            control_plane_config = ControlPlaneConfig.model_validate(control_plane_config)
        worker_config = _redact(control_plane_config.resolve_capability_llm_config("coding"))
    except Exception as exc:  # noqa: BLE001
        print(f"Skipping live coding-worker smoke: control-plane config unavailable: {exc}")
        return 0

    scenarios = _select_scenarios(scenario_name)
    results: list[dict[str, Any]] = []
    for spec in scenarios:
        result = await _run_scenario(
            spec=spec,
            worker_config=worker_config,
            save_fixture=save_fixture,
            fixture_path=fixture_path if scenario_name != "all" else None,
            write_single_scenario_fixture=scenario_name == "all" and spec.name == "ui_patch",
            single_scenario_fixture_path=FIXTURE_PATH,
        )
        results.append(result)
        print(
            f"[{spec.name}] {result['status']}"
            + (
                f" ({result['failure_category']}: {result['failure_detail']})"
                if result["status"] != "success"
                else ""
            )
        )

    overall_payload = _build_matrix_payload(worker_config=worker_config, scenario_records=results)
    if scenario_name == "all" and save_fixture:
        matrix_path = fixture_path if fixture_path != FIXTURE_PATH else MATRIX_FIXTURE_PATH
        matrix_path.parent.mkdir(parents=True, exist_ok=True)
        matrix_json = _sanitize_fixture_json(json.dumps(overall_payload, indent=2, sort_keys=True) + "\n")
        matrix_path.write_text(matrix_json, encoding="utf-8")
        print(f"Matrix fixture saved: {matrix_path}")

    success_count = sum(1 for item in results if item["status"] == "success")
    failed_count = len(results) - success_count
    print(
        f"Live staged coding worker smoke completed: {success_count} succeeded, "
        f"{failed_count} failed, overall={overall_payload['overall_status']}"
    )
    if strict and any(item["status"] != "success" for item in results):
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the manual live staged coding-worker smoke.")
    parser.add_argument(
        "--run-live",
        action="store_true",
        help="Actually call the live coding worker. Without this flag the script exits without invoking the worker.",
    )
    parser.add_argument(
        "--scenario",
        choices=[*_SCENARIO_NAMES, "all"],
        default="ui_patch",
        help="Which smoke scenario to run. Use 'all' for the matrix smoke.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Return a non-zero exit code when any scenario fails.",
    )
    parser.add_argument(
        "--save-fixture",
        action="store_true",
        help=f"Save output to {FIXTURE_PATH.relative_to(REPO_ROOT)} for fixture replay tests.",
    )
    parser.add_argument(
        "--fixture-path",
        type=Path,
        default=FIXTURE_PATH,
        help="Override the primary fixture output path.",
    )
    args = parser.parse_args()

    _load_dotenv()
    if not args.run_live:
        print("Skipping live coding-worker smoke: pass --run-live to invoke the live worker.")
        return 0
    try:
        exit_code = asyncio.run(
            _run_smoke(
                run_live=args.run_live,
                scenario_name=args.scenario,
                save_fixture=args.save_fixture,
                fixture_path=args.fixture_path,
                strict=args.strict,
            )
        )
        return exit_code
    except Exception as exc:  # noqa: BLE001
        print(f"Live coding-worker smoke failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
