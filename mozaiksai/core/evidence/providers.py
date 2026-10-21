"""Source-provider interface and generic collection validation."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import EvidencePeriod, EvidenceRecord, EvidenceSubject, Name


class EvidenceCollectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    subject: EvidenceSubject
    period: EvidencePeriod
    metric_types: tuple[Name, ...] = Field(default=(), max_length=128)

    @model_validator(mode="after")
    def _unique_metrics(self) -> EvidenceCollectionRequest:
        if len(set(self.metric_types)) != len(self.metric_types):
            raise ValueError("metric_types must be unique")
        return self


class EvidenceSourceProvider(Protocol):
    """A source emits canonical observations without owning their repository."""

    async def collect(self, request: EvidenceCollectionRequest) -> Sequence[EvidenceRecord]: ...


async def collect_evidence(
    provider: EvidenceSourceProvider,
    request: EvidenceCollectionRequest,
) -> tuple[EvidenceRecord, ...]:
    """Validate source output, subject scope, IDs, and deterministic order."""

    records = await provider.collect(request)
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes, bytearray)):
        raise ValueError("evidence provider must return a sequence of EvidenceRecord")
    seen: set[str] = set()
    normalized: list[EvidenceRecord] = []
    for record in records:
        if not isinstance(record, EvidenceRecord):
            raise ValueError("evidence provider must emit EvidenceRecord instances")
        if record.subject != request.subject:
            raise ValueError("evidence provider returned a different subject or scope")
        if request.metric_types and record.metric_type not in request.metric_types:
            raise ValueError("evidence provider returned an unrequested metric type")
        if record.id in seen:
            raise ValueError("evidence provider returned a duplicate evidence ID")
        seen.add(record.id)
        normalized.append(record)
    return tuple(sorted(normalized, key=lambda record: record.id))
