"""Replay and snapshot event contracts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

REPLAY_PROTOCOL_VERSION = "1.0.0"
REPLAY_BOUNDARY_EVENT_TYPE = "replay.boundary.reached"
REPLAY_SNAPSHOT_EVENT_TYPE = "replay.snapshot.created"


class ReplayBoundaryPayload(BaseModel):
    """Payload for replay boundary marker events."""

    model_config = ConfigDict(extra="forbid")

    last_seq: int = Field(..., ge=0)
    requested_last_seq: int = Field(..., ge=0)
    replay_complete: bool = True


class SnapshotEventPayload(BaseModel):
    """Payload describing a state snapshot reference."""

    model_config = ConfigDict(extra="forbid")

    snapshot_id: str = Field(..., min_length=1)
    snapshot_seq: int = Field(..., ge=0)
    state_ref: str = Field(..., min_length=1)
    codec: str | None = None
    state_hash: str | None = None
    metadata: dict[str, Any] | None = None


__all__ = [
    "REPLAY_BOUNDARY_EVENT_TYPE",
    "REPLAY_PROTOCOL_VERSION",
    "REPLAY_SNAPSHOT_EVENT_TYPE",
    "ReplayBoundaryPayload",
    "SnapshotEventPayload",
]
