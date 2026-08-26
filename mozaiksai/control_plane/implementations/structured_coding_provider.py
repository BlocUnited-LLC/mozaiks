from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from mozaiksai.control_plane.config import ControlPlaneConfig, load_control_plane_config
from mozaiksai.control_plane.contracts import (
    CodingWorkerPlan,
    CodingWorkerRequest,
    ControlPlaneToolCall,
    ControlPlaneToolContext,
    FileUpdate,
    ProposedFileChange,
    StagedPatchProposal,
    safe_artifact_relpath,
)
from mozaiksai.control_plane.executor import ControlPlaneToolExecutor
from mozaiksai.control_plane.loader import load_selected_refinement_harness
from mozaiksai.control_plane.schema import LoadedControlPlanePack
from mozaiksai.core.adapters.ag2_agent_runner import AG2StructuredAgentRunner

logger = logging.getLogger(__name__)

_CHECKPOINT_EVENT = "coding_requested"
_MODEL_VALIDATION_COMMAND_MAX_LENGTH = 240


class StructuredOutputCodingProvider:
    """Single-shot structured-output coding provider.

    Implements :class:`~mozaiksai.control_plane.ports.CodingExecutionProvider`
    with one strict structured-output model turn: the scoped file contents are
    inlined into the prompt, the model returns a full-file-rewrite
    :class:`CodingWorkerPlan`, and the plan is normalized and contained to the
    explicitly scoped paths before it becomes a :class:`StagedPatchProposal`.
    """

    provider_id = "control_plane_coding"

    def __init__(
        self,
        *,
        agent_factory: Any = None,
        agent_runner: AG2StructuredAgentRunner | None = None,
        config_loader: Any = load_control_plane_config,
        pack_loader: Any = load_selected_refinement_harness,
        tool_executor: Any = None,
    ) -> None:
        self._agent_runner = agent_runner or AG2StructuredAgentRunner(agent_factory=agent_factory)
        self._config_loader = config_loader
        self._pack_loader = pack_loader
        self._tool_executor = tool_executor or ControlPlaneToolExecutor(pack_loader=pack_loader)

    async def execute(self, request: CodingWorkerRequest) -> StagedPatchProposal:
        tool_context_loaded = False
        try:
            llm_config = self._load_config().resolve_capability_llm_config("coding") or {}
            system_prompt = self._load_system_prompt()
            control_plane_context = await self._load_control_plane_context(request)
            tool_context_loaded = bool(control_plane_context)
            user_prompt = self._build_user_prompt(request=request, control_plane_context=control_plane_context)
            plan = await self._agent_runner.run(
                agent_name="CodingWorker",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                llm_config=llm_config,
                response_schema=CodingWorkerPlan,
            )
            resolved_plan = self._normalize_plan(plan)
            applied_files = self._resolve_updated_files(request=request, plan=resolved_plan)
        except Exception as exc:
            return StagedPatchProposal(
                proposal_id=uuid.uuid4().hex,
                provider_id=self.provider_id,
                status="failed",
                error=str(exc),
                tool_context_loaded=tool_context_loaded,
            )

        return StagedPatchProposal(
            proposal_id=uuid.uuid4().hex,
            provider_id=self.provider_id,
            status="completed",
            summary=resolved_plan.summary,
            rationale=resolved_plan.rationale,
            changed_files=[
                ProposedFileChange(
                    path=path,
                    op="update" if path in request.files else "create",
                    content=content,
                )
                for path, content in applied_files.items()
            ],
            owned_paths=list(resolved_plan.owned_paths),
            validation_strategy_hint=resolved_plan.validation_strategy,
            validation_commands=list(resolved_plan.validation_commands),
            start_preview=bool(resolved_plan.start_preview),
            needs_human_review=bool(resolved_plan.needs_human_review),
            tool_context_loaded=tool_context_loaded,
        )

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
                f"Selected refinement harness does not declare a '{_CHECKPOINT_EVENT}' checkpoint with prompt_id"
            )
        prompt = pack.prompt_by_id(checkpoint.prompt_id)
        if prompt is None:
            raise RuntimeError(f"Coding prompt '{checkpoint.prompt_id}' was not found in prompts.yaml")
        return prompt.content

    async def _load_control_plane_context(self, request: CodingWorkerRequest) -> dict[str, Any]:
        pack = self._load_pack()
        checkpoint = pack.checkpoint_by_event(_CHECKPOINT_EVENT)
        if checkpoint is None or not checkpoint.tool_ids:
            return {}

        context = ControlPlaneToolContext(
            checkpoint=_CHECKPOINT_EVENT,
            app_id=request.app_id,
            user_id=request.user_id,
            build_family=request.build_family,
            build_key=request.build_key,
            build_record_id=request.build_record_id,
            requested_workflow_id=request.requested_workflow_id,
            source_surface=request.source_surface,
            raw_user_request=request.raw_user_request,
            extra=dict(request.metadata or {}),
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
                results[tool_id] = {"error": result.error or "tool_execution_failed"}
        return results

    @staticmethod
    def _normalize_plan(plan: CodingWorkerPlan) -> CodingWorkerPlan:
        owned_paths = []
        seen_owned: set[str] = set()
        for raw_path in plan.owned_paths:
            safe = safe_artifact_relpath(raw_path)
            if safe and safe not in seen_owned:
                owned_paths.append(safe)
                seen_owned.add(safe)

        seen_updated: set[str] = set()
        normalized_files: list[FileUpdate] = []
        for file_update in plan.updated_files or []:
            safe = safe_artifact_relpath(file_update.path)
            if not safe or safe in seen_updated:
                continue
            normalized_files.append(FileUpdate(path=safe, content=str(file_update.content)))
            seen_updated.add(safe)
            if safe not in seen_owned:
                owned_paths.append(safe)
                seen_owned.add(safe)

        commands = []
        for raw_command in plan.validation_commands or []:
            command = str(raw_command or "").strip()
            if command and StructuredOutputCodingProvider._safe_model_validation_command_hint(command):
                commands.append(command)
            elif command:
                logger.warning(
                    "CODING_WORKER: discarded unsafe model validation_command hint: %r",
                    command,
                )

        return plan.model_copy(
            update={
                "owned_paths": owned_paths,
                "updated_files": normalized_files,
                "validation_commands": commands,
            }
        )

    @staticmethod
    def _resolve_updated_files(*, request: CodingWorkerRequest, plan: CodingWorkerPlan) -> dict[str, str]:
        if not plan.updated_files:
            raise ValueError("coding worker returned no updated_files for the scoped refinement")

        files_as_dict = {fu.path: fu.content for fu in plan.updated_files}
        allowed_paths = set(request.files.keys())
        invalid_paths = [path for path in files_as_dict if path not in allowed_paths]
        if invalid_paths:
            raise ValueError(
                "coding worker attempted to edit paths outside the explicit scoped files: "
                + ", ".join(sorted(invalid_paths))
            )

        if plan.owned_paths:
            outside_owned = [path for path in files_as_dict if path not in set(plan.owned_paths)]
            if outside_owned:
                raise ValueError(
                    "coding worker returned updated_files outside the declared owned_paths: "
                    + ", ".join(sorted(outside_owned))
                )

        return {path: str(content) for path, content in files_as_dict.items()}

    @staticmethod
    def _build_user_prompt(
        *,
        request: CodingWorkerRequest,
        control_plane_context: dict[str, Any],
    ) -> str:
        payload = {
            "build_family": request.build_family,
            "build_key": request.build_key,
            "build_record_id": request.build_record_id,
            "requested_workflow_id": request.requested_workflow_id,
            "change_class": request.change_class,
            "source_surface": request.source_surface,
            "request": request.raw_user_request,
            "file_paths": sorted(request.files.keys()),
            "input_files": request.files,
            "validation_strategy_hint": request.validation_strategy or "auto",
            "start_preview_requested": bool(request.start_preview),
            "context_seed": request.context_seed,
            "metadata": request.metadata,
            "refinement_context": control_plane_context,
        }
        lines = [
            "Plan a scoped coding refinement for this Mozaiks artifact request.",
            "Return JSON only.",
            "payload_json:",
            json.dumps(payload, indent=2, sort_keys=True, default=str),
            "",
            "Return a JSON object with this exact shape:",
            (
                '{"summary":"...","owned_paths":["..."],'
                '"updated_files":[{"path":"relative/path","content":"full file content"}],'
                '"validation_strategy":"skip|local",'
                '"validation_commands":["..."],"start_preview":false,'
                '"needs_human_review":false,"rationale":"..."}'
            ),
        ]
        return "\n".join(lines)

    @staticmethod
    def _safe_model_validation_command_hint(command: str) -> bool:
        if not command or "\x00" in command:
            return False
        if len(command) > _MODEL_VALIDATION_COMMAND_MAX_LENGTH:
            return False
        return not any(char in command for char in "\r\n")
