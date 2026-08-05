from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from mozaiksai.control_plane.config import ControlPlaneConfig, load_control_plane_config
from mozaiksai.control_plane.contracts import ControlPlaneToolCall, ControlPlaneToolContext
from mozaiksai.control_plane.executor import ControlPlaneToolExecutor
from mozaiksai.control_plane.loader import load_selected_refinement_harness
from mozaiksai.control_plane.schema import LoadedControlPlanePack
from mozaiksai.core.adapters.ag2_agent_runner import AG2StructuredAgentRunner

ChangeClassLiteral = Literal["patch", "design", "feature", "core"]
_CHECKPOINT_EVENT = "request_submitted"


class ChangeClassifierResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    change_class: ChangeClassLiteral
    rationale: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    signals: list[str]


class LLMChangeClassifier:
    """Authoritative AG2-backed refinement change classifier."""

    def __init__(
        self,
        *,
        agent_factory: Callable[[str, dict[str, Any]], Any] | None = None,
        agent_runner: AG2StructuredAgentRunner | None = None,
        config_loader: Any = load_control_plane_config,
        pack_loader: Any = load_selected_refinement_harness,
        tool_executor: Any = None,
    ) -> None:
        self._agent_runner = agent_runner or AG2StructuredAgentRunner(agent_factory=agent_factory)
        self._config_loader = config_loader
        self._pack_loader = pack_loader
        self._tool_executor = tool_executor or ControlPlaneToolExecutor(pack_loader=pack_loader)

    async def classify(
        self,
        *,
        build_family: str | None = None,
        build_key: str | None = None,
        raw_user_request: str,
        declared_change_class: str | None = None,
        build_record_id: str | None = None,
        source_surface: str | None = None,
        app_id: str | None = None,
        user_id: str | None = None,
        requested_workflow_id: str | None = None,
        extra: dict[str, Any] | None = None,
        artifact_kind: str | None = None,
        artifact_key: str | None = None,
        artifact_version_id: str | None = None,
    ) -> ChangeClassifierResult:
        build_family = build_family or artifact_kind
        build_key = build_key or artifact_key
        build_record_id = build_record_id or artifact_version_id
        if not build_family:
            raise TypeError("classify requires build_family (artifact_kind alias accepted for transition)")

        control_plane = self._load_config()
        if not control_plane.enabled:
            raise RuntimeError("Refinement Engine is disabled in app/config/refinement_policy.yaml")
        if not control_plane.classifier_enabled():
            raise RuntimeError("Refinement classifier is disabled in app/config/refinement_policy.yaml")

        llm_config = control_plane.resolve_capability_llm_config("classifier") or {}

        user_prompt = self._build_user_prompt(
            build_family=build_family,
            raw_user_request=raw_user_request,
            declared_change_class=declared_change_class,
            build_record_id=build_record_id,
            source_surface=source_surface,
            app_id=app_id,
            requested_workflow_id=requested_workflow_id,
            control_plane_context=await self._load_control_plane_context(
                build_family=build_family,
                build_key=build_key,
                raw_user_request=raw_user_request,
                build_record_id=build_record_id,
                source_surface=source_surface,
                app_id=app_id,
                user_id=user_id,
                requested_workflow_id=requested_workflow_id,
                extra=extra or {},
            ),
            extra=extra or {},
        )

        result = await self._agent_runner.run(
            agent_name="ChangeClassifier",
            system_prompt=self._load_system_prompt(),
            user_prompt=user_prompt,
            llm_config=llm_config,
            response_schema=ChangeClassifierResult,
        )
        return result

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
            raise RuntimeError(f"Classifier prompt '{checkpoint.prompt_id}' was not found in prompts.yaml")
        return prompt.content

    async def _load_control_plane_context(
        self,
        *,
        build_family: str,
        build_key: str | None,
        raw_user_request: str,
        build_record_id: str | None,
        source_surface: str | None,
        app_id: str | None,
        user_id: str | None,
        requested_workflow_id: str | None,
        extra: dict[str, Any],
    ) -> dict[str, Any]:
        pack = self._load_pack()
        checkpoint = pack.checkpoint_by_event(_CHECKPOINT_EVENT)
        if checkpoint is None or not checkpoint.tool_ids:
            return {}

        context = ControlPlaneToolContext(
            checkpoint=_CHECKPOINT_EVENT,
            app_id=app_id,
            user_id=user_id,
            build_family=build_family or None,
            build_key=build_key,
            build_record_id=build_record_id,
            requested_workflow_id=requested_workflow_id,
            source_surface=source_surface,
            raw_user_request=raw_user_request or "",
            extra=dict(extra or {}),
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
        build_family: str,
        raw_user_request: str,
        declared_change_class: str | None,
        build_record_id: str | None,
        source_surface: str | None,
        app_id: str | None,
        requested_workflow_id: str | None,
        control_plane_context: dict[str, Any],
        extra: dict[str, Any],
    ) -> str:
        lines = [
            "Classify this Mozaiks refinement request.",
            f"build_family: {build_family or 'unknown'}",
            f"build_record_id: {build_record_id or 'unknown'}",
            f"requested_workflow_id: {requested_workflow_id or 'unknown'}",
            f"source_surface: {source_surface or 'unknown'}",
            f"app_id: {app_id or 'unknown'}",
            f"user_declared_hint: {declared_change_class or 'none'}",
            f"request: {raw_user_request or ''}",
        ]
        if control_plane_context:
            lines.append("refinement_context_json:")
            lines.append(json.dumps(control_plane_context, indent=2, sort_keys=True, default=str))
        if extra:
            lines.append(f"extra_context: {extra}")
        return "\n".join(lines)


_classifier: LLMChangeClassifier | None = None


def get_change_classifier() -> LLMChangeClassifier:
    global _classifier
    if _classifier is None:
        _classifier = LLMChangeClassifier()
    return _classifier
