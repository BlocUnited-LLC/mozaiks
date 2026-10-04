"""Contract-surface planner for the Mozaiks refinement harness.

Maps a user refinement request to an ordered set of Mozaiks contract surfaces
that need to be updated — instead of selecting files by keyword proximity.

This is the core of the contract-aware refinement path:
- For patch changes: use the existing scope_proposer + coding_worker
- For feature/design changes: use this planner to identify which contract
  surfaces are affected and in what dependency order, then drive targeted
  regeneration per surface rather than re-running the full workflow.

The planner uses:
1. An LLM call to classify which surface types and which specific module/page/
   workflow are affected (contract_surface_selection_system.yaml prompt)
2. Deterministic file resolution from the complete verified saved bundle
   and its schema, route and component declarations
3. Dependency ordering so data schemas are updated before module actions,
   module actions before page bindings, etc.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import PurePosixPath
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from factory_app.workflows._shared.generated_ui_contract import (
    _route_manifest_pages_from_content,
    resolve_registered_component_files,
)
from mozaiksai.control_plane.config import ControlPlaneConfig, load_control_plane_config
from mozaiksai.control_plane.contracts import (
    CONTRACT_SURFACE_CANONICAL_PATHS,
    CONTRACT_SURFACE_DEPENDENCY_ORDER,
    CONTRACT_SURFACE_TARGET_KINDS,
    ContractSurfaceKind,
    ContractSurfacePlan,
    ContractSurfaceTarget,
    ContractSurfaceTargetKind,
    ContractSurfaceUpdate,
)
from mozaiksai.control_plane.loader import load_selected_refinement_harness
from mozaiksai.control_plane.schema import LoadedControlPlanePack
from mozaiksai.core.adapters.ag2_agent_runner import AG2StructuredAgentRunner
from mozaiksai.core.runtime.app.auth_contract import APP_AUTH_COMPONENTS
from mozaiksai.core.runtime.app.page_schema import PageSchemaValidationError, validate_page_schema
from mozaiksai.core.usage.context import resolve_auxiliary_usage_context

from .refinement_router import RefinementRequest, RefinementRoutingDecision

_logger = logging.getLogger("mozaiksai.control_plane.implementations.contract_surface_planner")

_CHECKPOINT_EVENT = "contract_surface_requested"

_ELIGIBLE_CHANGE_CLASSES = {"feature", "design"}
_ELIGIBLE_ARTIFACT_KINDS = {"app_bundle", "workflow_bundle"}

class _SurfaceEntry(ContractSurfaceTarget):
    rationale: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    generation_hint: str


class ContractSurfaceClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    surfaces: list[_SurfaceEntry]
    summary: str
    confidence: float = Field(ge=0.0, le=1.0)
    requires_schema_migration: bool
    fallback_to_workflow: bool
    fallback_reason: str | None


def _safe_path(path: str) -> bool:
    return (
        isinstance(path, str) and bool(path) and path == path.strip()
        and not any(char in path for char in ("\\", ":", "\x00"))
        and not PurePosixPath(path).is_absolute()
        and all(part not in {"", ".", ".."} for part in path.split("/"))
    )


def _validate_workspace_files(files: dict[str, str]) -> None:
    if not isinstance(files, dict) or not files:
        raise ValueError("A complete verified saved bundle is required for surface planning")
    if any(not _safe_path(path) or not isinstance(content, str) for path, content in files.items()):
        raise ValueError("Saved bundle contains an unsafe path or non-text source")


def _validate_allowed_paths(allowed_paths: list[str] | None, files: dict[str, str]) -> None:
    if allowed_paths is not None and (
        not allowed_paths or len(set(allowed_paths)) != len(allowed_paths)
        or any(not _safe_path(path) or path not in files for path in allowed_paths)
    ):
        raise ValueError("Explicit write scope must contain distinct saved bundle paths")


def _mapping(files: dict[str, str], path: str) -> dict[str, Any]:
    content = files.get(path)
    if not isinstance(content, str) or not content.strip():
        raise ValueError(f"Missing or empty saved contract: {path}")
    try:
        parsed = json.loads(content) if path.endswith(".json") else yaml.safe_load(content)
    except (ValueError, yaml.YAMLError) as exc:
        raise ValueError(f"Invalid saved contract: {path}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"Saved contract must be an object: {path}")
    return parsed


def _page_paths(target_id: str, files: dict[str, str]) -> list[str]:
    candidates: list[str] = []
    schema_paths = [
        f"ui/pages/{target_id}{suffix}" for suffix in (".yaml", ".yml")
    ] + [f"ui/pages/{target_id}/page{suffix}" for suffix in (".yaml", ".yml")]
    for path in schema_paths:
        if path in files:
            try:
                page = validate_page_schema(_mapping(files, path), expected_name=target_id)
            except PageSchemaValidationError as exc:
                raise ValueError(f"Invalid saved page schema: {path}") from exc
            if page.name != target_id:
                raise ValueError("Schema page identity must match exactly")
            candidates.append(path)

    if "ui/route_manifest.json" in files:
        pages, warnings = _route_manifest_pages_from_content(
            files["ui/route_manifest.json"], source_label="Saved bundle",
        )
        if warnings:
            raise ValueError("; ".join(warnings))
        routes = [page for page in pages if page.get("id") == target_id]
        if len(routes) > 1:
            raise ValueError(f"Ambiguous saved page identity: {target_id}")
        if routes:
            route = routes[0]
            route_path = route.get("path")
            if not isinstance(route_path, str) or not route_path.startswith("/") or ".." in route_path:
                raise ValueError("Saved page requires a safe declared route path")
            if sum(page.get("path") == route.get("path") for page in pages) != 1:
                raise ValueError(f"Ambiguous saved route for page: {target_id}")
            component = route.get("component")
            if component == "SchemaPage":
                if route.get("schema") != target_id or len(candidates) != 1:
                    raise ValueError("Schema route must resolve to its exact saved page identity")
            else:
                if component in APP_AUTH_COMPONENTS or not isinstance(component, str):
                    raise ValueError("Page is not an app-owned custom component")
                registry = files.get("ui/index.js")
                if not registry or not registry.strip():
                    raise ValueError("Saved custom page requires ui/index.js")
                registered, warnings = resolve_registered_component_files(
                    files, source_label="Saved bundle", registry_paths={"ui/index.js"},
                )
                if warnings:
                    raise ValueError("; ".join(warnings))
                paths = registered.get(component, set())
                if len(paths) != 1:
                    raise ValueError(f"Custom page component must resolve to one local file: {component}")
                path = next(iter(paths))
                if not _safe_path(path) or not path.startswith("ui/pages/custom/") or not path.endswith(".jsx"):
                    raise ValueError("Custom page must resolve to a canonical app-owned JSX source")
                candidates.append(path)
        elif any(page.get("component") == "SchemaPage" and page.get("schema") == target_id for page in pages):
            # SchemaPage's explicit schema name is the runtime page identity.
            if sum(page.get("component") == "SchemaPage" and page.get("schema") == target_id for page in pages) != 1:
                raise ValueError(f"Ambiguous saved schema route: {target_id}")
    if len(candidates) != 1:
        raise ValueError(f"Page target must resolve to exactly one saved source: {target_id}")
    return candidates


def resolve_contract_surface_paths(
    *, kind: ContractSurfaceKind, target_id: str, target_kind: ContractSurfaceTargetKind,
    build_family: str, workspace_files: dict[str, str], artifact_app_id: str | None = None,
) -> list[str]:
    """Resolve write ownership from the supplied saved bundle, never guessed paths."""
    _validate_workspace_files(workspace_files)
    if CONTRACT_SURFACE_TARGET_KINDS.get(kind) != target_kind:
        raise ValueError(f"Unsupported contract surface pair: {kind}/{target_kind}")
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", target_id) or ".." in target_id:
        raise ValueError("Surface target must be a safe exact declared identity")
    expected_family = "workflow_bundle" if target_kind == "workflow" else "app_bundle"
    if build_family != expected_family:
        raise ValueError(f"{kind}/{target_kind} requires {expected_family}")
    if target_kind == "page":
        paths = _page_paths(target_id, workspace_files)
    else:
        if target_kind == "module":
            declaration = _mapping(workspace_files, f"modules/{target_id}/module.yaml")
            module = declaration.get("module")
            if not isinstance(module, dict) or module.get("id") != target_id:
                raise ValueError("Module target does not match its saved declaration")
        elif target_kind == "workflow":
            declaration = _mapping(workspace_files, f"workflows/{target_id}/orchestrator.yaml")
            if declaration.get("workflow_name") != target_id:
                raise ValueError("Workflow target does not match its saved declaration")
        else:
            declared_id = _mapping(workspace_files, "app.json").get("appId")
            if not artifact_app_id or target_id != artifact_app_id:
                raise ValueError("App target does not match its authoritative artifact app identity")
            if declared_id is not None and declared_id != artifact_app_id:
                raise ValueError("Saved appId conflicts with the authoritative artifact app identity")
        paths = [
            template.replace("{target_id}", target_id)
            for template in CONTRACT_SURFACE_CANONICAL_PATHS[kind]
            if template.replace("{target_id}", target_id) in workspace_files
        ]
    if not paths or any(not workspace_files.get(path, "").strip() for path in paths):
        raise ValueError(f"Surface has missing or empty saved source: {kind}/{target_id}")
    return paths


def validate_contract_surface_plan(
    *, plan: ContractSurfacePlan, refinement_request: RefinementRequest,
    routing_decision: RefinementRoutingDecision, workspace_files: dict[str, str],
    allowed_paths: list[str] | None = None,
) -> None:
    """Admit the whole plan before allocating a build or generating any surface."""
    _validate_workspace_files(workspace_files)
    _validate_allowed_paths(allowed_paths, workspace_files)
    change_class = routing_decision.change_intent.change_class.value
    if plan.build_family != refinement_request.build_family or plan.change_class != change_class:
        raise ValueError("Surface plan does not match its request family and classification")
    if change_class not in _ELIGIBLE_CHANGE_CLASSES or plan.fallback_to_workflow or not plan.surfaces:
        raise ValueError("Surface plan is not eligible for targeted generation")
    identities: set[tuple[str, str]] = set()
    for surface in plan.surfaces:
        identity = (surface.kind, surface.target_id)
        if identity in identities:
            raise ValueError("Surface plan contains a duplicate target")
        identities.add(identity)
        resolved = resolve_contract_surface_paths(
            kind=surface.kind, target_id=surface.target_id, target_kind=surface.target_kind,
            build_family=plan.build_family, workspace_files=workspace_files,
            artifact_app_id=refinement_request.artifact_app_id,
        )
        if not surface.affected_paths or len(set(surface.affected_paths)) != len(surface.affected_paths):
            raise ValueError("Surface write paths must be nonempty and distinct")
        if not set(surface.affected_paths).issubset(resolved):
            raise ValueError("Surface write paths do not belong to the declared saved target")
        if allowed_paths is not None and not set(surface.affected_paths).issubset(allowed_paths):
            raise ValueError("Surface plan exceeds the explicitly approved write scope")


def _available_targets(files: dict[str, str], build_family: str, artifact_app_id: str | None) -> list[dict[str, Any]]:
    """Give the classifier exact saved identities and deterministic write candidates."""
    identities: set[tuple[ContractSurfaceTargetKind, str]] = set()
    for path in files:
        parts = path.split("/")
        if len(parts) == 3 and parts[0] == "modules" and parts[2] == "module.yaml":
            identities.add(("module", parts[1]))
        if len(parts) == 3 and parts[0] == "workflows" and parts[2] == "orchestrator.yaml":
            identities.add(("workflow", parts[1]))
        if path.startswith("ui/pages/") and path.endswith((".yaml", ".yml")):
            name = _mapping(files, path).get("name")
            if isinstance(name, str):
                identities.add(("page", name))
    if "app.json" in files and artifact_app_id:
        identities.add(("app", artifact_app_id))
    if "ui/route_manifest.json" in files:
        pages, warnings = _route_manifest_pages_from_content(files["ui/route_manifest.json"], source_label="Saved bundle")
        if warnings:
            raise ValueError("; ".join(warnings))
        for page in pages:
            if isinstance(page.get("id"), str):
                identities.add(("page", page["id"]))
    targets = []
    for target_kind, target_id in sorted(identities):
        for kind, expected_target in CONTRACT_SURFACE_TARGET_KINDS.items():
            if expected_target != target_kind:
                continue
            try:
                paths = resolve_contract_surface_paths(
                    kind=kind, target_id=target_id, target_kind=target_kind,
                    build_family=build_family, workspace_files=files, artifact_app_id=artifact_app_id,
                )
            except ValueError:
                continue
            targets.append({"kind": kind, "target_kind": target_kind, "target_id": target_id, "affected_paths": paths})
    return targets


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------


class ContractSurfacePlanner:
    """Maps user intent to ordered Mozaiks contract surface updates.

    For feature and design changes on app_bundle or workflow_bundle artifacts,
    this planner:

    1. Calls an LLM to classify which contract surface types are affected and
       which specific module/page/workflow is the target.
    2. Resolves declared targets against the complete verified saved bundle.
    3. Checks resolved paths against any explicitly approved write scope.
    4. Orders surfaces by CONTRACT_SURFACE_DEPENDENCY_ORDER so data schemas
       are updated before handlers, handlers before page bindings, etc.
    5. Returns a ContractSurfacePlan the harness decision policy uses to shape
       the Studio UX (show grouped surface diff, drive targeted regeneration).

    Falls back to workflow_reentry when:
    - The LLM classifies the request as too broad for targeted regeneration
    - Confidence is below the threshold
    Unresolved targets or out-of-scope paths fail admission before generation.
    """

    _CONFIDENCE_THRESHOLD = 0.65

    def __init__(
        self,
        *,
        agent_runner: AG2StructuredAgentRunner | None = None,
        config_loader: Any = load_control_plane_config,
        pack_loader: Any = load_selected_refinement_harness,
    ) -> None:
        self._agent_runner = agent_runner or AG2StructuredAgentRunner()
        self._config_loader = config_loader
        self._pack_loader = pack_loader

    def eligible(
        self,
        *,
        change_class: str,
        build_family: str,
    ) -> bool:
        return (
            str(change_class or "").strip().lower() in _ELIGIBLE_CHANGE_CLASSES
            and str(build_family or "").strip().lower() in _ELIGIBLE_ARTIFACT_KINDS
        )

    async def propose(
        self,
        *,
        refinement_request: RefinementRequest,
        routing_decision: RefinementRoutingDecision,
        workspace_files: dict[str, str],
        allowed_paths: list[str] | None = None,
        context_graph_catalog: dict[str, Any] | None = None,
    ) -> ContractSurfacePlan:
        change_class = str(routing_decision.change_intent.change_class.value or "").strip().lower()
        build_family = str(refinement_request.build_family or "").strip().lower()

        if not self.eligible(change_class=change_class, build_family=build_family):
            return ContractSurfacePlan(
                summary="Contract surface planning not applicable for this change class.",
                change_class=change_class,
                build_family=build_family,
                fallback_to_workflow=True,
                fallback_reason=f"change_class={change_class!r} is not eligible for contract surface planning",
                confidence=0.0,
            )

        _validate_workspace_files(workspace_files)
        _validate_allowed_paths(allowed_paths, workspace_files)
        pack = self._load_pack()
        checkpoint = pack.checkpoint_by_event(_CHECKPOINT_EVENT)
        if checkpoint is None or not checkpoint.prompt_id:
            return ContractSurfacePlan(
                summary="No contract_surface_requested checkpoint declared in this refinement harness.",
                change_class=change_class,
                build_family=build_family,
                fallback_to_workflow=True,
                fallback_reason="missing checkpoint declaration",
                confidence=0.0,
            )

        prompt = pack.prompt_by_id(checkpoint.prompt_id)
        if prompt is None:
            return ContractSurfacePlan(
                summary=f"Prompt '{checkpoint.prompt_id}' not found.",
                change_class=change_class,
                build_family=build_family,
                fallback_to_workflow=True,
                fallback_reason="missing prompt",
                confidence=0.0,
            )

        llm_config = self._load_config().resolve_capability_llm_config("contract_surface")
        user_prompt = self._build_user_prompt(
            request=refinement_request,
            routing_decision=routing_decision,
            context_graph_catalog=context_graph_catalog,
            workspace_files=workspace_files,
            allowed_paths=allowed_paths,
        )

        try:
            classification = await self._agent_runner.run(
                usage_context=resolve_auxiliary_usage_context(
                    app_id=refinement_request.app_id, user_id=refinement_request.user_id,
                    context=refinement_request.usage_context,
                    target_app_id=refinement_request.artifact_app_id,
                ),
                agent_name="ContractSurfacePlanner",
                system_prompt=prompt.content,
                user_prompt=user_prompt,
                llm_config=llm_config,
                response_schema=ContractSurfaceClassification,
            )
        except Exception as exc:
            _logger.warning("Contract surface classification failed: %s", exc)
            return ContractSurfacePlan(
                summary="Contract surface classification failed.",
                change_class=change_class,
                build_family=build_family,
                fallback_to_workflow=True,
                fallback_reason=str(exc),
                confidence=0.0,
            )

        if classification.fallback_to_workflow or classification.confidence < self._CONFIDENCE_THRESHOLD:
            return ContractSurfacePlan(
                summary=classification.summary or "Change is too broad for targeted surface regeneration.",
                change_class=change_class,
                build_family=build_family,
                fallback_to_workflow=True,
                fallback_reason=classification.fallback_reason or "low confidence",
                confidence=float(classification.confidence),
            )

        surfaces = self._resolve_surfaces(
            classification=classification,
            build_family=build_family,
            workspace_files=workspace_files,
            artifact_app_id=refinement_request.artifact_app_id,
        )
        plan = ContractSurfacePlan(
            surfaces=surfaces,
            summary=classification.summary or f"Targeted update across {len(surfaces)} contract surface(s).",
            change_class=change_class,
            build_family=build_family,
            requires_schema_migration=classification.requires_schema_migration,
            confidence=float(classification.confidence),
            fallback_to_workflow=False,
        )
        validate_contract_surface_plan(
            plan=plan, refinement_request=refinement_request, routing_decision=routing_decision,
            workspace_files=workspace_files, allowed_paths=allowed_paths,
        )
        return plan

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_config(self) -> ControlPlaneConfig:
        config = self._config_loader()
        return config if isinstance(config, ControlPlaneConfig) else ControlPlaneConfig.model_validate(config)

    def _load_pack(self) -> LoadedControlPlanePack:
        pack = self._pack_loader()
        return pack if isinstance(pack, LoadedControlPlanePack) else LoadedControlPlanePack.model_validate(pack)

    @staticmethod
    def _resolve_surfaces(
        *,
        classification: ContractSurfaceClassification,
        build_family: str,
        workspace_files: dict[str, str],
        artifact_app_id: str | None = None,
    ) -> list[ContractSurfaceUpdate]:
        updates = [
            ContractSurfaceUpdate(
                **entry.model_dump(),
                affected_paths=resolve_contract_surface_paths(
                    kind=entry.kind, target_id=entry.target_id, target_kind=entry.target_kind,
                    build_family=build_family, workspace_files=workspace_files, artifact_app_id=artifact_app_id,
                ),
                dependency_order=CONTRACT_SURFACE_DEPENDENCY_ORDER[entry.kind],
            )
            for entry in classification.surfaces
        ]
        return sorted(updates, key=lambda surface: surface.dependency_order)

    @staticmethod
    def _build_user_prompt(
        *,
        request: RefinementRequest,
        routing_decision: RefinementRoutingDecision,
        context_graph_catalog: dict[str, Any] | None,
        workspace_files: dict[str, str],
        allowed_paths: list[str] | None,
    ) -> str:
        payload: dict[str, Any] = {
            "change_class": routing_decision.change_intent.change_class.value,
            "build_family": request.build_family,
            "raw_user_request": request.raw_user_request,
            "workflow_sequence": routing_decision.workflow_sequence,
            "rationale": routing_decision.change_intent.rationale,
            "available_targets": _available_targets(workspace_files, request.build_family, request.artifact_app_id),
            "allowed_paths": allowed_paths,
        }
        if context_graph_catalog:
            payload["candidate_files"] = (context_graph_catalog.get("candidate_files") or [])[:15]
            payload["node_count"] = context_graph_catalog.get("node_count")
            payload["stale_status"] = context_graph_catalog.get("stale_status")

        lines = [
            "Classify which Mozaiks contract surfaces this refinement request affects.",
            "Return JSON only.",
            "payload_json:",
            json.dumps(payload, indent=2, sort_keys=True, default=str),
            "",
            "Return a JSON object with this exact shape:",
            json.dumps(
                {
                    "surfaces": [
                        {
                            "kind": "module_action|module_contract|page_binding|data_schema|workflow_tool|workflow_agent|ui_component|app_config",
                            "target_id": "the module_id, workflow_name, or page_id",
                            "target_kind": "module|workflow|page|app",
                            "rationale": "why this surface is affected",
                            "confidence": 0.85,
                            "generation_hint": "what specifically to add or change on this surface",
                        }
                    ],
                    "summary": "one-line summary of the targeted update",
                    "confidence": 0.85,
                    "requires_schema_migration": False,
                    "fallback_to_workflow": False,
                    "fallback_reason": None,
                },
                indent=2,
            ),
        ]
        return "\n".join(lines)


_contract_surface_planner: ContractSurfacePlanner | None = None


def get_contract_surface_planner() -> ContractSurfacePlanner:
    global _contract_surface_planner
    if _contract_surface_planner is None:
        _contract_surface_planner = ContractSurfacePlanner()
    return _contract_surface_planner
