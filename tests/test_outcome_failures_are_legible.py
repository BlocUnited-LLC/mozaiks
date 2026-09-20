"""An outcome tool that gives up must say why.

A live build died inside AgentGenerator after `pattern_selection` ran three
times. The backend log recorded only that the tool "completed successfully"
three times and then the workflow failed — the rejection reason existed, but
only in a context variable nobody logs.

Diagnosing that cost an hour and three wrong theories. These tests pin the
information into the log, for every outcome-contract tool rather than one.
"""

from __future__ import annotations

import logging

import pytest

from mozaiksai.core.workflow.declarative.contracts import ToolOutcomeSpec
from mozaiksai.core.workflow.validation.tool_outcomes import wrap_tool_outcome

SPEC = ToolOutcomeSpec.model_validate({
    "context_key": "demo_outcome",
    "attempts_key": "demo_attempts",
    "result_field": "outcome",
    "values": ["selected", "invalid", "blocked"],
    "error_value": "blocked",
    "max_attempts": 2,
    "retry_on": ["invalid"],
})


class _Ctx:
    def __init__(self, **seed) -> None:
        self._d = dict(seed)

    def get(self, key, default=None):
        return self._d.get(key, default)

    def set(self, key, value) -> None:
        self._d[key] = value


def test_a_rejection_reports_the_tools_own_reason(caplog) -> None:
    def picky(context_variables=None):
        return {"outcome": "invalid", "error": "An empty workflow partition requires a rationale"}

    wrapped = wrap_tool_outcome(picky, SPEC)
    with caplog.at_level(logging.WARNING):
        wrapped(context_variables=_Ctx(demo_attempts=0))

    assert "TOOL_OUTCOME_REJECTED" in caplog.text
    assert "An empty workflow partition requires a rationale" in caplog.text, (
        "the reason the tool already produced must reach the log"
    )
    assert "picky" in caplog.text and "1/2" in caplog.text


def test_a_terminal_failure_without_a_reason_says_so_rather_than_nothing(caplog) -> None:
    """Silence about the reason must itself be visible.

    Only the terminal value can carry this guarantee. A contract declares
    `error_value` but no success value, so a non-terminal outcome with no error
    is indistinguishable from a healthy one — reporting those would recreate the
    noise that hid these failures.
    """
    def terse(context_variables=None):
        return {"outcome": "blocked"}

    wrapped = wrap_tool_outcome(terse, SPEC)
    with caplog.at_level(logging.WARNING):
        wrapped(context_variables=_Ctx(demo_attempts=0))

    assert "TOOL_OUTCOME_REJECTED" in caplog.text
    assert "tool reported none" in caplog.text


def test_exhausting_the_budget_names_the_budget(caplog) -> None:
    def picky(context_variables=None):
        return {"outcome": "invalid", "error": "still wrong"}

    wrapped = wrap_tool_outcome(picky, SPEC)
    ctx = _Ctx(demo_attempts=SPEC.max_attempts, demo_outcome="invalid")
    with caplog.at_level(logging.WARNING):
        result = wrapped(context_variables=ctx)

    assert result["outcome"] == "blocked"
    assert "TOOL_OUTCOME_FAILED" in caplog.text
    assert "attempts_exhausted" in caplog.text
    assert "demo_outcome" in caplog.text and "max_attempts=2" in caplog.text


def test_an_unrecognised_outcome_shows_what_it_got(caplog) -> None:
    def confused(context_variables=None):
        return {"outcome": "probably_fine"}

    wrapped = wrap_tool_outcome(confused, SPEC)
    with caplog.at_level(logging.WARNING):
        wrapped(context_variables=_Ctx(demo_attempts=0))

    assert "TOOL_OUTCOME_UNRECOGNISED" in caplog.text
    assert "probably_fine" in caplog.text, "the offending value must be shown"


def test_success_stays_quiet(caplog) -> None:
    """Logging every successful call would bury the failures again.

    `selected` is deliberately listed in this contract's retry_on — that field
    says which previous outcomes permit another call, not which ones failed.
    pattern_selection allows a follow-up call after a successful selection, and
    a live run reported those as retries until this was keyed off the error
    instead.
    """
    def fine(context_variables=None):
        return {"outcome": "selected"}

    wrapped = wrap_tool_outcome(fine, SPEC)
    with caplog.at_level(logging.WARNING):
        wrapped(context_variables=_Ctx(demo_attempts=0))

    assert "TOOL_OUTCOME" not in caplog.text


@pytest.mark.asyncio
async def test_async_tools_are_covered_too(caplog) -> None:
    async def picky(context_variables=None):
        return {"outcome": "invalid", "error": "async reason"}

    wrapped = wrap_tool_outcome(picky, SPEC)
    with caplog.at_level(logging.WARNING):
        await wrapped(context_variables=_Ctx(demo_attempts=0))

    assert "async reason" in caplog.text


def test_a_success_listed_in_retry_on_is_not_reported_as_a_failure(caplog) -> None:
    """The exact false positive a live run produced.

    AgentGenerator's pattern_selection lists its own success value in retry_on,
    because a successful selection still permits a follow-up call. Keying the
    log off retry_on reported healthy runs as retries.
    """
    import logging

    permissive = ToolOutcomeSpec.model_validate({
        **SPEC.model_dump(), "retry_on": ["selected", "invalid"],
    })
    assert "selected" in permissive.retry_on

    def fine(context_variables=None):
        return {"outcome": "selected"}

    wrapped = wrap_tool_outcome(fine, permissive)
    with caplog.at_level(logging.WARNING):
        wrapped(context_variables=_Ctx(demo_attempts=1, demo_outcome="selected"))

    assert "TOOL_OUTCOME" not in caplog.text, (
        "a successful outcome that permits a follow-up call is not a rejection"
    )
