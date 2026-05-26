from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ValidationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    completed: list[str] = Field(default_factory=list)
    failed: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    checked_at: str | None = None
    source: str | None = None

    def completed_names(self) -> set[str]:
        return {_normalize_name(name) for name in self.completed if _normalize_name(name)}

    def failed_names(self) -> set[str]:
        return {_normalize_name(name) for name in self.failed if _normalize_name(name)}

    def artifact_names(self) -> set[str]:
        return {_normalize_artifact_name(name) for name in self.artifacts if _normalize_artifact_name(name)}


def _normalize_name(value: str) -> str:
    return str(value or "").strip().lower()


def _normalize_artifact_name(value: str) -> str:
    return str(value or "").replace("\\", "/").strip().lower()


def normalize_validation_evidence(value: ValidationEvidence | dict[str, Any] | list[str] | None) -> ValidationEvidence:
    if isinstance(value, ValidationEvidence):
        return value
    if value is None:
        return ValidationEvidence()
    if isinstance(value, list):
        return ValidationEvidence(completed=[str(item) for item in value])
    if isinstance(value, dict):
        return ValidationEvidence.model_validate(value)
    raise TypeError("validation_evidence must be a ValidationEvidence, dict, list, or None.")


__all__ = [
    "ValidationEvidence",
    "normalize_validation_evidence",
]
