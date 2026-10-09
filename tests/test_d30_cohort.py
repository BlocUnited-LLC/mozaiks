"""The D30 calculator only releases counts from mature, complete app-local evidence."""

from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from mozaiksai.core.metrics.d30_cohort import (
    ActionObservation,
    CohortScope,
    EligibleEndUser,
    SourceCoverage,
    calculate_d30_action_cohort,
)

SCOPE = CohortScope("app-1", "production", "tenant-1", "workspace-1")
OTHER_SCOPE = CohortScope("app-1", "production", "tenant-1", "workspace-2")
TEST_SCOPE = CohortScope("app-1", "test", "tenant-1", "workspace-1")
DAY = date(2026, 8, 1)
START = datetime(2026, 8, 1, tzinfo=UTC)
D30 = START + timedelta(days=30)
D31 = START + timedelta(days=31)


def eligible(user_id: str, at: datetime = START) -> EligibleEndUser:
    return EligibleEndUser(SCOPE, user_id, at)


def action(
    user_id: str,
    at: datetime = D30,
    *,
    action_id: str = "tasks.complete",
    provenance: str = "token_validated",
    actor_kind: str = "end_user",
    succeeded: bool = True,
) -> ActionObservation:
    return ActionObservation(SCOPE, user_id, at, action_id, provenance, actor_kind, succeeded)


def coverage(start: datetime, through: datetime) -> SourceCoverage:
    return SourceCoverage(SCOPE, start, through, authoritative=True, continuous=True)


def calculate(*, eligible_users=None, actions=None, eligible_coverage=None, action_coverage=None, as_of=None):
    return calculate_d30_action_cohort(
        scope=SCOPE,
        cohort_date=DAY,
        eligible_users=[eligible("u1"), eligible("u2")] if eligible_users is None else eligible_users,
        actions=[action("u1")] if actions is None else actions,
        eligible_coverage=coverage(START, START + timedelta(days=1)) if eligible_coverage is None else eligible_coverage,
        action_coverage=coverage(D30, D31) if action_coverage is None else action_coverage,
        qualifying_action_ids={"tasks.complete"},
        as_of=D31 if as_of is None else as_of,
    )


def test_d30_joins_distinct_eligible_end_users_to_auth_success_on_utc_day_30():
    eastern = timezone(timedelta(hours=-4))
    result = calculate(
        eligible_users=[eligible("u1"), eligible("u1"), eligible("u2"), eligible("u3", START - timedelta(days=1))],
        actions=[
            action("u1", D30),
            action("u1", D30 + timedelta(hours=3)),
            action("u2", D30 - timedelta(microseconds=1)),
            action("u2", D31),
            action("u2", D30 + timedelta(hours=2), provenance="anonymous"),
            action("u2", D30 + timedelta(hours=2), actor_kind="operator"),
            action("u2", D30 + timedelta(hours=2), succeeded=False),
            action("u2", D30 + timedelta(hours=2), action_id="tasks.read"),
            action("u3", D30),
            action("u1", D30.astimezone(eastern)),
        ],
    )
    assert (result.status, result.denominator, result.numerator, result.rate_percent) == (
        "available", 2, 1, 50.0,
    )
    assert result.d30_start == D30
    assert result.d30_end == D31
    assert "u1" not in str(asdict(result))


