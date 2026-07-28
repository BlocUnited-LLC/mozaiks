"""Dogfood App Intelligence index -> scope -> coding-worker smoke.

This is a deterministic local smoke for proving the code-intelligence path
against a real workspace without requiring MongoDB, API keys, Playwright, or
source mutation.

Usage:
    python scripts/dogfood_context_graph_refinement.py
    python scripts/dogfood_context_graph_refinement.py --workspace C:/path/to/mozaiks-app --json
    python scripts/dogfood_context_graph_refinement.py --target-path app_generator/hosted_build_context.py
"""

from __future__ import annotations

# The script is intentionally runnable from a checkout via
# `python scripts/dogfood_context_graph_refinement.py`, so it adds the repo root
# before importing repo-local packages.
# ruff: noqa: E402
import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory_app.refinement_harness.tools._artifact_workspace import load_artifact_workspace
from factory_app.refinement_harness.tools.get_context_graph_catalog import get_context_graph_catalog
from factory_app.refinement_harness.tools.get_context_graph_scope import get_context_graph_scope
from mozaiksai.control_plane import (
    ArtifactKind,
    ArtifactScopeProposer,
    ChangeClass,
    ChangeIntent,
    CodingWorkerRequest,
    ControlPlaneCapabilityConfig,
    ControlPlaneConfig,
    ControlPlaneToolResult,
    ImpactSet,
    RefinementRequest,
    RefinementRoutingDecision,
    ScopedRefinementCodingWorker,
    load_selected_refinement_harness,
)
from mozaiksai.control_plane.app_intelligence import (
    APP_INTELLIGENCE_WORKSPACE_ARTIFACT_KEY,
    index_workspace_app_intelligence,
)
from mozaiksai.control_plane.contracts import ControlPlaneToolContext
from mozaiksai.core.artifacts.models import (
    ArtifactCommitMetadata,
    ArtifactLifecycleStatus,
    ArtifactValidationStatus,
    ArtifactVersionDoc,
)

DEFAULT_WORKSPACE = REPO_ROOT.parent / "mozaiks-app"
DEFAULT_REQUEST = "Update hosted build context behavior for a dogfood code-intelligence smoke."


