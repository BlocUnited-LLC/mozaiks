"""Artifact event contracts and taxonomy constants."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

ARTIFACT_EVENT_SCHEMA_VERSION = "1.0.0"
ARTIFACT_CREATED_EVENT_TYPE = "artifact.created"
ARTIFACT_UPDATED_EVENT_TYPE = "artifact.updated"
ARTIFACT_STATE_REPLACED_EVENT_TYPE = "artifact.state.replaced"
ARTIFACT_STATE_PATCHED_EVENT_TYPE = "artifact.state.patched"
LARGE_ARTIFACT_INLINE_THRESHOLD_BYTES = 262144


class ArtifactRef(BaseModel):
    """Reference to persisted artifact content."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(..., min_length=1)
    artifact_uri: str | None = None
    media_type: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    checksum: str | None = None

    @model_validator(mode="after")
    def validate_large_artifact_reference(self) -> "ArtifactRef":
        if (
            self.size_bytes is not None
            and self.size_bytes > LARGE_ARTIFACT_INLINE_THRESHOLD_BYTES
            and not self.artifact_uri
        ):
            raise ValueError(
                "Large artifacts must be referenced by artifact_uri; inline payload content is not allowed."
            )
        return self


class ArtifactCreatedPayload(BaseModel):
    """Payload for artifact.created."""

    model_config = ConfigDict(extra="forbid")

    artifact: ArtifactRef
    metadata: dict[str, Any] | None = None


class ArtifactUpdatedPayload(BaseModel):
    """Payload for artifact.updated."""

    model_config = ConfigDict(extra="forbid")

    artifact: ArtifactRef
    previous_artifact_uri: str | None = None
    metadata: dict[str, Any] | None = None


class ArtifactStateReplacedPayload(BaseModel):
    """Payload for artifact.state.replaced."""

    model_config = ConfigDict(extra="forbid")

    artifact: ArtifactRef
    state_ref: str = Field(..., min_length=1)
    previous_state_ref: str | None = None
    metadata: dict[str, Any] | None = None


class ArtifactStatePatchedPayload(BaseModel):
    """Payload for artifact.state.patched."""

    model_config = ConfigDict(extra="forbid")

    artifact: ArtifactRef
    base_state_ref: str = Field(..., min_length=1)
    patch_ref: str = Field(..., min_length=1)
    metadata: dict[str, Any] | None = None


ARTIFACT_EVENT_TYPES: tuple[str, ...] = (
    ARTIFACT_CREATED_EVENT_TYPE,
    ARTIFACT_UPDATED_EVENT_TYPE,
    ARTIFACT_STATE_REPLACED_EVENT_TYPE,
    ARTIFACT_STATE_PATCHED_EVENT_TYPE,
)

__all__ = [
    "ARTIFACT_CREATED_EVENT_TYPE",
    "ARTIFACT_EVENT_SCHEMA_VERSION",
    "ARTIFACT_EVENT_TYPES",
    "ARTIFACT_STATE_PATCHED_EVENT_TYPE",
    "ARTIFACT_STATE_REPLACED_EVENT_TYPE",
    "ARTIFACT_UPDATED_EVENT_TYPE",
    "ArtifactCreatedPayload",
    "ArtifactRef",
    "ArtifactStatePatchedPayload",
    "ArtifactStateReplacedPayload",
    "ArtifactUpdatedPayload",
    "LARGE_ARTIFACT_INLINE_THRESHOLD_BYTES",
]
