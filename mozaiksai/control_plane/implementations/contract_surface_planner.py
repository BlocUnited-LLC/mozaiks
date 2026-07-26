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
2. Deterministic file resolution from the context graph and canonical path
   templates — not keyword-ranked guessing
3. Dependency ordering so data schemas are updated before module actions,
   module actions before page bindings, etc.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from mozaiksai.control_plane.config import ControlPlaneConfig, load_control_plane_config
from mozaiksai.control_plane.contracts import (
    CONTRACT_SURFACE_CANONICAL_PATHS,
    CONTRACT_SURFACE_DEPENDENCY_ORDER,
    ContractSurfacePlan,
    ContractSurfaceUpdate,
)
from mozaiksai.control_plane.executor import ControlPlaneToolExecutor
from mozaiksai.control_plane.loader import load_selected_refinement_harness
from mozaiksai.control_plane.schema import LoadedControlPlanePack
from mozaiksai.core.adapters.ag2_agent_runner import AG2StructuredAgentRunner

from .refinement_router import RefinementRequest, RefinementRoutingDecision

_logger = logging.getLogger("mozaiksai.control_plane.implementations.contract_surface_planner")

_CHECKPOINT_EVENT = "contract_surface_requested"

_ELIGIBLE_CHANGE_CLASSES = {"feature", "design"}
_ELIGIBLE_ARTIFACT_KINDS = {"app_bundle", "workflow_bundle"}

_VALID_SURFACE_KINDS: set[str] = {
    "module_action",
    "module_contract",
    "page_binding",
    "data_schema",
    "workflow_tool",
    "workflow_agent",
    "ui_component",
    "app_config",
}


# ---------------------------------------------------------------------------
# Internal LLM output model
# ---------------------------------------------------------------------------


class _SurfaceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    target_id: str
    target_kind: str
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)
    generation_hint: str


class _ContractSurfaceClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    surfaces: list[_SurfaceEntry]
    summary: str
    confidence: float = Field(ge=0.0, le=1.0)
    requires_schema_migration: bool
    fallback_to_workflow: bool
    fallback_reason: str | None


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------


