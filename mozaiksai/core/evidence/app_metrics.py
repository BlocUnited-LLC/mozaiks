"""Strict projection of an AppMetrics numeric snapshot into Evidence Core."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from typing import Any

from .models import (
    Completeness,
    EvidencePeriod,
    EvidenceRecord,
    EvidenceScalar,
    EvidenceSource,
    EvidenceSourceKind,
    EvidenceSubject,
    EvidenceType,
    VerificationLevel,
    _utc_datetime,
)


def _required_text(event: Mapping[str, Any], field: str) -> str:
    value = event.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"AppMetrics snapshot requires {field}")
    return value.strip()


def _optional_text(event: Mapping[str, Any], field: str) -> str | None:
    value = event.get(field)
    return value.strip() if isinstance(value, str) and value.strip() else None


def project_app_metric_snapshot(
    event: Mapping[str, Any],
    *,
    evidence_type: EvidenceType,
    verification: VerificationLevel,
    completeness: Completeness,
    methodology_version: str,
) -> EvidenceRecord:
    """Project one explicit numeric snapshot without remapping app identity.

    Raw AppMetrics events need their own denominator and aggregation method;
    only ``record_snapshot`` rows qualify here. The caller supplies quality and
    interpretation, including whether a KPI is measured or derived.
    """

    metadata = event.get("metadata")
    if not isinstance(metadata, Mapping) or metadata.get("metric_kind") != "snapshot":
        raise ValueError("AppMetrics evidence projection requires a numeric snapshot")
    raw_value = event.get("value")
    if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
        raise ValueError("AppMetrics snapshot value must be numeric")
    if not math.isfinite(raw_value):
        raise ValueError("AppMetrics snapshot value must be finite")

    source_id = _required_text(event, "metric_event_id")
    app_id = _required_text(event, "app_id")
    observed_at = _required_text(event, "occurred_at")
    collected_at = _required_text(event, "created_at")
    start = _optional_text(event, "period_start")
    end = _optional_text(event, "period_end")
    if (start is None) != (end is None):
        raise ValueError("AppMetrics period requires both period_start and period_end")
    period = (
        EvidencePeriod.interval(start, end)
        if start is not None and end is not None
        else EvidencePeriod.point(observed_at)
    )

    evidence_id = (
        "ev_" + hashlib.sha256(f"app_metrics:{app_id}:{source_id}".encode()).hexdigest()[:32]
    )
    projected_metadata: dict[str, EvidenceScalar] = {
        key: text
        for key in ("aggregation", "visibility")
        if (text := _optional_text(event, key)) is not None
    }
    return EvidenceRecord(
        id=evidence_id,
        subject=EvidenceSubject(
            kind=_required_text(event, "subject_type"),
            id=_required_text(event, "subject_id"),
            tenant_id=_optional_text(event, "tenant_id"),
            workspace_id=_optional_text(event, "workspace_id"),
        ),
        evidence_type=evidence_type,
        metric_type=_required_text(event, "event_name"),
        value=raw_value,
        unit=_required_text(event, "unit"),
        period=period,
        source=EvidenceSource(
            kind=EvidenceSourceKind.APP_METRICS,
            system="mozaiksai.core.metrics.app_metrics",
            source_record_ids=(source_id,),
            app_id=app_id,
            tenant_id=_optional_text(event, "tenant_id"),
            workspace_id=_optional_text(event, "workspace_id"),
            correlation_id=_optional_text(event, "correlation_id"),
        ),
        verification=verification,
        completeness=completeness,
        methodology_version=methodology_version,
        observed_at=_utc_datetime(observed_at),
        collected_at=_utc_datetime(collected_at),
        metadata=projected_metadata,
    )
