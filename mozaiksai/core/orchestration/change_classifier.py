from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional, Protocol, runtime_checkable


class ChangeType(str, Enum):
    FOUNDATIONAL = "FOUNDATIONAL"
    STRUCTURAL = "STRUCTURAL"
    FEATURE = "FEATURE"
    SURFACE = "SURFACE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ChangeClassification:
    change_type: ChangeType
    rationale: str
    confidence: float = 0.5


@runtime_checkable
class ChangeClassifierAdapter(Protocol):
    async def classify(self, *, text: str, context: Optional[Dict[str, Any]] = None) -> ChangeClassification:
        ...


class HeuristicChangeClassifier:
    """Default engine-agnostic classifier adapter.

    Runtime callers can replace this adapter with an LLM-backed implementation
    without changing orchestration routing logic.
    """

    async def classify(self, *, text: str, context: Optional[Dict[str, Any]] = None) -> ChangeClassification:
        raw = str(text or "").strip().lower()
        if not raw:
            return ChangeClassification(ChangeType.UNKNOWN, "empty input", confidence=0.0)

        foundational_markers = {"restart", "start over", "new direction", "new goal", "pivot"}
        structural_markers = {"architecture", "database", "tech stack", "microservice", "module", "workflow"}
        feature_markers = {"add", "feature", "support", "implement", "build"}
        surface_markers = {"rename", "color", "copy", "text", "label", "button"}

        if any(token in raw for token in foundational_markers):
            return ChangeClassification(ChangeType.FOUNDATIONAL, "foundational marker match", confidence=0.75)
        if any(token in raw for token in structural_markers):
            return ChangeClassification(ChangeType.STRUCTURAL, "structural marker match", confidence=0.7)
        if any(token in raw for token in feature_markers):
            return ChangeClassification(ChangeType.FEATURE, "feature marker match", confidence=0.65)
        if any(token in raw for token in surface_markers):
            return ChangeClassification(ChangeType.SURFACE, "surface marker match", confidence=0.6)
        return ChangeClassification(ChangeType.FEATURE, "default feature classification", confidence=0.5)


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
    "ChangeClassification",
    "ChangeClassifierAdapter",
    "HeuristicChangeClassifier",
    "get_change_classifier",
    "set_change_classifier",
]