class ContractSurfacePlanner:
    """Maps user intent to ordered Mozaiks contract surface updates.

    For feature and design changes on app_bundle or workflow_bundle artifacts,
    this planner:

    1. Calls an LLM to classify which contract surface types are affected and
       which specific module/page/workflow is the target.
    2. Resolves the canonical file paths for each surface using the declared
       CONTRACT_SURFACE_CANONICAL_PATHS templates — not keyword proximity.
    3. Filters resolved paths against the context graph catalog (when present)
       to confirm the files actually exist in the workspace.
    4. Orders surfaces by CONTRACT_SURFACE_DEPENDENCY_ORDER so data schemas
       are updated before handlers, handlers before page bindings, etc.
    5. Returns a ContractSurfacePlan the harness decision policy uses to shape
       the Studio UX (show grouped surface diff, drive targeted regeneration).

    Falls back to workflow_reentry when:
    - The LLM classifies the request as too broad for targeted regeneration
    - Confidence is below the threshold
    - The context graph catalog is missing and path resolution is not possible
    """

    _CONFIDENCE_THRESHOLD = 0.65

    def __init__(
        self,
        *,
        agent_runner: AG2StructuredAgentRunner | None = None,
        config_loader: Any = load_control_plane_config,
        pack_loader: Any = load_selected_refinement_harness,
        tool_executor: Any = None,
    ) -> None:
        self._agent_runner = agent_runner or AG2StructuredAgentRunner()
        self._config_loader = config_loader
        self._pack_loader = pack_loader
        self._tool_executor = tool_executor or ControlPlaneToolExecutor(pack_loader=pack_loader)

    def eligible(
        self,
        *,
        change_class: str,
        artifact_kind: str,
    ) -> bool:
        return (
            str(change_class or "").strip().lower() in _ELIGIBLE_CHANGE_CLASSES
            and str(artifact_kind or "").strip().lower() in _ELIGIBLE_ARTIFACT_KINDS
        )

    async def propose(
        self,
        *,
        refinement_request: RefinementRequest,
        routing_decision: RefinementRoutingDecision,
        context_graph_catalog: dict[str, Any] | None = None,
    ) -> ContractSurfacePlan:
        change_class = str(routing_decision.change_intent.change_class.value or "").strip().lower()
        artifact_kind = str(refinement_request.artifact_kind or "").strip().lower()

        if not self.eligible(change_class=change_class, artifact_kind=artifact_kind):
            return ContractSurfacePlan(
                summary="Contract surface planning not applicable for this change class.",
                change_class=change_class,
                artifact_kind=artifact_kind,
                fallback_to_workflow=True,
                fallback_reason=f"change_class={change_class!r} is not eligible for contract surface planning",
                confidence=0.0,
            )

        pack = self._load_pack()
        checkpoint = pack.checkpoint_by_event(_CHECKPOINT_EVENT)
        if checkpoint is None or not checkpoint.prompt_id:
            return ContractSurfacePlan(
                summary="No contract_surface_requested checkpoint declared in this refinement harness.",
                change_class=change_class,
                artifact_kind=artifact_kind,
                fallback_to_workflow=True,
                fallback_reason="missing checkpoint declaration",
                confidence=0.0,
            )

        prompt = pack.prompt_by_id(checkpoint.prompt_id)
        if prompt is None:
            return ContractSurfacePlan(
                summary=f"Prompt '{checkpoint.prompt_id}' not found.",
                change_class=change_class,
                artifact_kind=artifact_kind,
                fallback_to_workflow=True,
                fallback_reason="missing prompt",
                confidence=0.0,
            )

        llm_config = self._load_config().resolve_capability_llm_config("contract_surface")
        user_prompt = self._build_user_prompt(
            request=refinement_request,
            routing_decision=routing_decision,
            context_graph_catalog=context_graph_catalog,
        )

        try:
            classification = await self._agent_runner.run(
                agent_name="ContractSurfacePlanner",
                system_prompt=prompt.content,
                user_prompt=user_prompt,
                llm_config=llm_config,
                response_schema=_ContractSurfaceClassification,
            )
        except Exception as exc:
            _logger.warning("Contract surface classification failed: %s", exc)
            return ContractSurfacePlan(
                summary="Contract surface classification failed.",
                change_class=change_class,
                artifact_kind=artifact_kind,
                fallback_to_workflow=True,
                fallback_reason=str(exc),
                confidence=0.0,
            )

        if classification.fallback_to_workflow or classification.confidence < self._CONFIDENCE_THRESHOLD:
            return ContractSurfacePlan(
                summary=classification.summary or "Change is too broad for targeted surface regeneration.",
                change_class=change_class,
                artifact_kind=artifact_kind,
                fallback_to_workflow=True,
                fallback_reason=classification.fallback_reason or "low confidence",
                confidence=float(classification.confidence),
            )

        surfaces = self._resolve_surfaces(
            classification=classification,
            context_graph_catalog=context_graph_catalog,
        )

        if not surfaces:
            return ContractSurfacePlan(
                summary="No contract surfaces could be resolved from the classification.",
                change_class=change_class,
                artifact_kind=artifact_kind,
                fallback_to_workflow=True,
                fallback_reason="empty surface resolution",
                confidence=float(classification.confidence),
            )

        return ContractSurfacePlan(
            surfaces=surfaces,
            summary=classification.summary or f"Targeted update across {len(surfaces)} contract surface(s).",
            change_class=change_class,
            artifact_kind=artifact_kind,
            requires_schema_migration=classification.requires_schema_migration,
            confidence=float(classification.confidence),
            fallback_to_workflow=False,
        )

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
        classification: _ContractSurfaceClassification,
        context_graph_catalog: dict[str, Any] | None,
    ) -> list[ContractSurfaceUpdate]:
        """Resolve each classified surface to canonical file paths and order by dependency."""

        # Build a set of known workspace paths from the context graph catalog for
        # filtering resolved paths to only those that actually exist.
        known_paths: set[str] = set()
        if context_graph_catalog and isinstance(context_graph_catalog.get("file_tree"), list):
            for entry in context_graph_catalog["file_tree"]:
                if isinstance(entry, str):
                    known_paths.add(entry)
        if context_graph_catalog and isinstance(context_graph_catalog.get("candidate_files"), list):
            for cf in context_graph_catalog["candidate_files"]:
                if isinstance(cf, dict) and cf.get("path"):
                    known_paths.add(str(cf["path"]))

        updates: list[ContractSurfaceUpdate] = []
        for entry in classification.surfaces:
            kind = str(entry.kind or "").strip().lower()
            if kind not in _VALID_SURFACE_KINDS:
                _logger.debug("Skipping unknown surface kind %r", kind)
                continue

            target_id = str(entry.target_id or "").strip()
            if not target_id:
                continue

            raw_paths = CONTRACT_SURFACE_CANONICAL_PATHS.get(kind, [])
            resolved_paths = [p.replace("{target_id}", target_id) for p in raw_paths]

            # If we have workspace knowledge, filter to paths that exist.
            # If we don't have workspace knowledge, include all canonical paths.
            if known_paths:
                confirmed_paths = [p for p in resolved_paths if p in known_paths]
                # If nothing confirmed but we had catalog data, keep top canonical
                # paths anyway — the generator will create them if missing.
                if not confirmed_paths:
                    confirmed_paths = resolved_paths[:2]
            else:
                confirmed_paths = resolved_paths

            dep_order = CONTRACT_SURFACE_DEPENDENCY_ORDER.get(kind, 4)

            updates.append(
                ContractSurfaceUpdate(
                    kind=kind,  # type: ignore[arg-type]
                    target_id=target_id,
                    target_kind=str(entry.target_kind or "module").strip() or "module",
                    affected_paths=confirmed_paths,
                    dependency_order=dep_order,
                    rationale=str(entry.rationale or "").strip() or f"{kind} update for {target_id}",
                    confidence=float(entry.confidence),
                    generation_hint=str(entry.generation_hint or "").strip(),
                )
            )

        # Sort by dependency order (stable sort preserves classification ordering within tier).
        updates.sort(key=lambda s: s.dependency_order)
        return updates

    @staticmethod
    def _build_user_prompt(
        *,
        request: RefinementRequest,
        routing_decision: RefinementRoutingDecision,
        context_graph_catalog: dict[str, Any] | None,
    ) -> str:
        payload: dict[str, Any] = {
            "change_class": routing_decision.change_intent.change_class.value,
            "artifact_kind": request.artifact_kind,
            "raw_user_request": request.raw_user_request,
            "workflow_sequence": routing_decision.workflow_sequence,
            "rationale": routing_decision.change_intent.rationale,
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
                            "target_kind": "module|workflow|page",
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
