"""`app_task_batch_status` must stay writable by the tools that write it.

PR #678 gave this key an explicit ``writer_ids: [task_batch]``. An explicit
writer list *replaces* the inferred set instead of extending it, so
``deterministic_tool`` — the attribution every declared workflow tool runs
under — silently dropped off. Three tools write this key, and the first of
them runs before any validation in ``review_app_build_plan``, so AppGenerator
failed on its first plan review of every run.

The failure does not name the tool. ``resolve_declared_context_writer`` only
honours a declared writer that is already in ``writer_ids``; otherwise it falls
back to ``context_bridge`` *before* the authority check. So a writer missing
from the list is reported as ``writer=context_bridge``, which reads like the
bridge overstepping rather than the declaration being too narrow.

The existing compile-time drift guard cannot catch this: it asserts a routing
key has *some* deterministic writer, and ``task_batch`` is one. These tests
assert the writers that actually write the key are the writers it declares.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.app_plan_review import review_app_build_plan
from mozaiksai.core.workflow.agents.factory import (
    ContextVariablesBridge,
    _workflow_tool_invocation,
)
from mozaiksai.core.workflow.context.authority import (
    _ALL_WRITERS,
    CONTEXT_BRIDGE_WRITER,
    DETERMINISTIC_TOOL_WRITER,
    TASK_BATCH_WRITER,
    ContextAuthorityError,
    build_context_authority_policy,
)
from mozaiksai.core.workflow.task_batches import parse_task_batches_config

WORKFLOW = "AppGenerator"
KEY = "app_task_batch_status"
RUN = (WORKFLOW, "app-1", "chat-1")
WORKFLOW_DIR = Path(__file__).resolve().parents[1] / "factory_app" / "workflows" / WORKFLOW

# Every writer the runtime can attribute a mutation to that must NOT reach a
# closed quality-state key. context_bridge is the one this regression produced.
FORBIDDEN_WRITERS = frozenset(_ALL_WRITERS) - {
    DETERMINISTIC_TOOL_WRITER,
    TASK_BATCH_WRITER,
    "runtime_system",
}


def _load(name: str) -> dict:
    return yaml.safe_load((WORKFLOW_DIR / name).read_text(encoding="utf-8")) or {}


def _definitions() -> dict:
    return _load("context_variables.yaml").get("definitions") or {}


def _policy():
    """Rebuild the policy exactly as the runtime does for this workflow."""
    raw = yaml.safe_load(
        (WORKFLOW_DIR / "extended_orchestration" / "task_batches.yaml").read_text(encoding="utf-8")
    ) or {}
    batch_keys: set[str] = set()
    for batch in parse_task_batches_config(raw).batches:
        batch_keys.add(batch.result.context_key)
        batch_keys.add(batch.result.status_key)
    return build_context_authority_policy(
        workflow_name=WORKFLOW,
        definitions=_definitions(),
        transition_rules=_load("transition_graph.yaml").get("transition_rules") or [],
        task_batch_context_keys=batch_keys,
    )


def _bound_bridge(policy, **seed):
    bridge = ContextVariablesBridge({"user_id": "u-1", **seed}, authority_policy=policy)
    bridge._bind_run(RUN, policy)
    return bridge


def test_review_app_build_plan_can_clear_task_batch_status():
    """The live failure: `_clear_plan` is the first statement of the tool.

    An invalid plan is enough — the rejection happened before validation, so
    this reaches the regression without constructing a valid AppBuildPlan.
    Before the fix this raises ContextAuthorityError instead of returning.
    """
    policy = _policy()
    bridge = _bound_bridge(policy, app_plan_attempts=0)

    with _workflow_tool_invocation(bridge):
        result = review_app_build_plan(AppBuildPlan=None, context_variables=bridge)

    assert result["ok"] is False, "an invalid plan must still be rejected on its merits"
    assert bridge.get(KEY) is None, "the tool clears the status before replanning"


def test_task_batch_status_accepts_both_declared_mechanisms():
    """Both writers are real: the tools write `planned`, the runner writes the rest."""
    policy = _policy()

    for writer, value in ((DETERMINISTIC_TOOL_WRITER, "planned"), (TASK_BATCH_WRITER, "completed")):
        bridge = _bound_bridge(policy)
        with _workflow_tool_invocation(bridge, writer_id=writer):
            bridge.set(KEY, value)
        assert bridge.get(KEY) == value, f"{writer} must be able to advance {KEY}"


def test_context_bridge_remains_unauthorized():
    """The fix must not reopen the key to the untrusted bridge lane.

    An unbound bridge is how the runtime represents 'no trusted invocation
    established', and it is the exact shape that produced the live rejection.
    """
    policy = _policy()
    unbound = ContextVariablesBridge({"user_id": "u-1"}, authority_policy=policy)

    assert not policy.can_write(KEY, writer_id=CONTEXT_BRIDGE_WRITER)
    with pytest.raises(ContextAuthorityError) as excinfo, _workflow_tool_invocation(unbound):
        unbound.set(KEY, "planned")
    assert f"key={KEY}" in str(excinfo.value)
    assert f"writer={CONTEXT_BRIDGE_WRITER}" in str(excinfo.value)


@pytest.mark.parametrize("writer", sorted(FORBIDDEN_WRITERS))
def test_unrelated_writers_remain_rejected(writer):
    """Widening the list by one writer must not widen it by any other."""
    assert not _policy().can_write(KEY, writer_id=writer)


def test_declared_writers_cover_every_tool_that_writes_the_key():
    """The guard the compile-time drift check cannot provide.

    That check only asks whether a routing key has *some* deterministic
    writer, which `task_batch` satisfied while the tools were locked out. This
    asserts the declaration lists the mechanisms that actually write the key,
    so narrowing it again fails here instead of in a live run.
    """
    declared = set(_definitions()[KEY]["writer_ids"])
    assert declared == {DETERMINISTIC_TOOL_WRITER, TASK_BATCH_WRITER}, (
        f"{KEY} is written by declared workflow tools (app_build_plan, "
        "review_app_build_plan) and by the task batch runner; both must be declared"
    )


def test_task_batch_results_stays_batch_only():
    """The sibling key #678 also narrowed is correct as-is — no tool writes it.

    Pinned so this fix is not mirrored onto a key that does not need it.
    """
    assert set(_definitions()["app_task_batch_results"]["writer_ids"]) == {TASK_BATCH_WRITER}
