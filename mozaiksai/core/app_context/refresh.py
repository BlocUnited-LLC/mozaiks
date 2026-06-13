"""Typed contracts for app-context refresh planning."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import ArtifactRef, SourceRef

BROWNFIELD_DISCOVERY_REFRESH_SEQUENCE = "brownfield_discovery_refresh"

CONTEXT_REFRESH_EXPECTED_ARTIFACTS = (
    "application_inventory",
    "ownership_boundary",
    "integration_inventory",
    "risk_report",
    "adoption_plan",
    "brownfield_registration",
    "app_context_graph",
    "app_context_version",
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ContextRefreshScope(StrEnum):
    DISCOVERY_INDEXING = "discovery_indexing"
    SOURCE_REF_RESCAN = "source_ref_rescan"
    OWNERSHIP_RECHECK = "ownership_recheck"
    INTEGRATION_RECHECK = "integration_recheck"


class ContextRefreshResultStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    NO_CHANGE = "no_change"
    MISSING_CONTEXT = "missing_context"


class ContextRefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_id: str = Field(min_length=1)
    current_context_version_id: str | None = None
    reason: str = Field(min_length=1)
    source_refs: list[SourceRef] = Field(default_factory=list)
    requested_by: str | None = None
    refresh_scope: ContextRefreshScope = ContextRefreshScope.DISCOVERY_INDEXING
    created_at: datetime = Field(default_factory=_utc_now)


class ContextRefreshPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_id: str = Field(min_length=1)
    current_context_version_id: str | None = None
    target_source_refs: list[SourceRef] = Field(default_factory=list)
    workflow_sequence: str = BROWNFIELD_DISCOVERY_REFRESH_SEQUENCE
    required_inputs: list[str] = Field(default_factory=list)
    expected_artifacts: list[str] = Field(default_factory=lambda: list(CONTEXT_REFRESH_EXPECTED_ARTIFACTS))
    mutation_allowed: Literal[False] = False
    warnings: list[str] = Field(default_factory=list)


class ContextRefreshResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_id: str = Field(min_length=1)
    previous_context_version_id: str | None = None
    new_context_version_id: str | None = None
    status: ContextRefreshResultStatus = ContextRefreshResultStatus.COMPLETED
    stale_resolved: bool
    warnings: list[str] = Field(default_factory=list)
    artifacts_created: list[ArtifactRef] = Field(default_factory=list)


__all__ = [
    "BROWNFIELD_DISCOVERY_REFRESH_SEQUENCE",
    "CONTEXT_REFRESH_EXPECTED_ARTIFACTS",
    "ContextRefreshPlan",
    "ContextRefreshRequest",
    "ContextRefreshResult",
    "ContextRefreshResultStatus",
    "ContextRefreshScope",
]
