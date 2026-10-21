"""Canonical evidence observation payload for module-owned event envelopes."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

from .models import (
    Completeness,
    EvidenceRecord,
    EvidenceSource,
    EvidenceSubject,
    Identifier,
    Name,
    VerificationLevel,
    _utc_datetime,
)

EVIDENCE_RECORDED_EVENT_TYPE = "domain.evidence.recorded"


class EvidenceRecorded(BaseModel):
    """Event payload; standard correlation and authority live in the envelope."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: Identifier
    subject: EvidenceSubject
    source: EvidenceSource
    verification: VerificationLevel
    completeness: Completeness
    methodology_version: Name
    observed_at: datetime
    record_digest: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

    @field_validator("observed_at", mode="before")
    @classmethod
    def _timestamp(cls, value: object) -> datetime:
        return _utc_datetime(value)

    @classmethod
    def from_record(cls, record: EvidenceRecord) -> EvidenceRecorded:
        return cls(
            evidence_id=record.id,
            subject=record.subject,
            source=record.source,
            verification=record.verification,
            completeness=record.completeness,
            methodology_version=record.methodology_version,
            observed_at=record.observed_at,
            record_digest=record.content_digest(),
        )


def evidence_recorded_payload_schema() -> dict:
    """JSON Schema for ``contracts/events.yaml`` payload declarations."""

    return EvidenceRecorded.model_json_schema()
