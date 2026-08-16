from __future__ import annotations

import inspect
from typing import Any, cast

import pytest

from mozaiksai.core.adapters.ag2_task_batch_runner import (
    AG2TaskBatchRunnerRequest,
    AG2TaskBatchRunnerResult,
)
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.workflow.module_contract_executor import (
    MODULE_CONTRACT_AGENT_NAME,
    MODULE_CONTRACT_STRUCTURED_OUTPUT_ID,
    MODULE_CONTRACT_VALIDATOR_ID,
    ModuleContractExecutor,
    register_module_contract_executor,
)
from mozaiksai.core.workflow.queue import QueueItem
from mozaiksai.core.workflow.work_assignment_worker import (
    WORK_ASSIGNMENT_PAYLOAD_KEY,
    WorkAssignmentExecutionContext,
    WorkAssignmentExecutorRegistry,
    WorkAssignmentLeaseLostError,
    WorkAssignmentPermanentError,
    WorkAssignmentTransientError,
)
from mozaiksai.core.workflow.work_contracts import (
    WorkAssignment,
    make_work_assignment,
)


class FakeRunner:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[AG2TaskBatchRunnerRequest] = []

    async def run(self, request: AG2TaskBatchRunnerRequest) -> AG2TaskBatchRunnerResult:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return cast(AG2TaskBatchRunnerResult, outcome)


class FakeAgent:
    pass


def _assignment(*, owned_paths: list[str] | None = None, **overrides: Any) -> WorkAssignment:
    payload = {
        "assignment_id": "module-contract-1",
        "plan_id": "plan-1",
        "plan_digest": "a" * 64,
        "baseline_sha": "b" * 40,
        "assignment_kind": "module_contract",
        "owned_paths": owned_paths or ["modules/inventory/module.yaml"],
        "allowed_agent_ids": [MODULE_CONTRACT_AGENT_NAME],
        "required_structured_output_id": MODULE_CONTRACT_STRUCTURED_OUTPUT_ID,
        "required_validators": [MODULE_CONTRACT_VALIDATOR_ID],
    }
    payload.update(overrides)
    return make_work_assignment(**cast(Any, payload))


def _item(assignment: WorkAssignment) -> QueueItem:
    return QueueItem(
        workflow_name="WorkAssignmentWorker",
        chat_id="chat-1",
        app_id="app-1",
        user_id="user-1",
        tenant_id="tenant-1",
        payload={WORK_ASSIGNMENT_PAYLOAD_KEY: assignment.model_dump(mode="json"), "workspace_id": "workspace-1"},
    )


def _context(assignment: WorkAssignment, *, renew_results: list[bool] | None = None) -> WorkAssignmentExecutionContext:
    renews = list(renew_results or [True, True, True])

    async def _renew(_extend_seconds: int | None = None) -> bool:
        return renews.pop(0) if renews else True

    return WorkAssignmentExecutionContext(
        assignment=assignment,
        item=_item(assignment),
        claim_token="claim-1",
        queue_attempt_count=1,
        executor_attempt=1,
        worker_id="worker-1",
        allowed_agent_ids=assignment.allowed_agent_ids,
        owned_paths=assignment.owned_paths,
        required_structured_output_id=assignment.required_structured_output_id,
        workspace_id="workspace-1",
        renew_lease=_renew,
    )


def _runner_result(output: dict[str, Any], *, status: RunStatus = RunStatus.COMPLETED, close_reason: str = "ok") -> AG2TaskBatchRunnerResult:
    return AG2TaskBatchRunnerResult(status=status, output=output, close_reason=close_reason)


