from __future__ import annotations

import json
from typing import Any

from factory_app.control_plane.tools._artifact_workspace import (
    load_artifact_workspace,
    safe_relpath,
)
from mozaiksai.control_plane.config import ControlPlaneConfig, load_control_plane_config
from mozaiksai.control_plane.contracts import (
    ControlPlaneToolCall,
    ControlPlaneToolContext,
    ScopeProposal,
)
from mozaiksai.control_plane.executor import ControlPlaneToolExecutor
from mozaiksai.control_plane.loader import load_selected_control_plane_pack
from mozaiksai.control_plane.schema import LoadedControlPlanePack
from mozaiksai.core.artifacts.store import get_artifact_store
from mozaiksai.core.capabilities import get_general_capability_service

from .refinement_router import RefinementRequest, RefinementRoutingDecision

_CHECKPOINT_EVENT = "scope_requested"


class ArtifactScopeProposer:
    """First-party control-plane scope proposer for coding refinements."""

    def __init__(
        self,
        *,
        capability_service: Any = None,
        config_loader: Any = load_control_plane_config,
        pack_loader: Any = load_selected_control_plane_pack,
        tool_executor: Any = None,
        artifact_store: Any = None,
    ) -> None:
        self._service = capability_service or get_general_capability_service()
        self._config_loader = config_loader
        self._pack_loader = pack_loader
        self._tool_executor = tool_executor or ControlPlaneToolExecutor(pack_loader=pack_loader)
        self._artifact_store = artifact_store

    async def propose(
        self,
        *,
        refinement_request: RefinementRequest,
        routing_decision: RefinementRoutingDecision,
        payload: dict[str, Any] | None = None,
    ) -> ScopeProposal:
        pack = self._load_pack()
        checkpoint = pack.checkpoint_by_event(_CHECKPOINT_EVENT)
        if checkpoint is None or not checkpoint.prompt_id:
            raise RuntimeError(
                f"Selected control-plane profile does not declare a '{_CHECKPOINT_EVENT}' checkpoint with prompt_id"
            )

        llm_config = self._load_config().resolve_capability_llm_config("coding")
        temperature = None
        if isinstance(llm_config, dict) and llm_config.get("temperature") is not None:
            try:
                temperature = float(llm_config["temperature"])
            except Exception:
                temperature = None

        control_plane_context = await self._load_control_plane_context(
            refinement_request=refinement_request,
            routing_decision=routing_decision,
            payload=payload or {},
        )
        response = await self._service.generate_json_completion(
            system_prompt=self._load_system_prompt(),
            user_prompt=self._build_user_prompt(
                refinement_request=refinement_request,
                routing_decision=routing_decision,
                control_plane_context=control_plane_context,
                payload=payload or {},
            ),
            app_id=refinement_request.app_id,
            user_id=None,
            ui_context={"surface": refinement_request.source_surface or "scope_proposer"},
            llm_config=llm_config,
            temperature=temperature,
        )
        proposal = ScopeProposal.model_validate(response.get("parsed") or {})
        proposal = self._normalize_proposal(proposal)
        proposal = await self._apply_available_path_policy(
            proposal=proposal,
            refinement_request=refinement_request,
            control_plane_context=control_plane_context,
        )
        return self._apply_scope_policy(proposal)

    async def materialize_files(
        self,
        *,
        refinement_request: RefinementRequest,
        selected_paths: list[str],
    ) -> dict[str, str]:
        app_id = str(refinement_request.app_id or "").strip()
        artifact_version_id = str(refinement_request.artifact_version_id or "").strip()
        if not app_id or not artifact_version_id:
            return {}

        store = self._artifact_store or get_artifact_store()
        workspace = await load_artifact_workspace(
            artifact_store=store,
            app_id=app_id,
            artifact_version_id=artifact_version_id,
        )
        if not workspace.get("present"):
            return {}

        file_map = workspace["file_map"]
        files: dict[str, str] = {}
        for raw_path in selected_paths:
            safe = safe_relpath(raw_path)
            if safe and safe in file_map:
                files[safe] = file_map[safe]
        return files

    def _load_config(self) -> ControlPlaneConfig:
        config = self._config_loader()
        return config if isinstance(config, ControlPlaneConfig) else ControlPlaneConfig.model_validate(config)

    def _load_pack(self) -> LoadedControlPlanePack:
        pack = self._pack_loader()
        return pack if isinstance(pack, LoadedControlPlanePack) else LoadedControlPlanePack.model_validate(pack)

    def _load_system_prompt(self) -> str:
        pack = self._load_pack()
        checkpoint = pack.checkpoint_by_event(_CHECKPOINT_EVENT)
        if checkpoint is None or not checkpoint.prompt_id:
            raise RuntimeError(
                f"Selected control-plane profile does not declare a '{_CHECKPOINT_EVENT}' checkpoint with prompt_id"
            )
        prompt = pack.prompt_by_id(checkpoint.prompt_id)
        if prompt is None:
            raise RuntimeError(f"Scope prompt '{checkpoint.prompt_id}' was not found in prompts.yaml")
        return prompt.content

    async def _load_control_plane_context(
        self,
        *,
        refinement_request: RefinementRequest,
        routing_decision: RefinementRoutingDecision,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        pack = self._load_pack()
        checkpoint = pack.checkpoint_by_event(_CHECKPOINT_EVENT)
        if checkpoint is None or not checkpoint.tool_ids:
            return {}

        context = ControlPlaneToolContext(
            checkpoint=_CHECKPOINT_EVENT,
            app_id=refinement_request.app_id,
            user_id=refinement_request.user_id,
            artifact_kind=refinement_request.artifact_kind,
            artifact_key=refinement_request.normalized_artifact_key(),
            artifact_version_id=refinement_request.artifact_version_id,
            requested_workflow_id=routing_decision.workflow_id,
            source_surface=refinement_request.source_surface,
            raw_user_request=refinement_request.raw_user_request,
            extra={
                "change_intent": routing_decision.change_intent.model_dump(mode="python"),
                "impact_set": routing_decision.impact_set.model_dump(mode="python"),
                "selected_file_paths": list(payload.get("selected_file_paths") or []),
            },
        )
        results: dict[str, Any] = {}
        for tool_id in checkpoint.tool_ids:
            result = await self._tool_executor.execute_tool(
                ControlPlaneToolCall(tool_id=tool_id, target=_CHECKPOINT_EVENT),
                context=context,
            )
            if result.success:
                results[tool_id] = result.output
            else:
                results[tool_id] = {
                    "error": result.error or "tool_execution_failed",
                    "metadata": dict(result.metadata or {}),
                }
        return results

    @staticmethod
    def _build_user_prompt(
        *,
        refinement_request: RefinementRequest,
        routing_decision: RefinementRoutingDecision,
        control_plane_context: dict[str, Any],
        payload: dict[str, Any],
    ) -> str:
        body = {
            "artifact_kind": refinement_request.artifact_kind,
            "artifact_key": refinement_request.normalized_artifact_key(),
            "artifact_version_id": refinement_request.artifact_version_id,
            "requested_workflow_id": routing_decision.workflow_id,
            "change_class": routing_decision.change_intent.change_class.value,
            "raw_user_request": refinement_request.raw_user_request,
            "source_surface": refinement_request.source_surface,
            "existing_selected_paths": list(payload.get("selected_file_paths") or []),
            "control_plane_context": control_plane_context,
        }
        return "\n".join(
            [
                "Propose the narrowest safe file scope for this Mozaiks coding refinement.",
                "Return JSON only.",
                "payload_json:",
                json.dumps(body, indent=2, sort_keys=True, default=str),
                "",
                "Return a JSON object with this exact shape:",
                (
                    '{"resolution":"scoped_files|clarify|workflow","selected_paths":["..."],'
                    '"rationale":"...","confidence":0.0,"clarification_question":null,"signals":["..."]}'
                ),
            ]
        )

    @staticmethod
    def _normalize_proposal(proposal: ScopeProposal) -> ScopeProposal:
        selected_paths: list[str] = []
        for raw_path in proposal.selected_paths:
            safe = safe_relpath(raw_path)
            if safe and safe not in selected_paths:
                selected_paths.append(safe)
        return proposal.model_copy(update={"selected_paths": selected_paths})

    async def _apply_available_path_policy(
        self,
        *,
        proposal: ScopeProposal,
        refinement_request: RefinementRequest,
        control_plane_context: dict[str, Any],
    ) -> ScopeProposal:
        if proposal.resolution != "scoped_files":
            return proposal

        if not proposal.selected_paths:
            return proposal.model_copy(
                update={
                    "resolution": "clarify",
                    "clarification_question": (
                        proposal.clarification_question
                        or "No concrete file path was selected for this patch. Confirm the target file or use workflow refinement."
                    ),
                    "signals": _append_signal(proposal.signals, "empty_scope_selection"),
                    "rationale": f"{proposal.rationale} No scoped file path was selected.".strip(),
                }
            )

        available_paths = await self._available_workspace_paths(refinement_request)
        if not available_paths:
            available_paths = _paths_from_control_plane_context(control_plane_context)
        if not available_paths:
            return proposal

        selected = [path for path in proposal.selected_paths if path in available_paths]
        unavailable = [path for path in proposal.selected_paths if path not in available_paths]
        if not unavailable:
            return proposal

        rationale = (
            f"{proposal.rationale} Removed unavailable scoped path(s): {', '.join(unavailable)}."
        ).strip()
        signals = _append_signal(proposal.signals, "selected_paths_unavailable")
        if selected:
            return proposal.model_copy(update={"selected_paths": selected, "rationale": rationale, "signals": signals})
        return proposal.model_copy(
            update={
                "resolution": "clarify",
                "selected_paths": [],
                "rationale": rationale,
                "clarification_question": (
                    proposal.clarification_question
                    or "The selected file path is not present in the current artifact workspace. Confirm the exact target file."
                ),
                "signals": signals,
            }
        )

    async def _available_workspace_paths(self, refinement_request: RefinementRequest) -> set[str]:
        app_id = str(refinement_request.app_id or "").strip()
        artifact_version_id = str(refinement_request.artifact_version_id or "").strip()
        if not app_id or not artifact_version_id:
            return set()

        store = self._artifact_store or get_artifact_store()
        workspace = await load_artifact_workspace(
            artifact_store=store,
            app_id=app_id,
            artifact_version_id=artifact_version_id,
        )
        if not workspace.get("present"):
            return set()
        return set(workspace.get("file_map") or {})

    def _apply_scope_policy(self, proposal: ScopeProposal) -> ScopeProposal:
        if proposal.resolution != "scoped_files":
            return proposal

        scope_policy = self._load_pack().policies.scope
        selected_count = len(proposal.selected_paths)
        if selected_count <= scope_policy.max_selected_paths:
            return proposal

        rationale = (
            f"{proposal.rationale} The inferred scope touches {selected_count} files, "
            f"which exceeds the configured control-plane limit of {scope_policy.max_selected_paths}."
        ).strip()
        signals = list(proposal.signals)
        if "scope_limit_exceeded" not in signals:
            signals.append("scope_limit_exceeded")

        if scope_policy.overflow_behavior == "workflow":
            return proposal.model_copy(
                update={
                    "resolution": "workflow",
                    "rationale": rationale,
                    "signals": signals,
                }
            )

        return proposal.model_copy(
            update={
                "resolution": "clarify",
                "rationale": rationale,
                "clarification_question": (
                    proposal.clarification_question
                    or "This patch touches multiple files. Confirm the proposed scope or narrow it further."
                ),
                "signals": signals,
            }
        )


def _paths_from_control_plane_context(value: Any) -> set[str]:
    paths: set[str] = set()

    def visit(item: Any, parent_key: str | None = None) -> None:
        if isinstance(item, dict):
            path_value = item.get("path") or item.get("source_path")
            safe = safe_relpath(path_value) if path_value else None
            if safe:
                paths.add(safe)
            for key in ("file_tree", "selected_file_paths", "related_file_paths"):
                child = item.get(key)
                if isinstance(child, list):
                    for entry in child:
                        visit(entry, key)
            for key in ("candidate_files", "matches", "related_file_previews"):
                child = item.get(key)
                if isinstance(child, list):
                    for entry in child:
                        visit(entry, key)
            for child_key, child in item.items():
                if child_key in {
                    "file_tree",
                    "selected_file_paths",
                    "related_file_paths",
                    "candidate_files",
                    "matches",
                    "related_file_previews",
                }:
                    continue
                if isinstance(child, (dict, list)):
                    visit(child, child_key)
        elif isinstance(item, list):
            for child in item:
                visit(child, parent_key)
        elif parent_key in {"file_tree", "selected_file_paths", "related_file_paths"}:
            safe = safe_relpath(item)
            if safe:
                paths.add(safe)

    visit(value)
    return paths


def _append_signal(signals: list[str], signal: str) -> list[str]:
    out = list(signals or [])
    if signal not in out:
        out.append(signal)
    return out


_scope_proposer: ArtifactScopeProposer | None = None


def get_scope_proposer() -> ArtifactScopeProposer:
    global _scope_proposer
    if _scope_proposer is None:
        _scope_proposer = ArtifactScopeProposer()
    return _scope_proposer