class _MemoryArtifactStore:
    def __init__(self) -> None:
        self.created: list[ArtifactVersionDoc] = []

    async def create_artifact_version(self, **kwargs: Any) -> ArtifactVersionDoc:
        artifact_id = f"av_dogfood_{len(self.created) + 1}"
        lifecycle_status = _enum_value(
            kwargs.get("lifecycle_status"),
            ArtifactLifecycleStatus,
            ArtifactLifecycleStatus.DRAFT,
        )
        validation_status = _enum_value(
            kwargs.get("validation_status"),
            ArtifactValidationStatus,
            ArtifactValidationStatus.PENDING,
        )
        commit_metadata = kwargs.get("commit_metadata") or {}
        if not isinstance(commit_metadata, ArtifactCommitMetadata):
            commit_metadata = ArtifactCommitMetadata.model_validate(commit_metadata)

        parent_version_id = kwargs.get("parent_version_id")
        parent = None
        if parent_version_id:
            parent = next((artifact for artifact in self.created if artifact.id == parent_version_id), None)

        artifact = ArtifactVersionDoc(
            _id=artifact_id,
            app_id=kwargs["app_id"],
            artifact_kind=kwargs["artifact_kind"],
            artifact_key=kwargs["artifact_key"],
            version_number=len(self.created) + 1,
            parent_version_id=parent_version_id,
            lineage_root_id=parent.lineage_root_id if parent else artifact_id,
            source_workflow=kwargs.get("source_workflow"),
            source_chat_id=kwargs.get("source_chat_id"),
            lifecycle_status=lifecycle_status,
            validation_status=validation_status,
            files_manifest=list(kwargs.get("files_manifest") or []),
            commit_metadata=commit_metadata,
        )
        if artifact.lifecycle_status == ArtifactLifecycleStatus.CURRENT:
            self.created = [
                existing.model_copy(update={"lifecycle_status": ArtifactLifecycleStatus.SUPERSEDED})
                if existing.app_id == artifact.app_id
                and existing.artifact_kind == artifact.artifact_kind
                and existing.artifact_key == artifact.artifact_key
                and existing.lifecycle_status == ArtifactLifecycleStatus.CURRENT
                else existing
                for existing in self.created
            ]
        self.created.append(artifact)
        return artifact

    async def get_artifact_version(self, *, app_id: str, artifact_version_id: str) -> ArtifactVersionDoc | None:
        return next(
            (
                artifact
                for artifact in self.created
                if artifact.app_id == app_id and artifact.id == artifact_version_id
            ),
            None,
        )

    async def list_artifact_versions(
        self,
        *,
        app_id: str,
        artifact_kind: str | None = None,
        artifact_key: str | None = None,
        lifecycle_status: ArtifactLifecycleStatus | None = None,
        limit: int = 50,
        **_kwargs: Any,
    ) -> list[ArtifactVersionDoc]:
        rows = [
            artifact
            for artifact in self.created
            if artifact.app_id == app_id
            and (artifact_kind is None or artifact.artifact_kind == artifact_kind)
            and (artifact_key is None or artifact.artifact_key == artifact_key)
            and (lifecycle_status is None or artifact.lifecycle_status == lifecycle_status)
        ]
        rows.sort(key=lambda artifact: artifact.version_number, reverse=True)
        return rows[: max(1, int(limit))]

    async def accept_artifact_version(
        self,
        *,
        app_id: str,
        artifact_version_id: str,
        commit_metadata: dict[str, Any] | ArtifactCommitMetadata | None = None,
    ) -> ArtifactVersionDoc | None:
        target = await self.get_artifact_version(app_id=app_id, artifact_version_id=artifact_version_id)
        if target is None:
            return None
        refreshed = target.model_copy(
            update={
                "lifecycle_status": ArtifactLifecycleStatus.CURRENT,
                **({"commit_metadata": commit_metadata} if commit_metadata is not None else {}),
            }
        )
        self.created = [
            refreshed
            if existing.id == target.id
            else existing.model_copy(update={"lifecycle_status": ArtifactLifecycleStatus.SUPERSEDED})
            if existing.app_id == target.app_id
            and existing.artifact_kind == target.artifact_kind
            and existing.artifact_key == target.artifact_key
            and existing.lifecycle_status == ArtifactLifecycleStatus.CURRENT
            else existing
            for existing in self.created
        ]
        return refreshed


class _ToolExecutor:
    def __init__(self, artifact_store: _MemoryArtifactStore) -> None:
        self.artifact_store = artifact_store

    async def execute_tool(self, call: Any, *, context: ControlPlaneToolContext | None = None) -> ControlPlaneToolResult:
        if call.tool_id == "get_context_graph_catalog":
            output = await get_context_graph_catalog(context=context, artifact_store=self.artifact_store)
            return ControlPlaneToolResult(success=True, output=output)
        if call.tool_id == "get_context_graph_scope":
            output = await get_context_graph_scope(context=context, artifact_store=self.artifact_store)
            return ControlPlaneToolResult(success=True, output=output)
        if call.tool_id == "get_artifact_workspace_catalog":
            output = await _workspace_catalog(context=context, artifact_store=self.artifact_store)
            return ControlPlaneToolResult(success=True, output=output)
        if call.tool_id == "get_artifact_workspace_scope":
            output = await _workspace_scope(context=context, artifact_store=self.artifact_store)
            return ControlPlaneToolResult(success=True, output=output)
        return ControlPlaneToolResult(success=True, output={"present": True, "tool_id": call.tool_id})


class _ScopeService:
    def __init__(self, target_path: str) -> None:
        self.target_path = target_path

    async def generate_json_completion(self, **_kwargs: Any) -> dict[str, Any]:
        parsed = {
            "resolution": "scoped_files",
            "selected_paths": [self.target_path],
            "rationale": "Context Graph catalog selected this file for the requested dogfood patch.",
            "confidence": 0.91,
            "clarification_question": None,
            "signals": ["context_graph_candidate", "dogfood_smoke"],
        }
        return {"content": json.dumps(parsed), "parsed": parsed, "usage": {}}


