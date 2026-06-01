"""Surface-by-surface execution of a ContractSurfacePlan.

The SurfaceRegenerationWorker is the execution layer for the
targeted_regeneration harness path.

When the ContractSurfacePlanner identifies which contract surfaces need
updating and in what dependency order, this worker drives the actual
file generation — one surface at a time, accumulating changes so that
later surfaces (e.g. page_binding) see the updated files from earlier
surfaces (e.g. module_action).

Each surface call:
1. Loads current file content for the surface's affected_paths from the
   provided workspace snapshot (empty string for new files).
2. Calls the LLM with the coding_refinement_system prompt and a
   surface-specific task context (generation_hint, rationale, kind).
3. Validates that the LLM only writes to declared affected_paths.
4. Accumulates the updated files for the next surface in the plan.

File accumulation means: if surface 1 updates schemas.py and surface 2
needs schemas.py to write handler.py, surface 2 sees the updated schemas.py.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from mozaiksai.control_plane.config import ControlPlaneConfig, load_control_plane_config
from mozaiksai.control_plane.contracts import (
    ContractSurfacePlan,
    ContractSurfaceUpdate,
    SurfaceExecutionRecord,
    SurfacePlanExecutionResult,
)
from mozaiksai.control_plane.loader import load_selected_control_plane_pack
from mozaiksai.control_plane.schema import LoadedControlPlanePack
from mozaiksai.core.capabilities import get_general_capability_service

from .refinement_router import RefinementRequest, RefinementRoutingDecision

_logger = logging.getLogger("mozaiksai.control_plane.implementations.surface_regeneration_worker")

# Reuse the coding_requested checkpoint prompt — it provides the right
# structured-output-first file generation guidance without surface-specific
# assumptions baked in.
_CODING_CHECKPOINT_EVENT = "coding_requested"


class SurfaceRegenerationWorker:
    """Executes a ContractSurfacePlan surface by surface.

    Eligible for feature and design change classes on app_bundle and
    workflow_bundle artifacts — unlike the ScopedRefinementCodingWorker
    which is patch-only.

    Dependency ordering is the caller's responsibility: surfaces in
    plan.surfaces must already be sorted by dependency_order (the planner
    guarantees this).
    """

    def __init__(
        self,
        *,
        capability_service: Any = None,
        config_loader: Any = load_control_plane_config,
        pack_loader: Any = load_selected_control_plane_pack,
    ) -> None:
        self._service = capability_service or get_general_capability_service()
        self._config_loader = config_loader
        self._pack_loader = pack_loader

    async def execute_plan(
        self,
        *,
        plan: ContractSurfacePlan,
        refinement_request: RefinementRequest,
        routing_decision: RefinementRoutingDecision,
        workspace_files: dict[str, str] | None = None,
    ) -> SurfacePlanExecutionResult:
        """Execute every surface in plan order and return merged file changes.

        workspace_files supplies the current content of workspace files by
        relative path. Files absent from workspace_files default to empty
        string (new file). Accumulated outputs from earlier surfaces override
        workspace_files for later surfaces so dependent surfaces see the
        latest generated content.
        """
        system_prompt = self._load_system_prompt()
        llm_config = self._load_config().resolve_capability_llm_config("contract_surface")

        accumulated: dict[str, str] = {}
        records: list[SurfaceExecutionRecord] = []
        succeeded = 0

        for surface in plan.surfaces:
            current_files = self._build_current_files(
                surface=surface,
                accumulated=accumulated,
                workspace_files=workspace_files,
            )

            applied_files, error = await self._execute_surface(
                surface=surface,
                refinement_request=refinement_request,
                routing_decision=routing_decision,
                current_files=current_files,
                system_prompt=system_prompt,
                llm_config=llm_config,
            )

            if applied_files:
                accumulated.update(applied_files)
                succeeded += 1
                records.append(
                    SurfaceExecutionRecord(
                        kind=surface.kind,
                        target_id=surface.target_id,
                        status="success",
                        applied_files=applied_files,
                    )
                )
            else:
                records.append(
                    SurfaceExecutionRecord(
                        kind=surface.kind,
                        target_id=surface.target_id,
                        status="failed",
                        error=error,
                    )
                )
            _logger.info(
                "[surface_plan] %s/%s → %s",
                surface.kind,
                surface.target_id,
                "success" if applied_files else "failed",
            )

        total = len(plan.surfaces)
        if total == 0 or succeeded == 0:
            final_status: str = "failed"
        elif succeeded == total:
            final_status = "success"
        else:
            final_status = "partial"

        return SurfacePlanExecutionResult(
            status=final_status,  # type: ignore[arg-type]
            surfaces_executed=records,
            all_files=accumulated,
            requires_schema_migration=plan.requires_schema_migration,
            metadata={
                "surfaces_total": total,
                "surfaces_succeeded": succeeded,
                "surfaces_failed": total - succeeded,
                "change_class": plan.change_class,
                "artifact_kind": plan.artifact_kind,
            },
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_current_files(
        *,
        surface: ContractSurfaceUpdate,
        accumulated: dict[str, str],
        workspace_files: dict[str, str] | None,
    ) -> dict[str, str]:
        """Resolve current file content for this surface's affected_paths.

        Priority: accumulated (from earlier surfaces) > workspace_files > "" (new file).
        """
        result: dict[str, str] = {}
        for path in surface.affected_paths:
            if path in accumulated:
                result[path] = accumulated[path]
            elif workspace_files and path in workspace_files:
                result[path] = workspace_files[path]
            else:
                result[path] = ""
        return result

    async def _execute_surface(
        self,
        *,
        surface: ContractSurfaceUpdate,
        refinement_request: RefinementRequest,
        routing_decision: RefinementRoutingDecision,
        current_files: dict[str, str],
        system_prompt: str,
        llm_config: Optional[dict[str, Any]],
    ) -> tuple[dict[str, str], Optional[str]]:
        user_prompt = self._build_user_prompt(
            surface=surface,
            refinement_request=refinement_request,
            routing_decision=routing_decision,
            current_files=current_files,
        )
        try:
            response = await self._service.generate_json_completion(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                app_id=str(refinement_request.app_id or "").strip() or None,
                user_id=str(refinement_request.user_id or "").strip() or None,
                ui_context={"surface": "surface_regeneration", "kind": surface.kind},
                llm_config=llm_config,
                temperature=0.1,
            )
            updated_files = self._extract_updated_files(
                response=response.get("parsed") or {},
                allowed_paths=set(surface.affected_paths),
            )
            return updated_files, None
        except Exception as exc:
            _logger.warning(
                "Surface execution failed for %s/%s: %s",
                surface.kind,
                surface.target_id,
                exc,
            )
            return {}, str(exc)

    @staticmethod
    def _extract_updated_files(
        *,
        response: dict[str, Any],
        allowed_paths: set[str],
    ) -> dict[str, str]:
        """Extract and validate updated_files from the LLM response."""
        raw_files = response.get("updated_files")
        if not isinstance(raw_files, dict) or not raw_files:
            raise ValueError("LLM returned no updated_files for surface regeneration")

        result: dict[str, str] = {}
        invalid: list[str] = []
        for path, content in raw_files.items():
            safe_path = str(path or "").strip().replace("\\", "/")
            if not safe_path:
                continue
            if safe_path not in allowed_paths:
                invalid.append(safe_path)
                continue
            result[safe_path] = str(content or "")

        if invalid:
            raise ValueError(
                "LLM wrote paths outside declared surface scope: "
                + ", ".join(sorted(invalid))
            )
        if not result:
            raise ValueError("LLM returned no files within the declared surface scope")

        return result

    @staticmethod
    def _build_user_prompt(
        *,
        surface: ContractSurfaceUpdate,
        refinement_request: RefinementRequest,
        routing_decision: RefinementRoutingDecision,
        current_files: dict[str, str],
    ) -> str:
        new_paths = [p for p, c in current_files.items() if not c.strip()]
        existing_paths = [p for p, c in current_files.items() if c.strip()]

        payload: dict[str, Any] = {
            "task": "targeted_surface_regeneration",
            "surface_kind": surface.kind,
            "target_id": surface.target_id,
            "target_kind": surface.target_kind,
            "generation_hint": surface.generation_hint,
            "rationale": surface.rationale,
            "change_class": routing_decision.change_intent.change_class.value,
            "user_request": refinement_request.raw_user_request,
            "affected_paths": surface.affected_paths,
            "new_paths": new_paths,
            "existing_paths": existing_paths,
            "current_files": current_files,
        }

        lines = [
            "Apply a targeted Mozaiks contract surface update.",
            "Return JSON only.",
            "payload_json:",
            json.dumps(payload, indent=2, sort_keys=True, default=str),
            "",
            "Return a JSON object with this exact shape:",
            json.dumps(
                {
                    "summary": "one-line summary of the changes made",
                    "updated_files": {
                        "relative/path/to/file.py": "full updated file content here",
                    },
                    "rationale": "why these specific changes implement the generation_hint",
                },
                indent=2,
            ),
            "",
            "Rules:",
            "- Include ALL affected_paths in updated_files, not only the ones you changed.",
            "- For new_paths (empty current content), write the complete file.",
            "- Do not write paths outside affected_paths.",
        ]
        return "\n".join(lines)

    def _load_config(self) -> ControlPlaneConfig:
        config = self._config_loader()
        return config if isinstance(config, ControlPlaneConfig) else ControlPlaneConfig.model_validate(config)

    def _load_pack(self) -> LoadedControlPlanePack:
        pack = self._pack_loader()
        return pack if isinstance(pack, LoadedControlPlanePack) else LoadedControlPlanePack.model_validate(pack)

    def _load_system_prompt(self) -> str:
        pack = self._load_pack()
        checkpoint = pack.checkpoint_by_event(_CODING_CHECKPOINT_EVENT)
        if checkpoint is None or not checkpoint.prompt_id:
            raise RuntimeError(
                f"Selected control-plane pack does not declare a '{_CODING_CHECKPOINT_EVENT}' "
                "checkpoint with prompt_id — required for surface regeneration"
            )
        prompt = pack.prompt_by_id(checkpoint.prompt_id)
        if prompt is None:
            raise RuntimeError(
                f"Coding prompt '{checkpoint.prompt_id}' not found in control-plane pack "
                "— required for surface regeneration"
            )
        return prompt.content


_surface_regeneration_worker: SurfaceRegenerationWorker | None = None


def get_surface_regeneration_worker() -> SurfaceRegenerationWorker:
    global _surface_regeneration_worker
    if _surface_regeneration_worker is None:
        _surface_regeneration_worker = SurfaceRegenerationWorker()
    return _surface_regeneration_worker
