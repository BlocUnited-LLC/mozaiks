from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .dry_run import RefinementExecutionPlan
from .scoped_execution import (
    ScopedRefinementChange,
    ScopedRefinementResult,
    apply_scoped_refinement_changes,
)
from .staging import RefinementStagingResult


class StagedCodingWorkerChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    new_content: str
    reason: str = ""


class StagedCodingWorkerResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    changes: list[StagedCodingWorkerChange] = Field(default_factory=list)
    source: Literal["deterministic", "live_worker"] = "deterministic"
    warnings: list[str] = Field(default_factory=list)


_REASON_SECRET_TERMS = ("api_key", "apikey", "secret", "password", "token", "credential")
_REASON_FALLBACK = "Staged refinement output."


def _coerce_worker_result(worker_result: StagedCodingWorkerResult | dict[str, Any]) -> StagedCodingWorkerResult:
    if isinstance(worker_result, StagedCodingWorkerResult):
        return worker_result
    return StagedCodingWorkerResult.model_validate(worker_result)


def _reason_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    normalized = " ".join(text.split())
    if any(term in normalized.lower() for term in _REASON_SECRET_TERMS):
        return ""
    if len(normalized) <= 160:
        return normalized
    return normalized[:157].rstrip() + "..."


def select_staged_coding_worker_reason(
    *,
    plan: Any | None = None,
    fallback_reason: str = "live worker output for staged refinement smoke",
) -> str:
    """Select a concise staged worker reason from plan text or fallback text.

    Priority:
    1. plan.rationale
    2. plan.summary
    3. fallback_reason
    """

    summary = ""
    rationale = ""
    if plan is not None:
        if isinstance(plan, dict):
            summary = _reason_text(plan.get("summary"))
            rationale = _reason_text(plan.get("rationale"))
        else:
            summary = _reason_text(getattr(plan, "summary", ""))
            rationale = _reason_text(getattr(plan, "rationale", ""))

    for candidate in (rationale, summary, _reason_text(fallback_reason)):
        if candidate:
            return candidate
    return _REASON_FALLBACK


def build_scoped_changes_from_worker_result(
    worker_result: StagedCodingWorkerResult | dict[str, Any],
) -> list[ScopedRefinementChange]:
    """Convert staged worker output into scoped refinement changes.

    This is a pure conversion step. Path safety, scope validation, and
    writes remain owned by ``apply_scoped_refinement_changes``.
    """

    result = _coerce_worker_result(worker_result)
    if not str(result.request_id or "").strip():
        raise ValueError("staged coding worker result requires request_id")
    return [
        ScopedRefinementChange(path=change.path, new_content=change.new_content, reason=change.reason)
        for change in result.changes
    ]


def run_deterministic_staged_coding_worker(
    plan: RefinementExecutionPlan,
    staging_result: RefinementStagingResult,
    worker_result: StagedCodingWorkerResult | dict[str, Any],
) -> ScopedRefinementResult:
    """Apply deterministic staged worker changes through scoped execution only."""

    result = _coerce_worker_result(worker_result)
    if not str(result.request_id or "").strip():
        raise ValueError("staged coding worker result requires request_id")
    if result.source != "deterministic":
        raise ValueError("live staged coding worker execution is not enabled in this slice")
    if result.request_id != plan.request_id or result.request_id != staging_result.request_id:
        raise ValueError("staged coding worker request_id must match the refinement plan and staging result")

    scoped_changes = build_scoped_changes_from_worker_result(result)
    return apply_scoped_refinement_changes(plan=plan, staging_result=staging_result, changes=scoped_changes)  # type: ignore[arg-type]


def run_live_staged_coding_worker(
    plan: RefinementExecutionPlan,
    staging_result: RefinementStagingResult,
    worker_result: StagedCodingWorkerResult | dict[str, Any],
) -> ScopedRefinementResult:
    """Apply a live/manual staged worker result through scoped execution only."""

    result = _coerce_worker_result(worker_result)
    if not str(result.request_id or "").strip():
        raise ValueError("staged coding worker result requires request_id")
    if result.request_id != plan.request_id or result.request_id != staging_result.request_id:
        raise ValueError("staged coding worker request_id must match the refinement plan and staging result")
    if result.source not in {"live_worker", "deterministic"}:
        raise ValueError("staged coding worker source must be live_worker or deterministic")

    scoped_changes = build_scoped_changes_from_worker_result(result)
    return apply_scoped_refinement_changes(plan=plan, staging_result=staging_result, changes=scoped_changes)  # type: ignore[arg-type]


__all__ = [
    "StagedCodingWorkerChange",
    "StagedCodingWorkerResult",
    "build_scoped_changes_from_worker_result",
    "run_deterministic_staged_coding_worker",
    "run_live_staged_coding_worker",
    "select_staged_coding_worker_reason",
]
