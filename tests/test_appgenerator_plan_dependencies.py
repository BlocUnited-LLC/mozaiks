"""A task cannot wait on a task the plan never declares.

A live build died here after the plan passed review:

    AG2 turn failed for AppPlanAgent: task batch 'app_build_tasks' has
    unresolved or cyclic dependencies:
    {'task_completion_4': ['task_completion_2', 'task_completion_3'],
     'task_completion_5': [..., 'task_borrow_requests_module_contract']}

`task_batches` raises that as a plain ValueError at batch-build time, which is
past the plan-revision loop -- so there is no feedback path and the build is
simply over. The existing repairs never caught it because they only strip
dependencies on tasks *they* removed, not on ids that were never declared.
"""

from __future__ import annotations

import pytest

from factory_app.workflows.AppGenerator.tools.app_plan_review import (
    validate_plan_dependencies,
)


def _task(task_id: str, depends_on: list[str] | None = None) -> dict:
    return {"task_id": task_id, "task_type": "module_contract", "depends_on": depends_on or []}


def test_a_plan_whose_dependencies_all_exist_is_accepted() -> None:
    plan = {"build_tasks": [_task("task_a"), _task("task_b", ["task_a"])]}
    validate_plan_dependencies(plan, None)


def test_the_live_failure_is_rejected_before_it_reaches_the_batch() -> None:
    plan = {
        "build_tasks": [
            _task("task_completion_4", ["task_completion_2", "task_completion_3"]),
            _task("task_completion_5", ["task_completion_4", "task_borrow_requests_module_contract"]),
        ]
    }
    with pytest.raises(ValueError) as err:
        validate_plan_dependencies(plan, None)
    message = str(err.value)
    # The agent has to know which ids to fix, so they must be named.
    for missing in ("task_completion_2", "task_completion_3", "task_borrow_requests_module_contract"):
        assert missing in message, f"{missing} must be named so the agent can act on it"
    # task_completion_4 is declared, so it must not be reported as missing.
    assert "task_completion_5 waits on ['task_borrow_requests_module_contract']" in message


def test_an_empty_or_absent_depends_on_is_not_a_dangling_reference() -> None:
    plan = {
        "build_tasks": [
            {"task_id": "task_a"},
            {"task_id": "task_b", "depends_on": None},
            {"task_id": "task_c", "depends_on": ["", "   "]},
        ]
    }
    validate_plan_dependencies(plan, None)


def test_an_empty_plan_is_not_an_error_here() -> None:
    """Coverage is a different validator's job; this one only judges edges."""
    validate_plan_dependencies({"build_tasks": []}, None)
    validate_plan_dependencies({}, None)


def test_review_runs_the_check_before_the_plan_is_cached() -> None:
    """A plan that fails this must never reach app_build_plan()."""
    import inspect

    from factory_app.workflows.AppGenerator.tools import app_plan_review

    source = inspect.getsource(app_plan_review.review_app_build_plan)
    # Skip the `def review_app_build_plan(` line: it contains "app_build_plan("
    # itself, which would make this comparison pass no matter what the body does.
    body = source[source.index(chr(10)) + 1 :]
    assert body.index("validate_plan_dependencies") < body.index("app_build_plan("), (
        "the dependency check must run before the plan is cached, or the batch "
        "still builds from a plan with unresolved edges"
    )
