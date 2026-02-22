"""Canonical event taxonomy for the runtime protocol."""

from __future__ import annotations

from mozaiks.contracts.artifacts import (
    ARTIFACT_CREATED_EVENT_TYPE,
    ARTIFACT_STATE_PATCHED_EVENT_TYPE,
    ARTIFACT_STATE_REPLACED_EVENT_TYPE,
    ARTIFACT_UPDATED_EVENT_TYPE,
)
from mozaiks.contracts.replay import (
    REPLAY_BOUNDARY_EVENT_TYPE,
    REPLAY_SNAPSHOT_EVENT_TYPE,
)

PROCESS_STARTED_EVENT_TYPE = "process.started"
PROCESS_COMPLETED_EVENT_TYPE = "process.completed"
PROCESS_FAILED_EVENT_TYPE = "process.failed"

CANONICAL_EVENT_TAXONOMY: dict[str, tuple[str, ...]] = {
    "process": (
        PROCESS_STARTED_EVENT_TYPE,
        PROCESS_COMPLETED_EVENT_TYPE,
        PROCESS_FAILED_EVENT_TYPE,
    ),
    "artifact": (
        ARTIFACT_CREATED_EVENT_TYPE,
        ARTIFACT_UPDATED_EVENT_TYPE,
        ARTIFACT_STATE_REPLACED_EVENT_TYPE,
        ARTIFACT_STATE_PATCHED_EVENT_TYPE,
    ),
    "replay": (
        REPLAY_BOUNDARY_EVENT_TYPE,
        REPLAY_SNAPSHOT_EVENT_TYPE,
    ),
}

__all__ = [
    "CANONICAL_EVENT_TAXONOMY",
    "PROCESS_COMPLETED_EVENT_TYPE",
    "PROCESS_FAILED_EVENT_TYPE",
    "PROCESS_STARTED_EVENT_TYPE",
]
