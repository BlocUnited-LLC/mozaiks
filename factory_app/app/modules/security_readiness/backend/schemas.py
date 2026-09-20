from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, TypedDict
from uuid import NAMESPACE_URL, uuid4, uuid5

SEVERITIES = ("low", "medium", "high", "critical")
STATUSES = ("open", "accepted", "resolved")
SOURCES = ("manual", "validation", "eval", "import")
CONTROL_AREAS = (
    "auth",
    "secrets",
    "permissions",
    "tenant_isolation",
    "managed_capability_boundary",
    "deployment",
    "data_persistence",
    "evals",
    "compliance_readiness",
)

_SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}


class SecurityFinding(TypedDict, total=False):
    finding_id: str
    app_id: str
    build_id: str | None
    build_registry_id: str | None
    artifact_version_id: str | None
    owner_user_id: str
    title: str
    description: str | None
    severity: str
    status: str
    control_area: str
    evidence_ref: str | None
    remediation: str | None
    source: str
    assessed_at: str
    created_at: str
    updated_at: str
    status_note: str | None
    status_updated_by: str | None
    status_updated_at: str | None


def timestamp_now() -> str:
    return datetime.now(UTC).isoformat()


def coerce_limit(value: Any, default: int = 100, maximum: int = 250) -> int:
    try:
        return max(1, min(int(value), maximum))
    except Exception:
        return default


def normalize_enum(value: Any, allowed: tuple[str, ...], *, default: str, field_name: str) -> str:
    normalized = str(value or default).strip().lower()
    if normalized not in allowed:
        raise ValueError(f"{field_name} must be one of: {', '.join(allowed)}")
    return normalized


def normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def build_finding_document(
    *,
    app_id: str,
    owner_user_id: str,
    finding: dict[str, Any],
    source: str,
    assessed_at: str,
    now: str,
    build_id: str | None = None,
    build_registry_id: str | None = None,
    artifact_version_id: str | None = None,
) -> SecurityFinding:
    title = normalize_optional_text(finding.get("title"))
    if not app_id:
        raise ValueError("app_id is required")
    if not title:
        raise ValueError("finding.title is required")
    source_key = normalize_optional_text(finding.get("finding_id"))
    # Scanner rule ids repeat between apps. Persist an opaque id scoped to the
    # owner, project, and assessed artifact so replay cannot overwrite another app.
    identity = json.dumps(
        [owner_user_id, app_id, build_registry_id, build_id, artifact_version_id, source, source_key],
        separators=(",", ":"),
    )
    finding_id = f"sr_{uuid5(NAMESPACE_URL, identity).hex}" if source_key else f"sr_{uuid4().hex}"
    return {
        "finding_id": finding_id,
        "app_id": app_id,
        "build_id": normalize_optional_text(build_id),
        "build_registry_id": normalize_optional_text(build_registry_id),
        "artifact_version_id": normalize_optional_text(artifact_version_id),
        "owner_user_id": owner_user_id,
        "title": title,
        "description": normalize_optional_text(finding.get("description")),
        "severity": normalize_enum(finding.get("severity"), SEVERITIES, default="medium", field_name="severity"),
        "status": normalize_enum(finding.get("status"), STATUSES, default="open", field_name="status"),
        "control_area": normalize_enum(
            finding.get("control_area"),
            CONTROL_AREAS,
            default="compliance_readiness",
            field_name="control_area",
        ),
        "evidence_ref": normalize_optional_text(finding.get("evidence_ref")),
        "remediation": normalize_optional_text(finding.get("remediation")),
        "source": normalize_enum(source, SOURCES, default="manual", field_name="source"),
        "assessed_at": assessed_at,
        "created_at": now,
        "updated_at": now,
    }


def public_finding(doc: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in doc.items() if key != "_id"}


def summarize_findings(findings: list[dict[str, Any]]) -> dict[str, Any]:
    by_status = {status: 0 for status in STATUSES}
    by_severity = {severity: 0 for severity in SEVERITIES}
    by_control_area = {area: 0 for area in CONTROL_AREAS}
    blocking = 0
    max_rank = 0
    max_severity: str | None = None
    for item in findings:
        status = str(item.get("status") or "open")
        severity = str(item.get("severity") or "medium")
        control_area = str(item.get("control_area") or "compliance_readiness")
        if status in by_status:
            by_status[status] += 1
        if severity in by_severity:
            by_severity[severity] += 1
        if control_area in by_control_area:
            by_control_area[control_area] += 1
        if status == "open" and severity in {"high", "critical"}:
            blocking += 1
        rank = _SEVERITY_RANK.get(severity, 0)
        if status == "open" and rank > max_rank:
            max_rank = rank
            max_severity = severity
    return {
        "total": len(findings),
        "open": by_status["open"],
        "accepted": by_status["accepted"],
        "resolved": by_status["resolved"],
        "blocking": blocking,
        "highest_open_severity": max_severity,
        "by_severity": by_severity,
        "by_control_area": by_control_area,
    }