def test_complete_mature_cohort_with_no_actions_is_zero_not_missing():
    result = calculate(actions=[])
    assert (result.status, result.denominator, result.numerator, result.rate_percent) == (
        "available", 2, 0, 0.0,
    )


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"as_of": D31 - timedelta(microseconds=1)}, "immature_cohort"),
        ({"eligible_coverage": coverage(START + timedelta(seconds=1), START + timedelta(days=1))}, "eligibility_coverage_unavailable"),
        ({"action_coverage": coverage(D30, D31 - timedelta(seconds=1))}, "action_coverage_unavailable"),
        ({"action_coverage": coverage(D30, D31 + timedelta(seconds=1))}, "action_coverage_unavailable"),
        ({"action_coverage": SourceCoverage(SCOPE, D30, D31, authoritative=True)}, "action_coverage_unavailable"),
        ({"action_coverage": SourceCoverage(OTHER_SCOPE, D30, D31, authoritative=True, continuous=True)}, "action_coverage_unavailable"),
        ({"action_coverage": SourceCoverage(TEST_SCOPE, D30, D31, authoritative=True, continuous=True)}, "action_coverage_unavailable"),
        ({"eligible_coverage": SourceCoverage(SCOPE, START, START + timedelta(days=1), continuous=True)}, "eligibility_coverage_unavailable"),
        ({"eligible_users": []}, "empty_cohort"),
        ({"eligible_users": [eligible("u1"), eligible("u1", START + timedelta(hours=1))]}, "conflicting_eligibility"),
        ({"eligible_users": [EligibleEndUser(OTHER_SCOPE, "u1", START)]}, "invalid_eligibility_record"),
        ({"eligible_users": [EligibleEndUser(TEST_SCOPE, "u1", START)]}, "invalid_eligibility_record"),
        ({"actions": [ActionObservation(OTHER_SCOPE, "u1", D30, "tasks.complete", "token_validated", "end_user", True)]}, "invalid_action_record"),
        ({"actions": [ActionObservation(TEST_SCOPE, "u1", D30, "tasks.complete", "token_validated", "end_user", True)]}, "invalid_action_record"),
        ({"actions": [action("u1", provenance="")]}, "invalid_action_record"),
        ({"actions": [action("u1", actor_kind="")]}, "invalid_action_record"),
    ],
)
def test_untrusted_or_incomplete_inputs_never_release_cohort_counts(changes, reason):
    result = calculate(**changes)
    assert result.status == "unavailable"
    assert result.reason == reason
    assert result.denominator is None
    assert result.numerator is None
    assert result.rate_percent is None


def test_naive_timestamp_and_scope_wildcards_fail_closed():
    result = calculate(actions=[action("u1", datetime(2026, 8, 31))])
    assert result.reason == "invalid_action_record"
    result = calculate_d30_action_cohort(
        scope=CohortScope("app-1", "production", "tenant-1"),
        cohort_date=DAY,
        eligible_users=[eligible("u1")],
        actions=[action("u1")],
        eligible_coverage=coverage(START, START + timedelta(days=1)),
        action_coverage=coverage(D30, D31),
        qualifying_action_ids={"tasks.complete"},
        as_of=D31,
    )
    assert result.reason == "eligibility_coverage_unavailable"


def test_qualifying_action_set_must_be_explicit():
    result = calculate_d30_action_cohort(
        scope=SCOPE,
        cohort_date=DAY,
        eligible_users=[eligible("u1")],
        actions=[action("u1")],
        eligible_coverage=coverage(START, START + timedelta(days=1)),
        action_coverage=coverage(D30, D31),
        qualifying_action_ids=[],
        as_of=D31,
    )
    assert result.reason == "invalid_qualifying_actions"
    assert result.denominator is None


@pytest.mark.parametrize("environment", ["", " ", "default", " DEFAULT "])
def test_missing_or_placeholder_environment_fails_closed(environment):
    result = calculate_d30_action_cohort(
        scope=CohortScope("app-1", environment, "tenant-1", "workspace-1"),
        cohort_date=DAY,
        eligible_users=[eligible("u1")],
        actions=[action("u1")],
        eligible_coverage=coverage(START, START + timedelta(days=1)),
        action_coverage=coverage(D30, D31),
        qualifying_action_ids={"tasks.complete"},
        as_of=D31,
    )
    assert result.reason == "invalid_scope"
    assert result.denominator is None


def test_scope_constructor_requires_explicit_environment():
    with pytest.raises(TypeError):
        CohortScope(app_id="app-1")


def test_plain_string_is_not_an_action_id_set():
    result = calculate_d30_action_cohort(
        scope=SCOPE,
        cohort_date=DAY,
        eligible_users=[eligible("u1")],
        actions=[action("u1")],
        eligible_coverage=coverage(START, START + timedelta(days=1)),
        action_coverage=coverage(D30, D31),
        qualifying_action_ids="tasks.complete",
        as_of=D31,
    )
    assert result.reason == "invalid_qualifying_actions"