class _CodingService:
    def __init__(self, target_path: str, original_content: str) -> None:
        self.target_path = target_path
        self.updated_content = _dogfood_patch(target_path, original_content)

    async def generate_json_completion(self, **_kwargs: Any) -> dict[str, Any]:
        parsed = {
            "summary": "Stage a deterministic dogfood patch for the selected Context Graph file.",
            "owned_paths": [self.target_path],
            "updated_files": {self.target_path: self.updated_content},
            "validation_strategy": "skip",
            "validation_commands": [],
            "start_preview": False,
            "needs_human_review": False,
            "rationale": "The smoke validates scope enforcement and staged artifact persistence without mutating source.",
        }
        return {"content": json.dumps(parsed), "parsed": parsed, "usage": {}}


async def _validation_runner(**_kwargs: Any) -> dict[str, Any]:
    return {
        "success": True,
        "validation_strategy": "skip",
        "validation_status": "skipped",
        "preview_url": None,
        "errors": [],
        "warnings": ["dogfood smoke skipped runtime validation intentionally"],
    }


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.exists() or not workspace.is_dir():
        raise ValueError(f"workspace does not exist or is not a directory: {workspace}")

    output_root = Path(args.output_root).expanduser().resolve() if args.output_root else Path(tempfile.mkdtemp(prefix="mozaiks_context_dogfood_"))
    store = _MemoryArtifactStore()
    result = await index_workspace_app_intelligence(
        app_id=args.app_id,
        workspace_root=workspace,
        artifact_store=store,
        generated_artifacts_root=output_root / "generated",
        source_workflow="dogfood_app_intelligence_refinement",
    )

    catalog_context = ControlPlaneToolContext(
        checkpoint="scope_requested",
        app_id=args.app_id,
        artifact_kind="app_bundle",
        artifact_key=APP_INTELLIGENCE_WORKSPACE_ARTIFACT_KEY,
        artifact_version_id=result.app_bundle_artifact_version_id,
        requested_workflow_id="AppGenerator",
        source_surface="dogfood_context_graph_refinement",
        raw_user_request=args.request,
    )
    catalog = await get_context_graph_catalog(context=catalog_context, artifact_store=store)
    target_path = args.target_path or _select_target_path(catalog)
    if not target_path:
        raise RuntimeError("Context Graph catalog produced no candidate files for this request")

    refinement_request = RefinementRequest(
        request_kind="refinement",
        declared_change_class=ChangeClass.PATCH,
        artifact_kind=ArtifactKind.APP_BUNDLE,
        artifact_key=APP_INTELLIGENCE_WORKSPACE_ARTIFACT_KEY,
        artifact_version_id=result.app_bundle_artifact_version_id,
        raw_user_request=args.request,
        source_surface="dogfood_context_graph_refinement",
        app_id=args.app_id,
        requested_workflow_id="AppGenerator",
    )
    routing_decision = RefinementRoutingDecision(
        workflow_id="AppGenerator",
        workflow_sequence="app_revision",
        refinement_request=refinement_request,
        context_seed={},
        is_full_restart=False,
        explanation="Dogfood smoke forces the narrow patch path after graph-aware scope selection.",
        change_intent=ChangeIntent(
            change_class=ChangeClass.PATCH,
            source="dogfood_smoke",
            signals=["context_graph_candidate"],
            rationale="A single file is selected from the Context Graph catalog.",
            confidence=0.91,
            touches_app_bundle=True,
        ),
        impact_set=ImpactSet(
            workflow_sequence="app_revision",
            affected_workflows=["AppGenerator"],
            affected_declarative_families=["app_bundle"],
            affected_bundle_paths=[target_path],
            requires_replanning=False,
            requires_rebuild=True,
            restart_from="AppGenerator",
            scope_summary=f"Scoped dogfood patch for {target_path}.",
        ),
    )

    tool_executor = _ToolExecutor(store)
    proposer = ArtifactScopeProposer(
        capability_service=_ScopeService(target_path),
        config_loader=_enabled_control_plane,
        pack_loader=load_selected_refinement_harness,
        tool_executor=tool_executor,
        artifact_store=store,
    )
    proposal = await proposer.propose(
        refinement_request=refinement_request,
        routing_decision=routing_decision,
        payload={},
    )
    files = await proposer.materialize_files(
        refinement_request=refinement_request,
        selected_paths=proposal.selected_paths,
    )
    if proposal.resolution != "scoped_files" or target_path not in files:
        raise RuntimeError(f"Scope proposer did not materialize target path {target_path}: {proposal.model_dump(mode='json')}")

    scope_context = catalog_context.model_copy(
        update={"extra": {"selected_file_paths": proposal.selected_paths}}
    )
    scope = await get_context_graph_scope(context=scope_context, artifact_store=store)

    worker = ScopedRefinementCodingWorker(
        capability_service=_CodingService(target_path, files[target_path]),
        config_loader=_enabled_control_plane,
        pack_loader=load_selected_refinement_harness,
        tool_executor=tool_executor,
        validation_runner=_validation_runner,
        artifact_store=store,
        output_root=output_root / "refinements",
    )
    coding_result = await worker.execute(
        CodingWorkerRequest(
            app_id=args.app_id,
            artifact_kind="app_bundle",
            artifact_key=APP_INTELLIGENCE_WORKSPACE_ARTIFACT_KEY,
            artifact_version_id=result.app_bundle_artifact_version_id,
            requested_workflow_id="AppGenerator",
            raw_user_request=args.request,
            source_surface="dogfood_context_graph_refinement",
            change_class="patch",
            files=files,
            validation_strategy="skip",
            metadata={
                "selected_file_paths": proposal.selected_paths,
                "scope_proposal": proposal.model_dump(mode="json"),
            },
        )
    )
    if coding_result.status != "validated":
        raise RuntimeError(f"Coding worker did not validate: {coding_result.model_dump(mode='json')}")

    return {
        "schema_version": "mozaiks.dogfood_context_graph_refinement.v1",
        "app_id": args.app_id,
        "workspace": workspace.as_posix(),
        "output_root": output_root.as_posix(),
        "request": args.request,
        "app_intelligence": {
            "app_bundle_artifact_version_id": result.app_bundle_artifact_version_id,
            "app_context_version_id": result.app_context_version_id,
            "graph_artifact_version_id": result.graph_artifact_version_id,
            "artifact_path": result.artifact_path,
            "indexed_file_count": result.indexed_file_count,
            "health_report": result.health_report,
            "warnings": result.warnings,
        },
        "catalog": {
            "present": catalog.get("present"),
            "graph_id": catalog.get("graph_id"),
            "node_count": catalog.get("node_count"),
            "edge_count": catalog.get("edge_count"),
            "candidate_count": len(catalog.get("candidate_files") or []),
            "top_candidates": (catalog.get("candidate_files") or [])[:5],
        },
        "scope": {
            "target_path": target_path,
            "proposal": proposal.model_dump(mode="json"),
            "selected_file_paths": scope.get("selected_file_paths"),
            "related_file_paths": scope.get("related_file_paths"),
            "selected_scope_keys": list((scope.get("selected_scope") or {}).keys()),
        },
        "coding_worker": {
            "status": coding_result.status,
            "eligible": coding_result.eligible,
            "applied_paths": sorted(coding_result.applied_files),
            "artifact_version_id": coding_result.metadata.get("artifact_version_id"),
            "artifact_path": coding_result.metadata.get("artifact_path"),
            "validation_status": (coding_result.validation_result or {}).get("validation_status"),
        },
        "source_mutated": False,
    }


