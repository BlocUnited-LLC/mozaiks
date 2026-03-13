from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ChangeType(str, Enum):
    FOUNDATIONAL = "FOUNDATIONAL"
    STRUCTURAL = "STRUCTURAL"
    FEATURE = "FEATURE"
    SURFACE = "SURFACE"
    UNKNOWN = "UNKNOWN"


class ChangeIntent(BaseModel):
    """Canonical routing contract for free-text or agent-classified changes.

    This is the typed object the universal orchestrator should consume.
    It is reusable across workflows, but only classifier / transfer agents
    should emit it explicitly.
    """

    model_config = ConfigDict(extra="forbid")

    change_type: ChangeType
    change_scope: str
    requires_appspec_revision: bool = False
    requires_replan: bool = False
    requires_new_iteration: bool = False
    target_workflow: Optional[str] = None
    rationale: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("change_scope")
    @classmethod
    def _validate_scope(cls, value: str) -> str:
        scope = str(value or "").strip().lower()
        if not scope:
            raise ValueError("change_scope must be non-empty")
        allowed = {"foundational", "structural", "feature", "surface", "unknown"}
        if scope not in allowed:
            raise ValueError(f"change_scope must be one of {sorted(allowed)}")
        return scope

    @field_validator("target_workflow")
    @classmethod
    def _validate_target_workflow(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        workflow = str(value).strip()
        return workflow or None

    @field_validator("rationale")
    @classmethod
    def _validate_rationale(cls, value: str) -> str:
        return str(value or "").strip()

    @model_validator(mode="after")
    def _align_scope(self) -> "ChangeIntent":
        expected_scope = self.change_type.value.lower()
        if self.change_scope != expected_scope:
            raise ValueError(
                f"change_scope '{self.change_scope}' must match change_type '{expected_scope}'"
            )
        return self

    @classmethod
    def from_change_type(
        cls,
        change_type: ChangeType,
        *,
        rationale: str,
        confidence: float = 0.5,
        target_workflow: Optional[str] = None,
    ) -> "ChangeIntent":
        defaults = _default_intent_for_change_type(change_type)
        resolved_target = target_workflow if target_workflow is not None else defaults["target_workflow"]
        return cls(
            change_type=change_type,
            change_scope=change_type.value.lower(),
            requires_appspec_revision=bool(defaults["requires_appspec_revision"]),
            requires_replan=bool(defaults["requires_replan"]),
            requires_new_iteration=bool(defaults["requires_new_iteration"]),
            target_workflow=resolved_target,
            rationale=rationale,
            confidence=confidence,
        )


@runtime_checkable
class ChangeClassifierAdapter(Protocol):
    async def classify(self, *, text: str, context: Optional[Dict[str, Any]] = None) -> ChangeIntent:
        ...


class HeuristicChangeClassifier:
    """Default engine-agnostic classifier adapter.

    Runtime callers can replace this adapter with an LLM-backed implementation
    without changing orchestration routing logic.
    """

    async def classify(self, *, text: str, context: Optional[Dict[str, Any]] = None) -> ChangeIntent:
        raw = str(text or "").strip().lower()
        if not raw:
            return ChangeIntent.from_change_type(
                ChangeType.UNKNOWN,
                rationale="empty input",
                confidence=0.0,
            )

        foundational_markers = {"restart", "start over", "new direction", "new goal", "pivot"}
        structural_markers = {"architecture", "database", "tech stack", "microservice", "module", "workflow"}
        feature_markers = {"add", "feature", "support", "implement", "build"}
        surface_markers = {"rename", "color", "copy", "text", "label", "button"}

        if any(token in raw for token in foundational_markers):
            return ChangeIntent.from_change_type(
                ChangeType.FOUNDATIONAL,
                rationale="foundational marker match",
                confidence=0.75,
            )
        if any(token in raw for token in structural_markers):
            return ChangeIntent.from_change_type(
                ChangeType.STRUCTURAL,
                rationale="structural marker match",
                confidence=0.7,
            )
        if any(token in raw for token in feature_markers):
            return ChangeIntent.from_change_type(
                ChangeType.FEATURE,
                rationale="feature marker match",
                confidence=0.65,
            )
        if any(token in raw for token in surface_markers):
            return ChangeIntent.from_change_type(
                ChangeType.SURFACE,
                rationale="surface marker match",
                confidence=0.6,
            )
        return ChangeIntent.from_change_type(
            ChangeType.FEATURE,
            rationale="default feature classification",
            confidence=0.5,
        )


def _default_intent_for_change_type(change_type: ChangeType) -> Dict[str, Any]:
    if change_type is ChangeType.FOUNDATIONAL:
        return {
            "requires_appspec_revision": True,
            "requires_replan": True,
            "requires_new_iteration": True,
            "target_workflow": "ValueEngine",
        }
    if change_type is ChangeType.STRUCTURAL:
        return {
            "requires_appspec_revision": False,
            "requires_replan": True,
            "requires_new_iteration": False,
            "target_workflow": "BuildApp",
        }
    if change_type is ChangeType.FEATURE:
        return {
            "requires_appspec_revision": False,
            "requires_replan": True,
            "requires_new_iteration": False,
            "target_workflow": "BuildApp",
        }
    if change_type is ChangeType.SURFACE:
        return {
            "requires_appspec_revision": False,
            "requires_replan": False,
            "requires_new_iteration": False,
            "target_workflow": "BuildApp",
        }
    return {
        "requires_appspec_revision": False,
        "requires_replan": False,
        "requires_new_iteration": False,
        "target_workflow": None,
    }


_classifier: Optional[ChangeClassifierAdapter] = None


def get_change_classifier() -> ChangeClassifierAdapter:
    global _classifier
    if _classifier is None:
        _classifier = HeuristicChangeClassifier()
    return _classifier


def set_change_classifier(classifier: Optional[ChangeClassifierAdapter]) -> None:
    global _classifier
    _classifier = classifier


__all__ = [
    "ChangeType",
    "ChangeIntent",
    "ChangeClassifierAdapter",
    "HeuristicChangeClassifier",
    "get_change_classifier",
    "set_change_classifier",
]
