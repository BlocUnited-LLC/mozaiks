from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, create_model

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
from mozaiksai.core.usage.context import resolve_auxiliary_usage_context

logger = logging.getLogger(__name__)

_CHECKPOINT_EVENT = "coding_requested"
_MODEL_VALIDATION_COMMAND_MAX_LENGTH = 240


class _TextReplacement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    old_text: str = Field(min_length=1, description="Exact unique substring of the original scoped file.")
    new_text: str = Field(description="Literal replacement text; use an empty string to remove the matched text.")


class _AnchoredFileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    replacements: list[_TextReplacement] = Field(min_length=1)

    def apply(self, original: str) -> str:
        spans = []
        for edit in self.replacements:
            start = original.find(edit.old_text)
            if start < 0 or original.find(edit.old_text, start + 1) >= 0:
                raise ValueError(f"{self.path}: old_text must match exactly once in the original scoped file")
            if edit.old_text == edit.new_text:
                raise ValueError(f"{self.path}: replacement makes no change")
            spans.append((start, start + len(edit.old_text), edit.new_text))

        pieces: list[str] = []
        cursor = 0
        for start, end, replacement in sorted(spans):
            if start < cursor:
                raise ValueError(f"{self.path}: replacement anchors overlap")
            pieces.extend((original[cursor:start], replacement))
            cursor = end
        pieces.append(original[cursor:])
        content = "".join(pieces)
        if content == original:
            raise ValueError(f"{self.path}: replacements make no change")
        return content


class _ProviderPlanFields(CodingWorkerPlan):
    model_config = ConfigDict(from_attributes=True)


# Keep all public plan fields and the complete-file format used by custom packs.
_ProviderCodingPlan = create_model(
    "StructuredCodingEdits", __base__=_ProviderPlanFields,
    updated_files=(list[_AnchoredFileUpdate | FileUpdate], ...),
)


class StructuredOutputCodingProvider:
    """Single-shot structured-output coding provider.

    Implements :class:`~mozaiksai.control_plane.ports.CodingExecutionProvider`
    using exact replacements against the original scoped files or a custom
    pack's complete-file output. Both forms produce the same checked
    :class:`CodingWorkerPlan` before staging.
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
            config = self._load_config().for_refinement_model(request.refinement_model)
            llm_config = config.resolve_capability_llm_config("coding")
            system_prompt = self._load_system_prompt()
            control_plane_context = await self._load_control_plane_context(request)
            tool_context_loaded = bool(control_plane_context)
            user_prompt = self._build_user_prompt(request=request, control_plane_context=control_plane_context)
            plan = await self._agent_runner.run(
                usage_context=resolve_auxiliary_usage_context(
                    app_id=request.app_id, user_id=request.user_id, context=request.usage_context,
                    target_app_id=request.artifact_app_id, run_build_binding=request.run_build_binding,
                ),
                agent_name="CodingWorker",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                llm_config=llm_config,
                response_schema=_ProviderCodingPlan,
            )
            resolved_plan = self._materialize_plan(request=request, plan=plan)
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
                    path=update.path, op="update", content=update.content,
                )
                for update in resolved_plan.updated_files
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
            target_app_id=request.target_app_id,
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
    def _materialize_plan(*, request: CodingWorkerRequest, plan: CodingWorkerPlan) -> CodingWorkerPlan:
        def scoped_path(raw: str) -> str:
            path = safe_artifact_relpath(raw)
            if path is None or path not in request.files:
                raise ValueError(f"coding worker attempted to edit paths outside the explicit scoped files: {raw!r}")
            return path

        owned_paths = [scoped_path(raw) for raw in plan.owned_paths]
        declared = set(owned_paths)
        if len(declared) != len(owned_paths):
            raise ValueError("coding worker returned duplicate owned paths")
        if not plan.updated_files:
            raise ValueError("coding worker returned no updated_files for the scoped refinement")

        updated_files = []
        seen = set()
        for update in plan.updated_files:
            path = scoped_path(update.path)
            if path in seen:
                raise ValueError(f"coding worker returned duplicate updates for {path}")
            if isinstance(update, _AnchoredFileUpdate):
                if path not in declared:
                    raise ValueError(f"anchored update outside the declared owned_paths: {path}")
                content = update.apply(request.files[path])
            else:
                if path not in owned_paths:
                    owned_paths.append(path)
                content = update.content
            updated_files.append(FileUpdate(path=path, content=content))
            seen.add(path)

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

        return CodingWorkerPlan.model_validate({
            **plan.model_dump(exclude={"updated_files"}),
            "owned_paths": owned_paths,
            "updated_files": updated_files,
            "validation_commands": commands,
        })

    @staticmethod
    def _build_user_prompt(
        *,
        request: CodingWorkerRequest,
        control_plane_context: dict[str, Any],
    ) -> str:
        # Routing and resume state stays on the request. The model sees the
        # scoped edit inputs and the evidence selected by checkpoint tools.
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
            "validation_strategy": request.validation_strategy,
            "start_preview_requested": bool(request.start_preview),
            "refinement_context": control_plane_context,
        }
        if request.dependency_graph is not None:
            payload["dependency_graph"] = request.dependency_graph.model_dump(mode="json", exclude_none=True)
        lines = [
            "Plan a scoped coding refinement for this Mozaiks artifact request.",
            "Validation execution is owned by the operator and runtime. Your strategy and command fields are advisory; they cannot choose host execution or skip required checks.",
            "Return JSON only.",
            "payload_json:",
            json.dumps(payload, indent=2, sort_keys=True, default=str, ensure_ascii=False),
            "",
            "Use exact, non-overlapping replacements against the original input_files. Include enough unchanged context in old_text to identify exactly one occurrence.",
            "A file entry may instead contain path and full content when the selected harness requires complete files.",
            "JSON shape for anchored edits:",
            (
                '{"summary":"...","owned_paths":["..."],'
                '"updated_files":[{"path":"relative/path","replacements":[{"old_text":"...","new_text":"..."}]}],'
                '"validation_strategy":"e2b|docker|skip",'
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