def _enabled_control_plane() -> ControlPlaneConfig:
    return ControlPlaneConfig(
        enabled=True,
        coding=ControlPlaneCapabilityConfig(
            enabled=True,
            llm_config={"model": "deterministic-dogfood-smoke", "temperature": 0.0},
        ),
    )


async def _workspace_catalog(
    *,
    context: ControlPlaneToolContext | None,
    artifact_store: _MemoryArtifactStore,
) -> dict[str, Any]:
    if context is None:
        return {"present": False, "reason": "missing_context"}
    workspace = await load_artifact_workspace(
        artifact_store=artifact_store,
        app_id=str(context.app_id or ""),
        artifact_version_id=str(context.artifact_version_id or ""),
    )
    if not workspace.get("present"):
        return workspace
    file_tree = sorted((workspace.get("file_map") or {}).keys())
    return {"present": True, "file_tree": file_tree[:200], "file_count": len(file_tree)}


async def _workspace_scope(
    *,
    context: ControlPlaneToolContext | None,
    artifact_store: _MemoryArtifactStore,
) -> dict[str, Any]:
    catalog = await _workspace_catalog(context=context, artifact_store=artifact_store)
    if not catalog.get("present") or context is None:
        return catalog
    selected = [str(path) for path in context.extra.get("selected_file_paths") or []]
    workspace = await load_artifact_workspace(
        artifact_store=artifact_store,
        app_id=str(context.app_id or ""),
        artifact_version_id=str(context.artifact_version_id or ""),
    )
    file_map = workspace.get("file_map") or {}
    return {
        "present": True,
        "selected_file_paths": selected,
        "selected_files": {path: file_map.get(path) for path in selected if path in file_map},
    }


