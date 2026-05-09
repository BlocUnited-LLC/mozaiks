from __future__ import annotations

import json
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from mozaiksai.core.capabilities import get_general_capability_service
from mozaiksai.control_plane.config import ControlPlaneConfig, load_control_plane_config
from mozaiksai.control_plane.contracts import ControlPlaneToolCall, ControlPlaneToolContext
from mozaiksai.control_plane.executor import ControlPlaneToolExecutor
from mozaiksai.control_plane.loader import load_selected_control_plane_pack
from mozaiksai.control_plane.schema import LoadedControlPlanePack


ChangeClassLiteral = Literal["patch", "design", "feature", "core"]
_CHECKPOINT_EVENT = "request_submitted"


class ChangeClassifierResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    change_class: ChangeClassLiteral
    rationale: str = Field(min_length=1)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    signals: list[str] = Field(default_factory=list)


class LLMChangeClassifier:
    """Authoritative LLM-backed refinement change classifier."""

    def __init__(
        self,
        *,
        capability_service: Any = None,
        config_loader: Any = load_control_plane_config,
        pack_loader: Any = load_selected_control_plane_pack,
        tool_executor: Any = None,
    ) -> None:
        self._service = capability_service or get_general_capability_service()
        self._config_loader = config_loader
        self._pack_loader = pack_loader
        self._tool_executor = tool_executor or ControlPlaneToolExecutor(pack_loader=pack_loader)

    async def classify(
        self,
        *,
        artifact_kind: str,
        artifact_key: Optional[str] = None,
        raw_user_request: str,
        declared_change_class: Optional[str] = None,
        artifact_version_id: Optional[str] = None,
        source_surface: Optional[str] = None,
        app_id: Optional[str] = None,
        user_id: Optional[str] = None,
        requested_workflow_id: Optional[str] = None,
        extra: Optional[dict[str, Any]] = None,
    ) -> ChangeClassifierResult:
        control_plane = self._load_config()
        if not control_plane.enabled:
            raise RuntimeError("Control-plane harness is disabled in app/config/ai.json")
        if not control_plane.classifier_enabled():
            raise RuntimeError("Control-plane classifier is disabled in app/config/ai.json")

        llm_config = control_plane.classifier.llm_config or None
        temperature = None
        if isinstance(llm_config, dict) and llm_config.get("temperature") is not None:
            try:
                temperature = float(llm_config["temperature"])
            except Exception:
                temperature = None

        user_prompt = self._build_user_prompt(
            artifact_kind=artifact_kind,
            raw_user_request=raw_user_request,
            declared_change_class=declared_change_class,
            artifact_version_id=artifact_version_id,
            source_surface=source_surface,
            app_id=app_id,
            requested_workflow_id=requested_workflow_id,
            control_plane_context=await self._load_control_plane_context(
                artifact_kind=artifact_kind,
                artifact_key=artifact_key,
                raw_user_request=raw_user_request,
                artifact_version_id=artifact_version_id,
                source_surface=source_surface,
                app_id=app_id,
                user_id=user_id,
                requested_workflow_id=requested_workflow_id,
                extra=extra or {},
            ),
            extra=extra or {},
        )
        response = await self._service.generate_json_completion(
            system_prompt=self._load_system_prompt(),
            user_prompt=user_prompt,
            app_id=app_id,
            user_id=user_id,
            ui_context={"surface": source_surface or "refinement_classifier"},
            llm_config=llm_config,
            temperature=temperature,
        )
        return ChangeClassifierResult.model_validate(response.get("parsed") or {})

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
            raise RuntimeError(f"Classifier prompt '{checkpoint.prompt_id}' was not found in prompts.yaml")
        return prompt.content

    async def _load_control_plane_context(
        self,
        *,
        artifact_kind: str,
        artifact_key: Optional[str],
        raw_user_request: str,
        artifact_version_id: Optional[str],
        source_surface: Optional[str],
        app_id: Optional[str],
        user_id: Optional[str],
        requested_workflow_id: Optional[str],
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
            artifact_kind=artifact_kind or None,
            artifact_key=artifact_key,
            artifact_version_id=artifact_version_id,
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
        artifact_kind: str,
        raw_user_request: str,
        declared_change_class: Optional[str],
        artifact_version_id: Optional[str],
        source_surface: Optional[str],
        app_id: Optional[str],
        requested_workflow_id: Optional[str],
        control_plane_context: dict[str, Any],
        extra: dict[str, Any],
    ) -> str:
        lines = [
            "Classify this Mozaiks refinement request.",
            f"artifact_kind: {artifact_kind or 'unknown'}",
            f"artifact_version_id: {artifact_version_id or 'unknown'}",
            f"requested_workflow_id: {requested_workflow_id or 'unknown'}",
            f"source_surface: {source_surface or 'unknown'}",
            f"app_id: {app_id or 'unknown'}",
            f"user_declared_hint: {declared_change_class or 'none'}",
            f"request: {raw_user_request or ''}",
        ]
        if control_plane_context:
            lines.append("control_plane_context_json:")
            lines.append(json.dumps(control_plane_context, indent=2, sort_keys=True, default=str))
        if extra:
            lines.append(f"extra_context: {extra}")
        lines.append("")
        lines.append("Return a JSON object with this exact shape:")
        lines.append(
            '{"change_class":"patch|design|feature|core","rationale":"...","confidence":0.0,"signals":["..."]}'
        )
        return "\n".join(lines)


_classifier: Optional[LLMChangeClassifier] = None


def get_change_classifier() -> LLMChangeClassifier:
    global _classifier
    if _classifier is None:
        _classifier = LLMChangeClassifier()
    return _classifier
