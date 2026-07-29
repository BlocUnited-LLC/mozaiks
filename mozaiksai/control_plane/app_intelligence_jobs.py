"""Studio-visible App Intelligence indexing job state."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from logs.logging_config import get_workflow_logger
from mozaiksai.core.data.persistence.namespaces import SYSTEM_DATABASE, PlatformCollections
from mozaiksai.core.multitenant import build_app_scope_filter

from .source_import import SourceImportKind, public_source_import_result

logger = get_workflow_logger("app_intelligence_jobs")

APP_INTELLIGENCE_INDEX_JOB_SCHEMA_VERSION: Literal["mozaiks.app_context_index_job.v1"] = "mozaiks.app_context_index_job.v1"

IndexJobStatus = Literal["queued", "running", "succeeded", "failed"]
IndexJobPhaseStatus = Literal["pending", "running", "complete", "failed", "skipped"]

APP_INTELLIGENCE_INDEX_PHASES: tuple[tuple[str, str], ...] = (
    ("repo_clone", "Clone repo"),
    ("workspace_scan", "Scan files"),
    ("source_index", "Source index"),
    ("symbol_parse", "Parse symbols"),
    ("context_graph", "Build graph"),
    ("app_intelligence", "App intelligence"),
    ("ready", "Ready"),
)

_JOBS_BY_ID: dict[str, AppIntelligenceIndexJob] = {}
_INDEXES_READY = False


def _utc_now() -> datetime:
    return datetime.now(UTC)


class AppIntelligenceIndexJobPhase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    status: IndexJobPhaseStatus = "pending"
    message: str = ""
    started_at: datetime | None = None
    completed_at: datetime | None = None


class AppIntelligenceIndexJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["mozaiks.app_context_index_job.v1"] = APP_INTELLIGENCE_INDEX_JOB_SCHEMA_VERSION
    job_id: str
    app_id: str
    requested_by: str | None = None
    source_kind: SourceImportKind = "local_workspace"
    workspace_root: str | None = None
    repo_url: str | None = None
    branch: str | None = None
    monorepo_path: str | None = None
    auth_connector_id: str | None = None
    ignored_paths: list[str] = Field(default_factory=list)
    artifact_key: str = "app_intelligence_workspace"
    make_current: bool = True
    scan_policy: dict[str, Any] | None = None
    status: IndexJobStatus = "queued"
    current_phase: str = "queued"
    progress_percent: int = 0
    phases: list[AppIntelligenceIndexJobPhase] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    app_intelligence: dict[str, Any] | None = None
    context_readiness: dict[str, Any] | None = None
    import_result: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None


def create_app_intelligence_index_job(
    *,
    app_id: str,
    requested_by: str | None,
    source_kind: SourceImportKind = "local_workspace",
    workspace_root: str | None = None,
    repo_url: str | None = None,
    branch: str | None = None,
    monorepo_path: str | None = None,
    auth_connector_id: str | None = None,
    ignored_paths: list[str] | None = None,
    artifact_key: str = "app_intelligence_workspace",
    make_current: bool = True,
    scan_policy: dict[str, Any] | None = None,
) -> AppIntelligenceIndexJob:
    resolved_app_id = str(app_id or "").strip()
    if not resolved_app_id:
        raise ValueError("app_id is required")
    phases = [
        AppIntelligenceIndexJobPhase(
            id=phase_id,
            label=label,
            status="skipped" if phase_id == "repo_clone" and source_kind == "local_workspace" else "pending",
            message="Local workspace indexing does not clone a repository."
            if phase_id == "repo_clone" and source_kind == "local_workspace"
            else "",
        )
        for phase_id, label in APP_INTELLIGENCE_INDEX_PHASES
    ]
    return AppIntelligenceIndexJob(
        job_id=f"aij_{uuid4().hex[:24]}",
        app_id=resolved_app_id,
        requested_by=str(requested_by or "").strip() or None,
        source_kind=source_kind,
        workspace_root=str(workspace_root or "").strip() or None,
        repo_url=str(repo_url or "").strip() or None,
        branch=str(branch or "").strip() or None,
        monorepo_path=str(monorepo_path or "").strip().strip("/\\") or None,
        auth_connector_id=str(auth_connector_id or "").strip() or None,
        ignored_paths=[str(item or "").strip() for item in ignored_paths or [] if str(item or "").strip()],
        artifact_key=str(artifact_key or "app_intelligence_workspace").strip() or "app_intelligence_workspace",
        make_current=bool(make_current),
        scan_policy=dict(scan_policy or {}) if scan_policy else None,
        phases=phases,
    )


def advance_app_intelligence_index_job(
    job: AppIntelligenceIndexJob,
    *,
    phase_id: str,
    message: str = "",
    details: dict[str, Any] | None = None,
) -> AppIntelligenceIndexJob:
    now = _utc_now()
    resolved_phase_id = str(phase_id or "").strip()
    seen_current = False
    phases: list[AppIntelligenceIndexJobPhase] = []
    phase_ids = [phase.id for phase in job.phases]
    current_index = phase_ids.index(resolved_phase_id) if resolved_phase_id in phase_ids else -1

    for index, phase in enumerate(job.phases):
        update: dict[str, Any] = {}
        if phase.status == "running" and phase.id != resolved_phase_id:
            update["status"] = "complete"
            update["completed_at"] = now
        if index < current_index and phase.status == "pending":
            update["status"] = "complete"
            update["completed_at"] = now
        if phase.id == resolved_phase_id:
            seen_current = True
            update["status"] = "running"
            update["started_at"] = phase.started_at or now
            update["message"] = message or _default_phase_message(phase.id)
        phases.append(phase.model_copy(update=update))

    progress_percent = _progress_percent(phases)
    return job.model_copy(
        update={
            "status": "running",
            "current_phase": resolved_phase_id if seen_current else job.current_phase,
            "progress_percent": progress_percent,
            "phases": phases,
            "started_at": job.started_at or now,
            "updated_at": now,
            "warnings": _dedupe([*job.warnings, *list((details or {}).get("warnings") or [])]),
        }
    )


def complete_app_intelligence_index_job(
    job: AppIntelligenceIndexJob,
    *,
    app_intelligence: dict[str, Any],
    context_readiness: dict[str, Any],
    warnings: list[str] | None = None,
) -> AppIntelligenceIndexJob:
    now = _utc_now()
    phases = [
        phase.model_copy(
            update={
                "status": "complete" if phase.status != "skipped" else "skipped",
                "completed_at": phase.completed_at or now if phase.status != "skipped" else phase.completed_at,
                "message": phase.message or _default_phase_message(phase.id),
            }
        )
        for phase in job.phases
    ]
    return job.model_copy(
        update={
            "status": "succeeded",
            "current_phase": "ready",
            "progress_percent": 100,
            "phases": phases,
            "completed_at": now,
            "updated_at": now,
            "app_intelligence": dict(app_intelligence or {}),
            "context_readiness": dict(context_readiness or {}),
            "warnings": _dedupe([*job.warnings, *list(warnings or [])]),
            "error": None,
        }
    )


def fail_app_intelligence_index_job(
    job: AppIntelligenceIndexJob,
    *,
    error: str,
    phase_id: str | None = None,
    warnings: list[str] | None = None,
) -> AppIntelligenceIndexJob:
    now = _utc_now()
    failed_phase = str(phase_id or job.current_phase or "workspace_scan").strip()
    phases: list[AppIntelligenceIndexJobPhase] = []
    for phase in job.phases:
        update: dict[str, Any] = {}
        if phase.id == failed_phase:
            update["status"] = "failed"
            update["completed_at"] = now
            update["message"] = error
        elif phase.status == "running":
            update["status"] = "failed"
            update["completed_at"] = now
            update["message"] = error
        phases.append(phase.model_copy(update=update))
    return job.model_copy(
        update={
            "status": "failed",
            "current_phase": failed_phase,
            "phases": phases,
            "completed_at": now,
            "updated_at": now,
            "warnings": _dedupe([*job.warnings, *list(warnings or [])]),
            "error": error,
        }
    )


def public_app_intelligence_index_job(job: AppIntelligenceIndexJob | None) -> dict[str, Any] | None:
    if job is None:
        return None
    payload = job.model_dump(mode="json")
    payload.pop("workspace_root", None)
    payload.pop("scan_policy", None)
    payload.pop("auth_connector_id", None)
    payload["import_result"] = public_source_import_result(job.import_result)
    payload["source"] = {
        "kind": job.source_kind,
        "workspace_root_present": bool(job.workspace_root),
        "repo_url": job.repo_url,
        "branch": job.branch,
        "monorepo_path": job.monorepo_path,
        "auth_connector_present": bool(job.auth_connector_id),
        "ignored_paths": list(job.ignored_paths),
    }
    return payload


async def save_app_intelligence_index_job(job: AppIntelligenceIndexJob) -> AppIntelligenceIndexJob:
    _JOBS_BY_ID[job.job_id] = job
    try:
        coll = await _jobs_collection()
        payload = job.model_dump(mode="python")
        payload["_id"] = job.job_id
        await coll.update_one(
            {"_id": job.job_id, **build_app_scope_filter(job.app_id)},
            {"$set": payload},
            upsert=True,
        )
    except Exception as exc:
        logger.warning("APP_INTELLIGENCE_INDEX_JOB_STORE_DEGRADED job_id=%s error=%s", job.job_id, exc)
    return job


async def get_app_intelligence_index_job(
    *,
    app_id: str,
    job_id: str,
) -> AppIntelligenceIndexJob | None:
    resolved_job_id = str(job_id or "").strip()
    memory_job = _JOBS_BY_ID.get(resolved_job_id)
    if memory_job is not None and memory_job.app_id == app_id:
        return memory_job
    try:
        coll = await _jobs_collection()
        raw = await coll.find_one({"_id": resolved_job_id, **build_app_scope_filter(app_id)})
        if isinstance(raw, dict):
            raw.pop("_id", None)
            job = AppIntelligenceIndexJob.model_validate(raw)
            _JOBS_BY_ID[job.job_id] = job
            return job
    except Exception as exc:
        logger.warning("APP_INTELLIGENCE_INDEX_JOB_LOAD_DEGRADED job_id=%s error=%s", resolved_job_id, exc)
    return None


async def get_latest_app_intelligence_index_job(*, app_id: str) -> AppIntelligenceIndexJob | None:
    memory_jobs = [job for job in _JOBS_BY_ID.values() if job.app_id == app_id]
    memory_jobs.sort(key=lambda job: _datetime_sort_key(job.created_at), reverse=True)
    try:
        coll = await _jobs_collection()
        rows = await coll.find(build_app_scope_filter(app_id)).sort("created_at", -1).limit(1).to_list(length=1)
        if rows:
            raw = rows[0]
            raw.pop("_id", None)
            job = AppIntelligenceIndexJob.model_validate(raw)
            _JOBS_BY_ID[job.job_id] = job
            return job
    except Exception as exc:
        logger.warning("APP_INTELLIGENCE_INDEX_JOB_LATEST_DEGRADED app_id=%s error=%s", app_id, exc)
    return memory_jobs[0] if memory_jobs else None


async def _jobs_collection():
    global _INDEXES_READY
    from mozaiksai.core.core_config import get_mongo_client

    client = get_mongo_client()
    coll = client[SYSTEM_DATABASE][PlatformCollections.APP_CONTEXT_INDEX_JOBS]
    if not _INDEXES_READY:
        await coll.create_index([("app_id", 1), ("created_at", -1)], name="app_context_index_jobs_app_created")
        await coll.create_index([("app_id", 1), ("status", 1), ("updated_at", -1)], name="app_context_index_jobs_status")
        _INDEXES_READY = True
    return coll


def _progress_percent(phases: list[AppIntelligenceIndexJobPhase]) -> int:
    weighted = 0.0
    total = max(1, len(phases))
    for phase in phases:
        if phase.status in {"complete", "skipped"}:
            weighted += 1.0
        elif phase.status == "running":
            weighted += 0.5
    return max(0, min(99, int((weighted / total) * 100)))


def _default_phase_message(phase_id: str) -> str:
    return {
        "repo_clone": "Preparing repository source.",
        "workspace_scan": "Scanning selected source files.",
        "source_index": "Building source context.",
        "symbol_parse": "Parsing symbols and imports.",
        "context_graph": "Building the relationship graph.",
        "app_intelligence": "Synthesizing app intelligence.",
        "ready": "App context is ready.",
    }.get(phase_id, "Indexing app context.")


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _datetime_sort_key(value: datetime | None) -> float:
    if value is None:
        return 0.0
    candidate = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return candidate.timestamp()


__all__ = [
    "APP_INTELLIGENCE_INDEX_JOB_SCHEMA_VERSION",
    "APP_INTELLIGENCE_INDEX_PHASES",
    "AppIntelligenceIndexJob",
    "AppIntelligenceIndexJobPhase",
    "advance_app_intelligence_index_job",
    "complete_app_intelligence_index_job",
    "create_app_intelligence_index_job",
    "fail_app_intelligence_index_job",
    "get_app_intelligence_index_job",
    "get_latest_app_intelligence_index_job",
    "public_app_intelligence_index_job",
    "save_app_intelligence_index_job",
]