def _select_target_path(catalog: dict[str, Any]) -> str | None:
    candidates = catalog.get("candidate_files") if isinstance(catalog, dict) else None
    if not isinstance(candidates, list):
        return None
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        path = str(candidate.get("path") or "").strip()
        if path:
            return path
    return None


def _dogfood_patch(path: str, content: str) -> str:
    marker = "Mozaiks dogfood Context Graph smoke: staged patch only."
    stripped = content.rstrip()
    lower_path = path.lower()
    if lower_path.endswith((".md", ".html")):
        return f"{stripped}\n\n<!-- {marker} -->\n"
    if lower_path.endswith((".js", ".jsx", ".ts", ".tsx", ".css")):
        return f"{stripped}\n\n// {marker}\n"
    return f"{stripped}\n\n# {marker}\n"


def _enum_value(value: Any, enum_type: Any, default: Any) -> Any:
    if isinstance(value, enum_type):
        return value
    if value is None:
        return default
    return enum_type(value)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Context Graph dogfood refinement smoke.")
    parser.add_argument("--workspace", default=str(DEFAULT_WORKSPACE), help="Workspace root to scan")
    parser.add_argument("--app-id", default="mozaiks-app-dogfood", help="App id for in-memory artifacts")
    parser.add_argument("--request", default=DEFAULT_REQUEST, help="Refinement request text")
    parser.add_argument("--target-path", default=None, help="Override selected path inside the scanned workspace")
    parser.add_argument("--output-root", default=None, help="Directory for staged smoke artifacts")
    parser.add_argument("--json", dest="json_output", action="store_true", help="Print compact JSON only")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        payload = asyncio.run(_run(args))
    except Exception as exc:
        print(f"Dogfood Context Graph refinement smoke failed: {exc}")
        return 1
    if args.json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    print("Dogfood Context Graph refinement smoke passed.")
    print(f"  workspace: {payload['workspace']}")
    print(f"  output_root: {payload['output_root']}")
    print(f"  indexed_file_count: {payload['app_intelligence']['indexed_file_count']}")
    print(f"  graph_health: {payload['app_intelligence']['health_report'].get('status')}")
    print(f"  candidate_count: {payload['catalog']['candidate_count']}")
    print(f"  target_path: {payload['scope']['target_path']}")
    print(f"  coding_worker_status: {payload['coding_worker']['status']}")
    print(f"  staged_artifact_path: {payload['coding_worker']['artifact_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
