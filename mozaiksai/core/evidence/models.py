"""Typed, domain-neutral evidence observations.

Evidence records describe observations. Source systems retain authority over
their original facts, and consuming applications own interpretation and storage.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    StringConstraints,
    field_validator,
    model_validator,
)

Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
EvidenceScalar = StrictBool | StrictInt | StrictFloat | StrictStr


def _utc_datetime(value: object) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("timestamp must be ISO 8601") from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(UTC)


def _finite_scalar(value: EvidenceScalar) -> EvidenceScalar:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("numeric evidence values must be finite")
    if isinstance(value, str) and len(value) > 2048:
        raise ValueError("text evidence values must be 2048 characters or fewer")
    return value


class EvidenceType(StrEnum):
    BEHAVIORAL = "behavioral"
    ECONOMIC = "economic"
    OPERATIONAL = "operational"
    SYNTHETIC = "synthetic"
    HUMAN_FEEDBACK = "human_feedback"
    RELIABILITY = "reliability"
    EXPERIMENT = "experiment"
    SECURITY = "security"


class EvidenceSourceKind(StrEnum):
    RUNTIME = "runtime"
    APP_METRICS = "app_metrics"
    HUMAN_FEEDBACK = "human_feedback"
    SYNTHETIC = "synthetic"
    EXTERNAL_PROVIDER = "external_provider"
    APP_MODULE = "app_module"
    REPORTED = "reported"


class VerificationLevel(StrEnum):
    MEASURED = "measured"
    DERIVED = "derived"
    REPORTED = "reported"
    SYNTHETIC = "synthetic"
    PARTIAL = "partial"
    UNVERIFIED = "unverified"
    UNAVAILABLE = "unavailable"


class Completeness(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"


class EvidenceSubject(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Name
    id: Identifier
    tenant_id: Identifier | None = None
    workspace_id: Identifier | None = None


class EvidencePeriod(BaseModel):
    """Exactly one point observation or one bounded interval."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["point", "interval"]
    point_at: datetime | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None

    @field_validator("point_at", "start_at", "end_at", mode="before")
    @classmethod
    def _timestamp(cls, value: object) -> datetime | None:
        return None if value is None else _utc_datetime(value)

    @model_validator(mode="after")
    def _consistent_shape(self) -> EvidencePeriod:
        if self.kind == "point":
            if self.point_at is None or self.start_at is not None or self.end_at is not None:
                raise ValueError("point evidence requires only point_at")
        elif self.point_at is not None or self.start_at is None or self.end_at is None:
            raise ValueError("interval evidence requires start_at and end_at only")
        elif self.start_at > self.end_at:
            raise ValueError("evidence interval start_at must not exceed end_at")
        return self

    @classmethod
    def point(cls, at: datetime | str) -> EvidencePeriod:
        return cls(kind="point", point_at=_utc_datetime(at))

    @classmethod
    def interval(cls, start: datetime | str, end: datetime | str) -> EvidencePeriod:
        return cls(kind="interval", start_at=_utc_datetime(start), end_at=_utc_datetime(end))


class EvidenceSource(BaseModel):
    """Reference to exact source facts or an immutable source-record manifest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: EvidenceSourceKind
    system: Name
    source_record_ids: tuple[Identifier, ...] = Field(default=(), max_length=8192)
    source_record_set_ref: Identifier | None = None
    source_record_set_digest: (
        Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")] | None
    ) = None
    app_id: Identifier | None = None
    tenant_id: Identifier | None = None
    workspace_id: Identifier | None = None
    correlation_id: Identifier | None = None

    @model_validator(mode="after")
    def _valid_references(self) -> EvidenceSource:
        if len(set(self.source_record_ids)) != len(self.source_record_ids):
            raise ValueError("source_record_ids must be unique")
        if (self.source_record_set_ref is None) != (self.source_record_set_digest is None):
            raise ValueError("source record-set reference and digest must be supplied together")
        return self

    @field_validator("source_record_ids")
    @classmethod
    def _canonical_order(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(value))


class EvidenceValueField(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Name
    value: EvidenceScalar
    unit: Name | None = None

    @field_validator("value")
    @classmethod
    def _value(cls, value: EvidenceScalar) -> EvidenceScalar:
        return _finite_scalar(value)


class StructuredEvidenceValue(BaseModel):
    """A bounded set of named scalar values, not an arbitrary nested document."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fields: tuple[EvidenceValueField, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def _unique_fields(self) -> StructuredEvidenceValue:
        names = [field.name for field in self.fields]
        if len(set(names)) != len(names):
            raise ValueError("structured evidence field names must be unique")
        return self


class EvidenceRecord(BaseModel):
    """One validated observation with explicit provenance and data quality."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: Identifier
    subject: EvidenceSubject
    evidence_type: EvidenceType
    metric_type: Name
    value: EvidenceScalar | StructuredEvidenceValue | None
    unit: Name | None = None
    period: EvidencePeriod
    source: EvidenceSource
    verification: VerificationLevel
    completeness: Completeness
    methodology_version: Name
    observed_at: datetime
    collected_at: datetime
    metadata: dict[str, EvidenceScalar] = Field(default_factory=dict, max_length=32)

    @field_validator("observed_at", "collected_at", mode="before")
    @classmethod
    def _timestamp(cls, value: object) -> datetime:
        return _utc_datetime(value)

    @field_validator("value")
    @classmethod
    def _value(cls, value: EvidenceScalar | StructuredEvidenceValue | None):
        if value is None or isinstance(value, StructuredEvidenceValue):
            return value
        return _finite_scalar(value)

    @field_validator("metadata")
    @classmethod
    def _safe_metadata(cls, value: dict[str, EvidenceScalar]) -> dict[str, EvidenceScalar]:
        for key, item in value.items():
            if not key or len(key) > 128:
                raise ValueError("metadata keys must be 1 to 128 characters")
            _finite_scalar(item)
        return value

    @model_validator(mode="after")
    def _consistent_quality(self) -> EvidenceRecord:
        unavailable = self.verification == VerificationLevel.UNAVAILABLE
        if unavailable != (self.value is None):
            raise ValueError(
                "unavailable evidence must have no value; other evidence must have a value"
            )
        if unavailable != (self.completeness == Completeness.UNAVAILABLE):
            raise ValueError("unavailable verification and completeness must agree")
        synthetic = self.verification == VerificationLevel.SYNTHETIC
        if synthetic != (self.source.kind == EvidenceSourceKind.SYNTHETIC):
            raise ValueError("synthetic verification requires a synthetic source, and vice versa")
        if self.evidence_type == EvidenceType.SYNTHETIC and not synthetic:
            raise ValueError("synthetic evidence type requires synthetic verification")
        if self.verification == VerificationLevel.DERIVED and not (
            self.source.source_record_ids or self.source.source_record_set_ref
        ):
            raise ValueError("derived evidence must reference its source records")
        return self

    def canonical_bytes(self) -> bytes:
        """Stable JSON representation suitable for immutable snapshot digests."""

        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")

    def content_digest(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()
