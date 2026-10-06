from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from mozaiksai.core.evidence import (
    EVIDENCE_RECORDED_EVENT_TYPE,
    Completeness,
    EvidenceCollectionRequest,
    EvidencePeriod,
    EvidenceRecord,
    EvidenceRecorded,
    EvidenceSource,
    EvidenceSourceKind,
    EvidenceSubject,
    EvidenceType,
    EvidenceValueField,
    StructuredEvidenceValue,
    VerificationLevel,
    collect_evidence,
    evidence_recorded_payload_schema,
    project_app_metric_snapshot,
)
from mozaiksai.core.runtime.app.module_loader import ModuleEventsManifest
from mozaiksai.core.runtime.composition.schema_validation import validate_json_schema


def _record(**overrides) -> EvidenceRecord:
    values = {
        "id": "ev-1",
        "subject": EvidenceSubject(kind="app", id="app-1", tenant_id="tenant-1"),
        "evidence_type": EvidenceType.BEHAVIORAL,
        "metric_type": "active_users",
        "value": 17,
        "unit": "users",
        "period": EvidencePeriod.interval("2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z"),
        "source": EvidenceSource(
            kind=EvidenceSourceKind.APP_METRICS,
            system="app_metrics",
            source_record_ids=("metric-2", "metric-1"),
            app_id="runtime-app-1",
            tenant_id="tenant-1",
        ),
        "verification": VerificationLevel.MEASURED,
        "completeness": Completeness.PARTIAL,
        "methodology_version": "active_users.v1",
        "observed_at": "2026-10-02T00:00:00Z",
        "collected_at": "2026-10-02T01:00:00Z",
        "metadata": {"aggregation": "period_unique"},
    }
    values.update(overrides)
    return EvidenceRecord(**values)


def _metric_snapshot(**overrides) -> dict:
    values = {
        "metric_event_id": "metric-1",
        "event_name": "kpi.active_users",
        "subject_type": "app",
        "subject_id": "runtime-app-1",
        "value": 17.0,
        "unit": "users",
        "app_id": "runtime-app-1",
        "tenant_id": "tenant-1",
        "workspace_id": "workspace-1",
        "correlation_id": "corr-1",
        "occurred_at": "2026-10-02T00:00:00Z",
        "created_at": "2026-10-02T01:00:00Z",
        "period_start": "2026-10-01T00:00:00Z",
        "period_end": "2026-10-02T00:00:00Z",
        "aggregation": "period_unique",
        "visibility": "admin",
        "metadata": {"metric_kind": "snapshot"},
    }
    values.update(overrides)
    return values


def test_point_and_interval_require_aware_valid_timestamps() -> None:
    point = EvidencePeriod.point("2026-10-01T05:00:00+05:00")
    assert point.point_at == datetime(2026, 10, 1, tzinfo=UTC)
    assert (
        EvidencePeriod.interval("2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z").kind == "interval"
    )
    with pytest.raises(ValueError, match="timezone"):
        EvidencePeriod.point("2026-10-01T00:00:00")
    with pytest.raises(ValidationError, match="start_at must not exceed"):
        EvidencePeriod.interval("2026-10-02T00:00:00Z", "2026-10-01T00:00:00Z")
    with pytest.raises(ValidationError, match="point evidence requires"):
        EvidencePeriod(kind="point", start_at="2026-10-01T00:00:00Z")


def test_evidence_schema_rejects_missing_source_invalid_quality_and_nonfinite_values() -> None:
    values = _record().model_dump()
    values.pop("source")
    with pytest.raises(ValidationError, match="source"):
        EvidenceRecord.model_validate(values)
    with pytest.raises(ValidationError, match="verification"):
        _record(verification="confirmed")
    with pytest.raises(ValidationError, match="finite"):
        _record(value=float("nan"))
    with pytest.raises(ValidationError, match="timezone"):
        _record(observed_at="2026-10-02T00:00:00")
    with pytest.raises(ValidationError, match="unavailable"):
        _record(value=None)
    assert (
        _record(
            value=None,
            verification=VerificationLevel.UNAVAILABLE,
            completeness=Completeness.UNAVAILABLE,
        ).value
        is None
    )


def test_structured_value_has_typed_named_fields_without_nested_dump() -> None:
    value = StructuredEvidenceValue(
        fields=(
            EvidenceValueField(name="attempts", value=20, unit="count"),
            EvidenceValueField(name="successes", value=17, unit="count"),
        )
    )
    record = _record(value=value, metric_type="agent_success_counts", unit=None)
    assert record.value == value
    with pytest.raises(ValidationError):
        EvidenceValueField(name="attempts", value={"raw": [1, 2]})
    with pytest.raises(ValidationError, match="unique"):
        StructuredEvidenceValue(fields=(value.fields[0], value.fields[0]))


def test_source_references_are_exact_bounded_and_canonically_ordered() -> None:
    source = _record().source
    assert source.source_record_ids == ("metric-1", "metric-2")
    many = EvidenceSource(
        kind="app_module",
        system="receipts",
        source_record_ids=tuple(f"receipt-{index:04d}" for index in range(5000)),
    )
    assert len(many.source_record_ids) == 5000
    with pytest.raises(ValidationError, match="unique"):
        EvidenceSource(kind="app_module", system="receipts", source_record_ids=("same", "same"))
    with pytest.raises(ValidationError, match="together"):
        EvidenceSource(kind="app_module", system="receipts", source_record_set_ref="manifest-1")
    with pytest.raises(ValidationError, match="source records"):
        _record(
            verification=VerificationLevel.DERIVED,
            source=EvidenceSource(kind="app_module", system="formula"),
        )
    assert (
        _record(
            verification=VerificationLevel.DERIVED,
            source=EvidenceSource(
                kind="app_module",
                system="formula",
                source_record_set_ref="manifest-1",
                source_record_set_digest="a" * 64,
            ),
        ).source.source_record_set_ref
        == "manifest-1"
    )


