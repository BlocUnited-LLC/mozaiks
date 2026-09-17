"""A task being told exactly what is wrong deserves more than one retry.

A live build failed the whole batch at the module contract task:

    Schema contract required names must match property required flags

That rule is already stated in ConfigMiddlewareAgent's prompt, the validator
message is fed back verbatim on retry together with the rejected candidate, and
the task still failed after both attempts.

The feedback loop is sound - task_batches.py appends [TASK VALIDATION FEEDBACK]
and [REJECTED TASK OUTPUT] to the retry prompt. What was short was the budget.

The original justification here was fail_batch: one task giving up discarded
every completed task in the run. That is no longer true - the batch now runs
continue_with_available, so a failure costs its dependent subtree rather than
the build. The budget still earns its keep, because that subtree is the module
and every page bound to it, but it is no longer the whole run.
"""

from pathlib import Path

import yaml

BATCHES = (
    Path(__file__).resolve().parents[1]
    / "factory_app"
    / "workflows"
    / "AppGenerator"
    / "extended_orchestration"
    / "task_batches.yaml"
)


def _execution() -> dict:
    config = yaml.safe_load(BATCHES.read_text(encoding="utf-8"))
    return next(b for b in config["batches"] if b["id"] == "app_build_tasks")["execution"]


def test_a_failing_task_gets_more_than_one_correction_attempt() -> None:
    execution = _execution()

    assert execution["retry_limit"] >= 3, (
        "A task is given the validator error and its rejected output on retry. "
        "One attempt to act on that is not enough for mechanical contract errors."
    )


def test_a_failure_costs_its_subtree_and_not_the_build() -> None:
    """This test previously asserted fail_batch, which was the defect.

    A live build lost 1.3h and five completed tasks to one module_contract
    failure. The scheduler already drains dependents of a failed task, so
    continue_with_available stops the subtree and lets acceptance judge the
    rest - which is the only way the bundle repair loop ever gets to run.
    """
    execution = _execution()

    assert execution["failure_policy"] == "continue_with_available"


def test_the_retry_prompt_still_carries_the_error_and_the_rejected_output() -> None:
    """The budget only helps because the feedback is real; pin both."""
    source = (
        Path(__file__).resolve().parents[1]
        / "mozaiksai"
        / "core"
        / "workflow"
        / "task_batches.py"
    ).read_text(encoding="utf-8")

    assert "[TASK VALIDATION FEEDBACK]" in source
    assert "[REJECTED TASK OUTPUT]" in source
    assert "retry_limit + 1" in source
