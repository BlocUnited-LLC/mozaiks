"""Workflow bundle repair stops on unchanged findings within its finite budget."""


from __future__ import annotations

from typing import Any

from factory_app.workflows.AgentGenerator.tools.workflow_quality_gate import (
    prepare_workflow_bundle_repair,
)


class _Context:
    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self._values = dict(values or {})

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._values[key] = value


def _gate(issue: str = "Workflow alpha: orchestrator.yaml missing initial_agent") -> dict[str, Any]:
    return {
        "passed": False,
        "structure": {
            "workflows": [{"workflow_name": "alpha", "errors": [issue]}],
        },
    }


def _repair(context: _Context, gate: dict[str, Any]) -> dict[str, Any]:
    return prepare_workflow_bundle_repair(
        quality_gate=gate,
        bundle_entries=[{"workflow_name": "alpha"}],
        context_variables=context,
        max_attempts=3,
    )


def test_the_first_failure_is_repairable() -> None:
    context = _Context()

    result = _repair(context, _gate())

    assert result["status"] == "needs_revision"
    assert result["repairable"] is True
    assert result["no_progress"] is False
    assert context.get("workflow_bundle_repair_failure_fingerprint")


def test_an_identical_second_failure_stops_instead_of_retrying() -> None:
    """The drift: this used to spend attempt 2 on a provably unchanged failure."""
    context = _Context()
    _repair(context, _gate())

    result = _repair(context, _gate())

    assert result["status"] == "blocked"
    assert result["repairable"] is False
    assert result["no_progress"] is True
    assert result["reason"] == "workflow_bundle_repair_no_progress"
    # Budget remained - it stopped because nothing changed, not because it ran out.
    assert result["attempt"] < result["max_attempts"]


def test_a_different_failure_is_still_repairable() -> None:
    """Progress means new evidence, not merely fewer issues."""
    context = _Context()
    _repair(context, _gate())

    result = _repair(context, _gate("Workflow alpha: agents.yaml missing a system message"))

    assert result["status"] == "needs_revision"
    assert result["no_progress"] is False


def test_the_attempt_budget_still_blocks_when_progress_keeps_happening() -> None:
    context = _Context()
    for index in range(3):
        _repair(context, _gate(f"Workflow alpha: distinct issue {index}"))

    result = _repair(context, _gate("Workflow alpha: distinct issue final"))

    assert result["status"] == "blocked"
    assert result["reason"] == "workflow_bundle_repair_attempts_exhausted"
    assert result["no_progress"] is False


def test_reordered_workflow_findings_do_not_authorize_another_attempt() -> None:
    context = _Context()
    gate = _gate()
    gate["structure"]["workflows"].append({"workflow_name": "beta", "errors": ["missing agents.yaml"]})
    first = _repair(context, gate)
    gate["structure"]["workflows"].reverse()

    repeated = _repair(context, gate)

    assert repeated["status"] == "blocked"
    assert repeated["no_progress"] is True
    assert repeated["attempt"] == first["attempt"]


def test_the_new_keys_are_declared_so_a_transition_can_read_them() -> None:
    import yaml

    path = "factory_app/workflows/AgentGenerator/context_variables.yaml"
    definitions = yaml.safe_load(open(path, encoding="utf-8"))["definitions"]

    assert definitions["workflow_bundle_repair_no_progress"]["type"] == "boolean"
    assert definitions["workflow_bundle_repair_no_progress"]["source"]["default"] is False
    assert definitions["workflow_bundle_repair_failure_fingerprint"]["type"] == "string"
