from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from mozaiksai.core.capabilities import get_general_capability_service
from mozaiksai.core.control_plane import ControlPlaneConfig, load_control_plane_config


ChangeClassLiteral = Literal["patch", "design", "feature", "core"]


class ChangeClassifierResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    change_class: ChangeClassLiteral
    rationale: str = Field(min_length=1)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    signals: list[str] = Field(default_factory=list)


class LLMChangeClassifier:
    """Authoritative LLM-backed refinement change classifier."""

    _SYSTEM_PROMPT = """You are the authoritative Mozaiks refinement change classifier.

Classify a natural-language build refinement request into exactly one of:
- patch: targeted fix or localized correction within the current artifact boundary
- design: visual, UX, information architecture, schema, or design-system revision without changing the product concept
- feature: additive capability within the current product direction
- core: change in value proposition, target user, product identity, business model, architecture direction, or anything that should reopen ValueEngine

Rules:
- Use the request text as the primary signal.
- Treat a user-declared hint as advisory only, never authoritative.
- Be conservative about `core`, but choose it when the request changes what the product fundamentally is.
- Return JSON only.
- Do not include markdown fences.
- Keep `signals` short and semantic, such as "new_capability", "concept_shift", "workflow_expansion", "visual_redesign", "bug_fix", "target_user_change".
"""

    def __init__(
        self,
        *,
        capability_service: Any = None,
        config_loader: Any = load_control_plane_config,
    ) -> None:
        self._service = capability_service or get_general_capability_service()
        self._config_loader = config_loader

    async def classify(
        self,
        *,
        artifact_kind: str,
        raw_user_request: str,
        declared_change_class: Optional[str] = None,
        artifact_version_id: Optional[str] = None,
        source_surface: Optional[str] = None,
        app_id: Optional[str] = None,
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
            extra=extra or {},
        )
        response = await self._service.generate_json_completion(
            system_prompt=self._SYSTEM_PROMPT,
            user_prompt=user_prompt,
            app_id=app_id,
            user_id=None,
            ui_context={"surface": source_surface or "refinement_classifier"},
            llm_config=llm_config,
            temperature=temperature,
        )
        return ChangeClassifierResult.model_validate(response.get("parsed") or {})

    def _load_config(self) -> ControlPlaneConfig:
        config = self._config_loader()
        return config if isinstance(config, ControlPlaneConfig) else ControlPlaneConfig.model_validate(config)

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