def test_synthetic_evidence_cannot_look_observed() -> None:
    synthetic = EvidenceSource(
        kind="synthetic", system="persona-simulator", source_record_ids=("run-1",)
    )
    with pytest.raises(ValidationError, match="synthetic verification"):
        _record(source=synthetic)
    result = _record(
        source=synthetic,
        verification=VerificationLevel.SYNTHETIC,
        evidence_type=EvidenceType.SYNTHETIC,
    )
    assert result.verification == VerificationLevel.SYNTHETIC
    with pytest.raises(ValidationError, match="synthetic evidence type"):
        _record(evidence_type=EvidenceType.SYNTHETIC)


def test_canonical_bytes_and_digest_are_stable_across_key_and_timezone_order() -> None:
    first = _record(metadata={"b": 2, "a": 1})
    second = _record(
        metadata={"a": 1, "b": 2},
        observed_at="2026-10-01T20:00:00-04:00",
        source=EvidenceSource(
            kind="app_metrics",
            system="app_metrics",
            source_record_ids=("metric-1", "metric-2"),
            app_id="runtime-app-1",
            tenant_id="tenant-1",
        ),
    )
    assert first.canonical_bytes() == second.canonical_bytes()
    assert first.content_digest() == second.content_digest()
    assert first.content_digest() != first.model_copy(update={"value": 18}).content_digest()


@pytest.mark.asyncio
async def test_provider_collection_validates_scope_duplicates_and_canonical_order() -> None:
    request = EvidenceCollectionRequest(
        subject=_record().subject, period=_record().period, metric_types=("active_users",)
    )

    class Provider:
        async def collect(self, request):
            return [_record(id="ev-2"), _record(id="ev-1")]

    assert tuple(record.id for record in await collect_evidence(Provider(), request)) == (
        "ev-1",
        "ev-2",
    )

    class WrongScope:
        async def collect(self, request):
            return [_record(subject=EvidenceSubject(kind="app", id="app-other"))]

    class Duplicate:
        async def collect(self, request):
            return [_record(), _record()]

    class RawDict:
        async def collect(self, request):
            return [_record().model_dump()]

    for provider, reason in (
        (WrongScope(), "subject or scope"),
        (Duplicate(), "duplicate"),
        (RawDict(), "EvidenceRecord"),
    ):
        with pytest.raises(ValueError, match=reason):
            await collect_evidence(provider, request)


def test_app_metrics_snapshot_projection_retains_original_source_and_scope() -> None:
    projected = project_app_metric_snapshot(
        _metric_snapshot(),
        evidence_type=EvidenceType.BEHAVIORAL,
        verification=VerificationLevel.MEASURED,
        completeness=Completeness.PARTIAL,
        methodology_version="active_users.v1",
    )
    assert projected.subject == EvidenceSubject(
        kind="app", id="runtime-app-1", tenant_id="tenant-1", workspace_id="workspace-1"
    )
    assert projected.source.source_record_ids == ("metric-1",)
    assert projected.source.app_id == "runtime-app-1"
    assert projected.source.correlation_id == "corr-1"
    assert projected.period.kind == "interval"
    assert projected.metric_type == "kpi.active_users"
    assert projected.metadata == {"aggregation": "period_unique", "visibility": "admin"}
    assert (
        projected.id
        == project_app_metric_snapshot(
            _metric_snapshot(),
            evidence_type=EvidenceType.BEHAVIORAL,
            verification=VerificationLevel.MEASURED,
            completeness=Completeness.PARTIAL,
            methodology_version="active_users.v1",
        ).id
    )


def test_app_metrics_projection_rejects_unqualified_or_ambiguous_rows() -> None:
    kwargs = {
        "evidence_type": EvidenceType.BEHAVIORAL,
        "verification": VerificationLevel.MEASURED,
        "completeness": Completeness.UNKNOWN,
        "methodology_version": "v1",
    }
    for row, reason in (
        (_metric_snapshot(metadata={}), "numeric snapshot"),
        (_metric_snapshot(subject_id=""), "subject_id"),
        (_metric_snapshot(value=float("inf")), "finite"),
        (_metric_snapshot(period_end=None), "both period_start"),
        (_metric_snapshot(app_id=""), "app_id"),
    ):
        with pytest.raises(ValueError, match=reason):
            project_app_metric_snapshot(row, **kwargs)


def test_evidence_recorded_contract_validates_payload_with_existing_event_schema() -> None:
    record = _record()
    payload = EvidenceRecorded.from_record(record).model_dump(mode="json")
    schema = evidence_recorded_payload_schema()
    assert EVIDENCE_RECORDED_EVENT_TYPE == "domain.evidence.recorded"
    assert validate_json_schema(payload, schema) is None
    assert validate_json_schema({**payload, "evidence_id": 2}, schema) is not None
    assert validate_json_schema({**payload, "record_digest": "not-a-digest"}, schema) is not None
    assert validate_json_schema({**payload, "entitlement_amount": 42}, schema) is not None
    manifest = ModuleEventsManifest.model_validate(
        {
            "schema_version": "mozaiks.events.v1",
            "events": [
                {
                    "type": EVIDENCE_RECORDED_EVENT_TYPE,
                    "version": 1,
                    "producer": "evidence",
                    "payload_schema": schema,
                }
            ],
        }
    )
    assert manifest.events[0].type == EVIDENCE_RECORDED_EVENT_TYPE