def _module_output(
    *,
    module_id: str = "inventory",
    module_type: str = "standard",
    event_type: str | None = None,
    code_files: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    emits = [event_type] if event_type else []
    output: dict[str, Any] = {
        "mode": "module_contract_bundle",
        "module_contract": {
            "module_id": module_id,
            "module_yaml": {
                "schema_version": "mozaiks.module.v1",
                "module": {
                    "id": module_id,
                    "display_name": "Inventory",
                    "version": "1.0.0",
                    "type": module_type,
                    "description": "Tracks inventory.",
                    "handler": "backend.handler:InventoryModule",
                },
                "permissions": [
                    {"id": "inventory.read", "description": "Read inventory."},
                ],
                "actions": [
                    {
                        "id": "list_items",
                        "description": "List items.",
                        "handler_method": "list_items",
                        "permissions": ["inventory.read"],
                        "emits": emits,
                        "input_schema": {"type": "object", "properties": {}},
                        "output_schema": {"type": "object", "properties": {}},
                    }
                ],
                "capabilities": [
                    {
                        "capability_id": "inventory.list_items",
                        "kind": "action",
                        "target": "list_items",
                        "title": "List Items",
                        "permissions": ["inventory.read"],
                    }
                ],
            },
            "events_yaml": {
                "schema_version": "mozaiks.events.v1",
                "events": (
                    [
                        {
                            "type": event_type,
                            "version": 1,
                            "description": "Inventory listed.",
                            "producer": module_id,
                            "payload_schema": {"type": "object", "properties": {}},
                        }
                    ]
                    if event_type
                    else []
                ),
            },
            "reactions_yaml": {"schema_version": "mozaiks.reactions.v1", "reactions": []},
            "notifications_yaml": {"schema_version": "mozaiks.notifications.v1", "notifications": []},
            "settings_yaml": {"schema_version": "mozaiks.settings.v1", "settings": [], "features": []},
            "admin_yaml": {"schema_version": "mozaiks.admin.v2", "panels": []},
            "profile_yaml": None,
            "relationships_yaml": None,
            "policy_hooks_yaml": None,
            "python_stubs": [],
            "js_stubs": [],
            "runtime_extensions_yaml": None,
        },
        "service_foundation_bundle": None,
        "subscription_config_bundle": None,
        "code_files": code_files or [],
        "deleted_files": [],
        "agent_message": "Generated inventory contract.",
    }
    return output


@pytest.mark.asyncio
async def test_module_contract_executor_returns_deterministic_work_result() -> None:
    assignment = _assignment(
        owned_paths=[
            "modules/inventory/contracts/events.yaml",
            "modules/inventory/module.yaml",
        ]
    )
    output = _module_output(event_type="domain.inventory.listed")
    executor = ModuleContractExecutor(agent=FakeAgent(), runner=FakeRunner([_runner_result(output), _runner_result(output)]))

    first = await executor.execute(_context(assignment))
    second = await executor.execute(_context(assignment))

    assert first.result_digest == second.result_digest
    assert first.output_digest == second.output_digest
    assert [artifact.path for artifact in first.changed_artifacts] == [
        "modules/inventory/contracts/events.yaml",
        "modules/inventory/module.yaml",
    ]
    assert first.validation_evidence[0].validator_id == MODULE_CONTRACT_VALIDATOR_ID


@pytest.mark.asyncio
async def test_executor_ignores_model_supplied_backend_code_files() -> None:
    assignment = _assignment()
    output = _module_output(
        code_files=[
            {
                "filename": "modules/inventory/backend/handler.py",
                "content": "raise RuntimeError('must be ignored')\n",
            }
        ]
    )
    executor = ModuleContractExecutor(agent=FakeAgent(), runner=FakeRunner([_runner_result(output)]))

    result = await executor.execute(_context(assignment))

    assert [artifact.path for artifact in result.changed_artifacts] == ["modules/inventory/module.yaml"]


@pytest.mark.asyncio
async def test_assignment_backend_owned_path_is_rejected_before_runner() -> None:
    assignment = _assignment(owned_paths=["modules/inventory/module.yaml", "modules/inventory/backend/handler.py"])
    runner = FakeRunner([_runner_result(_module_output())])
    executor = ModuleContractExecutor(agent=FakeAgent(), runner=runner)

    with pytest.raises(WorkAssignmentPermanentError, match="non-contract artifact"):
        await executor.execute(_context(assignment))

    assert runner.requests == []


@pytest.mark.asyncio
async def test_unknown_module_taxonomy_is_permanent_failure() -> None:
    assignment = _assignment()
    executor = ModuleContractExecutor(
        agent=FakeAgent(),
        runner=FakeRunner([_runner_result(_module_output(module_type="unknown"))]),
    )

    with pytest.raises(WorkAssignmentPermanentError):
        await executor.execute(_context(assignment))


@pytest.mark.asyncio
async def test_structured_output_validation_failure_is_permanent() -> None:
    assignment = _assignment()
    executor = ModuleContractExecutor(
        agent=FakeAgent(),
        runner=FakeRunner(
            [
                AG2TaskBatchRunnerResult(
                    status=RunStatus.FAILED,
                    close_reason="structured_output_validation_failed",
                    error="bad json",
                )
            ]
        ),
    )

    with pytest.raises(WorkAssignmentPermanentError, match="structured output"):
        await executor.execute(_context(assignment))


@pytest.mark.asyncio
async def test_validator_failure_is_permanent() -> None:
    assignment = _assignment(owned_paths=["modules/inventory/module.yaml", "modules/inventory/contracts/events.yaml"])
    output = _module_output(event_type="domain.inventory.listed")
    output["module_contract"]["events_yaml"]["events"][0]["producer"] = "other"
    executor = ModuleContractExecutor(agent=FakeAgent(), runner=FakeRunner([_runner_result(output)]))

    with pytest.raises(WorkAssignmentPermanentError, match="producer"):
        await executor.execute(_context(assignment))


@pytest.mark.asyncio
async def test_lost_lease_does_not_publish_authoritative_result() -> None:
    assignment = _assignment()
    executor = ModuleContractExecutor(agent=FakeAgent(), runner=FakeRunner([_runner_result(_module_output())]))

    with pytest.raises(WorkAssignmentLeaseLostError):
        await executor.execute(_context(assignment, renew_results=[True, False]))


@pytest.mark.asyncio
async def test_transient_runner_failure_is_retryable() -> None:
    assignment = _assignment()
    executor = ModuleContractExecutor(agent=FakeAgent(), runner=FakeRunner([ConnectionError("rate limited")]))

    with pytest.raises(WorkAssignmentTransientError):
        await executor.execute(_context(assignment))


def test_register_module_contract_executor_uses_closed_registry() -> None:
    registry = WorkAssignmentExecutorRegistry()
    executor = register_module_contract_executor(registry, agent=FakeAgent(), runner=FakeRunner([]))

    assert registry.resolve("module_contract") is executor


def test_executor_source_has_no_arbitrary_import_or_repository_mutation() -> None:
    import mozaiksai.core.workflow.module_contract_executor as module_contract_executor

    source = inspect.getsource(module_contract_executor)
    forbidden = [
        "__import__(",
        "importlib",
        "subprocess",
        ".write_text(",
        ".open(",
        "git ",
        "github",
    ]
    assert not any(term in source for term in forbidden)
