"""App-local D30 action cohort math over authoritative, scoped inputs.

This module does not obtain eligibility or action records. The consuming app
owns those sources and their coverage evidence. Automatic usage instrumentation
is fire and forget and cannot supply complete cohort evidence by itself.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal


@dataclass(frozen=True)
class CohortScope:
    app_id: str
    environment: str
    tenant_id: str | None = None
    workspace_id: str | None = None


@dataclass(frozen=True)
class EligibleEndUser:
    """One app-authoritative first eligibility fact for a stable end-user ID."""

    scope: CohortScope
    user_id: str
    first_eligible_at: datetime


@dataclass(frozen=True)
class ActionObservation:
    """One observed app action; only validated, successful end-user actions count."""

    scope: CohortScope
    user_id: str
    occurred_at: datetime
    action_id: str
    auth_provenance: str
    actor_kind: str
    succeeded: bool


@dataclass(frozen=True)
class SourceCoverage:
    """Source-owned attestation of continuous, reconciled coverage.

    ``authoritative`` means the source owns the relevant identity and delivery
    facts. A caller must derive this from its own trusted data source, never
    from a client request or from the automatic usage metrics stream.
    """

    scope: CohortScope
    starts_at: datetime
    complete_through: datetime
    authoritative: bool = False
    continuous: bool = False


@dataclass(frozen=True)
class D30CohortResult:
    scope: CohortScope
    cohort_date: date
    d30_start: datetime
    d30_end: datetime
    status: Literal["available", "unavailable"]
    reason: str | None = None
    denominator: int | None = None
    numerator: int | None = None
    rate_percent: float | None = None


def _utc(value: datetime) -> datetime | None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(UTC)


def _valid_scope(scope: CohortScope) -> bool:
    return (
        isinstance(scope, CohortScope)
        and isinstance(scope.app_id, str)
        and bool(scope.app_id.strip())
        and scope.app_id == scope.app_id.strip()
        and isinstance(scope.environment, str)
        and bool(scope.environment.strip())
        and scope.environment == scope.environment.strip()
        and scope.environment.lower() != "default"
        and all(
            value is None or (isinstance(value, str) and bool(value.strip()) and value == value.strip())
            for value in (scope.tenant_id, scope.workspace_id)
        )
    )


def _coverage_spans(
    coverage: SourceCoverage,
    scope: CohortScope,
    start: datetime,
    end: datetime,
    as_of: datetime,
) -> bool:
    if not isinstance(coverage, SourceCoverage):
        return False
    begins = _utc(coverage.starts_at)
    through = _utc(coverage.complete_through)
    return (
        coverage.scope == scope
        and _valid_scope(coverage.scope)
        and coverage.authoritative is True
        and coverage.continuous is True
        and begins is not None
        and through is not None
        and begins <= start
        and through >= end
        and through <= as_of
    )


def calculate_d30_action_cohort(
    *,
    scope: CohortScope,
    cohort_date: date,
    eligible_users: Iterable[EligibleEndUser],
    actions: Iterable[ActionObservation],
    eligible_coverage: SourceCoverage,
    action_coverage: SourceCoverage,
    qualifying_action_ids: Iterable[str],
    as_of: datetime,
) -> D30CohortResult:
    """Count end users who make a qualifying action on UTC day 30.

    The denominator is distinct stable IDs whose first eligibility falls on
    ``cohort_date``. The numerator is the subset with at least one successful,
    token-authenticated end-user action during UTC calendar day 30. Duplicate
    rows never increase either count. This function returns no counts until the
    whole day is mature and both sources attest complete, continuous coverage.
    It never returns a user ID.
    """

    if type(cohort_date) is not date:
        raise ValueError("cohort_date must be a UTC calendar date")
    cohort_start = datetime.combine(cohort_date, time.min, tzinfo=UTC)
    cohort_end = cohort_start + timedelta(days=1)
    d30_start = cohort_start + timedelta(days=30)
    d30_end = cohort_end + timedelta(days=30)

    def unavailable(reason: str) -> D30CohortResult:
        return D30CohortResult(scope, cohort_date, d30_start, d30_end, "unavailable", reason)

    if not _valid_scope(scope):
        return unavailable("invalid_scope")
    if isinstance(qualifying_action_ids, str):
        return unavailable("invalid_qualifying_actions")
    action_ids = set(qualifying_action_ids)
    if not action_ids or any(not isinstance(item, str) or not item.strip() or item != item.strip() for item in action_ids):
        return unavailable("invalid_qualifying_actions")
    observed_at = _utc(as_of)
    if observed_at is None:
        return unavailable("invalid_as_of")
    if observed_at < d30_end:
        return unavailable("immature_cohort")
    if not _coverage_spans(eligible_coverage, scope, cohort_start, cohort_end, observed_at):
        return unavailable("eligibility_coverage_unavailable")
    if not _coverage_spans(action_coverage, scope, d30_start, d30_end, observed_at):
        return unavailable("action_coverage_unavailable")

    first_eligible_by_user: dict[str, datetime] = {}
    for row in eligible_users:
        if not isinstance(row, EligibleEndUser):
            return unavailable("invalid_eligibility_record")
        at = _utc(row.first_eligible_at)
        if (
            row.scope != scope
            or not isinstance(row.user_id, str)
            or not row.user_id.strip()
            or row.user_id != row.user_id.strip()
            or at is None
        ):
            return unavailable("invalid_eligibility_record")
        previous = first_eligible_by_user.setdefault(row.user_id, at)
        if previous != at:
            return unavailable("conflicting_eligibility")

    cohort_ids = {
        user_id
        for user_id, at in first_eligible_by_user.items()
        if cohort_start <= at < cohort_end
    }
    if not cohort_ids:
        return unavailable("empty_cohort")

    returned_ids: set[str] = set()
    for row in actions:
        if not isinstance(row, ActionObservation):
            return unavailable("invalid_action_record")
        at = _utc(row.occurred_at)
        if (
            row.scope != scope
            or not isinstance(row.user_id, str)
            or not row.user_id.strip()
            or row.user_id != row.user_id.strip()
            or at is None
            or not isinstance(row.action_id, str)
            or not row.action_id.strip()
            or not isinstance(row.auth_provenance, str)
            or not row.auth_provenance.strip()
            or not isinstance(row.actor_kind, str)
            or not row.actor_kind.strip()
            or not isinstance(row.succeeded, bool)
        ):
            return unavailable("invalid_action_record")
        if (
            row.user_id in cohort_ids
            and d30_start <= at < d30_end
            and row.action_id in action_ids
            and row.auth_provenance == "token_validated"
            and row.actor_kind == "end_user"
            and row.succeeded is True
        ):
            returned_ids.add(row.user_id)

    denominator = len(cohort_ids)
    numerator = len(returned_ids)
    return D30CohortResult(
        scope=scope,
        cohort_date=cohort_date,
        d30_start=d30_start,
        d30_end=d30_end,
        status="available",
        denominator=denominator,
        numerator=numerator,
        rate_percent=round(100 * numerator / denominator, 2),
    )


__all__ = [
    "ActionObservation",
    "CohortScope",
    "D30CohortResult",
    "EligibleEndUser",
    "SourceCoverage",
    "calculate_d30_action_cohort",
]
