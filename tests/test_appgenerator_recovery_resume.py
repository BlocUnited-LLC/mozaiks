"""AppGenerator continuation with real Hub, context bridge, and task execution.

Only agent responses and validation diagnostics are deterministic fixtures. The
Factory graph, repair policies, AG2 task lifecycle, and checkpointing are real.
"""

from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from ag2 import Agent
from ag2.knowledge import MemoryKnowledgeStore

from factory_app.workflows.AppGenerator.tools.app_validation import (
    _record_validation_outcome,
    validate_app_bundle_from_request,
)
from factory_app.workflows.AppGenerator.tools.code_file_utils import admitted_app_file_map
from factory_app.workflows.AppGenerator.tools.repair_policy import (
    prepare_bundle_repair,
    prepare_task_recovery,
)
from factory_app.workflows.AppGenerator.tools.task_integrity import (
    mark_repair_responded,
    validate_repair_candidate,
)
from mozaiksai.core.adapters.ag2_network_runner import (
    CHANNEL_TERMINAL_ERROR,
    AG2NetworkRunner,
    AG2NetworkRunnerRequest,
    checkpoint_agent_context,
)
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.workflow import task_batches as tb
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge, _workflow_tool_invocation
from mozaiksai.core.workflow.context.authority import (
    DETERMINISTIC_TOOL_WRITER,
    TASK_BATCH_WRITER,
    build_context_authority_policy,
)
from tests.test_generated_app_functional_acceptance import _basic_crud_files

WORKFLOWS = Path(__file__).resolve().parents[1] / "factory_app" / "workflows"


@pytest.mark.asyncio
@pytest.mark.parametrize("continuation", ["live", "reopen"])
async def test_readiness_user_reply_materializes_auth_before_real_validation(continuation):
    rules = yaml.safe_load(
        (WORKFLOWS / "AppGenerator" / "transition_graph.yaml").read_text(encoding="utf-8")
    )["transition_rules"]
    definitions = yaml.safe_load(
        (WORKFLOWS / "AppGenerator" / "context_variables.yaml").read_text(encoding="utf-8")
    )["definitions"]
    policy = build_context_authority_policy(
        workflow_name="AppGenerator", definitions=definitions, transition_rules=rules,
    )
    files = _basic_crud_files()
    manifest = json.loads(files["app.json"])
    manifest["authRequired"] = True
    files["app.json"] = json.dumps(manifest)
    initial = {
        "generated_files": files, "coding_participation": "autonomous",
        "app_build_plan": {"build_tasks": [{
            "task_id": "app_schema", "task_type": "page_bundle", "initial_agent": "AppSchemaAgent",
            "owned_paths": ["app.json"], "depends_on": [],
        }]},
        "app_task_batch_results": {
            "app_schema": {"code_files": [{"filename": "app.json", "content": files["app.json"]}]},
            "_meta": {"status": "completed"},
        },
    }
    bridge = ContextVariablesBridge(initial, authority_policy=policy)
    speakers = []
    validations = []

    class DeterministicAgent(Agent):
        async def ask(self, *msg, **kwargs):
            speakers.append(self.name)
            return SimpleNamespace(body="deterministic response")

    async def output_hook(agent_name, envelope):
        with _workflow_tool_invocation(bridge):
            if agent_name == "IntegrationReadinessAgent":
                bridge.set("integration_readiness_status", "blocked")
            elif agent_name == "AppValidationAgent":
                validations.append(await validate_app_bundle_from_request(
                    {"validation_strategy": "skip", "start_dev_server": False},
                    context_variables=bridge,
                ))
            elif agent_name != "DownloadAgent":
                raise AssertionError(f"Unexpected turn: {agent_name}")

    names = {
        name for rule in rules for name in (rule["source_agent"], rule["target_agent"])
        if name not in {"user", "terminate"}
    }
    agents = {name: DeterministicAgent(name, prompt="Deterministic test response") for name in names}
    for agent in agents.values():
        agent._mozaiks_context_bridge = bridge
    store = MemoryKnowledgeStore()

    def request(message, *, reopen=False):
        return AG2NetworkRunnerRequest(
            workflow_name="AppGenerator", chat_id="auth-readiness-resume", app_id="auth-app",
            agents=agents, initial_agent_name="IntegrationReadinessAgent", initial_message=message,
            transition_rules=rules, context_variables=initial, knowledge_store=store,
            agent_output_handler=output_hook,
            context_authority_policy=policy, resume_existing_only=reopen, idle_timeout_seconds=30.0,
        )

    result = await AG2NetworkRunner().run(request("Check integration readiness"))
    try:
        assert result.status is RunStatus.PAUSED, result.error
        assert speakers == ["IntegrationReadinessAgent"]
        assert "config/auth.yaml" not in result.context_variables["generated_files"]
        if continuation == "reopen":
            await result.live_run.close()
            result = await AG2NetworkRunner().run(request("Confirmed", reopen=True))
        else:
            result = await result.live_run.continue_with_user_message("Confirmed")
        assert result.status is RunStatus.FAILED, result.error
        assert result.close_reason == "workflow_failed"
        acceptance = validations[0]["app_bundle_acceptance_result"]
        assert acceptance["status"] == "pending"
        assert acceptance["validation_evidence"]["failed"] == []
        assert acceptance["validation_evidence"]["skipped"] == ["app_runtime_smoke"]
        assert validations[0]["integration_tests_passed"] is False
        assert validations[0]["app_validation_result"]["validation_status"] == "pending"
        assert speakers == ["IntegrationReadinessAgent", "AppValidationAgent"]
        assert validations[0]["app_runtime_load_result"]["passed"] is True
        assert validations[0]["bundle_scan_result"]["passed"] is True
        generated = admitted_app_file_map(result.context_variables)
        assert "schema_version: mozaiks.auth.v1" in generated["config/auth.yaml"]
        assert {page["path"] for page in json.loads(generated["ui/route_manifest.json"])["pages"]} == {
            "/orders", "/login", "/auth/callback",
        }
    finally:
        if result.live_run is not None:
            await result.live_run.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("continuation", ["uninterrupted", "live", "reopen"])
@pytest.mark.parametrize("pause_before_artifact_repairs", [False, True], ids=["spent-budget", "available-budget"])
async def test_repair_rounds_preserve_execution_and_budgets_across_resume(
    continuation, pause_before_artifact_repairs,
):
    rules = yaml.safe_load(
        (WORKFLOWS / "AppGenerator" / "transition_graph.yaml").read_text(encoding="utf-8")
    )["transition_rules"]
    config = tb.load_task_batches_config("AppGenerator", workflows_root=WORKFLOWS)
    definitions = yaml.safe_load(
        (WORKFLOWS / "AppGenerator" / "context_variables.yaml").read_text(encoding="utf-8")
    )["definitions"]
    batch_keys = {"app_task_batch_results", "app_task_batch_status",
                  "app_task_recovery_result", "app_task_recovery_status"}
    policy = build_context_authority_policy(
        workflow_name="AppGenerator", definitions=definitions,
        transition_rules=rules, task_batch_context_keys=batch_keys,
    )
    tasks = [
        {"task_id": "models", "initial_agent": "ModelAgent", "initial_message": "Emit schemas",
         "owned_paths": ["modules/tasks/backend/schemas.py"], "depends_on": []},
        {"task_id": "services", "initial_agent": "ServiceAgent", "initial_message": "Emit service",
         "owned_paths": ["modules/tasks/backend/service.py"], "depends_on": ["models"]},
        {"task_id": "page", "task_type": "page_bundle", "initial_agent": "AppSchemaAgent", "initial_message": "Emit page",
         "owned_paths": ["ui/pages/tasks.yaml"], "depends_on": ["services"]},
    ]
    initial_context = {
        "coding_participation": "autonomous", "interview_outcome": "blocked",
        "build_task_model": "AppBuildTask", "build_timestamp": "2026-09-26T00:00:00Z",
        "app_build_plan": {"build_tasks": tasks, "pages": [{"name": "tasks", "route": "/tasks"}]},
        "app_task_batch_items": tasks,
        "app_plan_outcome": "ready", "app_task_batch_results": None,
    }
    if pause_before_artifact_repairs:
        # An integration-readiness pause after batch recovery leaves the independent
        # artifact proposal budget untouched; a user retry must reach validation.
        initial_context["integration_readiness_status"] = "blocked"
    store = MemoryKnowledgeStore()
    bridge = ContextVariablesBridge(initial_context, authority_policy=policy)
    task_calls = Counter()
    agent_calls = Counter()
    triggers = []
    snapshots = []
    artifact_repairs = []

    class DeterministicAgent(Agent):
        async def ask(self, *msg, **kwargs):
            worker_context = kwargs.get("variables") or {}
            task = worker_context.get("current_task")
            if worker_context.get("task_run_mode") and task:
                task_id = task["task_id"]
                task_calls[task_id] += 1
                # The real runner must receive the checkpointed attempt and dependencies.
                evidence = bridge.get("app_task_batch_results")
                assert evidence["_meta"]["in_flight"][task_id]["attempt"] == task_calls[task_id]
                assert set(worker_context["dependency_task_outputs"]) == set(task["depends_on"])
                if task_id == "page":
                    return SimpleNamespace(body=json.dumps({
                        "agent_message": "Tasks page generated.", "manifest": None,
                        "pages": [{
                            "schema_version": "mozaiks.app_page.v1", "name": "tasks", "route": "/tasks",
                            "title": "Tasks", "page_type": "record_list", "layout": "full-width",
                            "sections": [{"id": "heading", "primitive": "PageHeader", "config": {"title": "Tasks"}}],
                        }],
                        "custom_route_bundle": None, "theme_config_patch": None, "shell_config": None,
                        "asset_manifest": None,
                    }))
                files = [{"filename": path, "content": "accepted"} for path in task["owned_paths"]]
                if task_id == "services" and task_calls[task_id] == 1:
                    files.append({"filename": "modules/tasks/backend/schemas.py", "content": "foreign"})
                typed_files = {
                    "models": {"model_files": []},
                    "services": {"python_files": []},
                }
                return SimpleNamespace(body=json.dumps({
                    **typed_files[task_id], "code_files": files, "agent_message": None,
                }))

            agent_calls[self.name] += 1
            with _workflow_tool_invocation(bridge, writer_id=DETERMINISTIC_TOOL_WRITER):
                if self.name == "AppValidationAgent":
                    recovery = prepare_task_recovery(bridge)
                    if recovery is None:
                        # Different diagnostics permit both original artifact proposals.
                        # The third diagnostic is deferred after the two-proposal ceiling.
                        attempts = bridge.get("bundle_repair_attempt_count", 0)
                        repair = prepare_bundle_repair({
                            "passed": False, "diagnostics": [{
                                "path": "modules/tasks/backend/service.py",
                                "error": f"service runtime check {attempts} failed",
                            }],
                        }, bridge)
                        bridge.set("app_validation_status", "failed")
                        _record_validation_outcome(
                            bridge, files=admitted_app_file_map(bridge),
                            acceptance={"status": "failed", "bundle_repair": repair},
                            validation={"validation_status": "failed"}, passed=False,
                        )
                elif self.name == "ServiceAgent":
                    candidate = {"modules/tasks/backend/service.py": f"repair {len(artifact_repairs) + 1}"}
                    artifact_repairs.append(validate_repair_candidate(bridge, candidate))
                    mark_repair_responded(bridge)
            return SimpleNamespace(body="deterministic response")

    names = {
        name for rule in rules for name in (rule["source_agent"], rule["target_agent"])
        if name not in {"user", "terminate"}
    }
    agents = {name: DeterministicAgent(name, prompt="Deterministic test response") for name in names}
    for agent in agents.values():
        agent._mozaiks_context_bridge = bridge

    async def output_hook(agent_name, envelope):
        if agent_name not in {"AppPlanAgent", "AppValidationAgent"}:
            return
        triggers.append(agent_name)
        context = bridge.snapshot()

        async def checkpoint(updates):
            assert set(updates) <= batch_keys
            with _workflow_tool_invocation(bridge, writer_id=TASK_BATCH_WRITER):
                for key, value in updates.items():
                    bridge.set(key, value)
            await checkpoint_agent_context()

        await tb.execute_task_batches_for_trigger(
            workflow_name="AppGenerator", trigger_agent=agent_name,
            batches_config=config, agents=agents, context_variables=context,
            chat_id="repair-resume", app_id="repair-app", fresh_agents_per_task=False,
            checkpoint=checkpoint, parent_channel_id=envelope.channel_id,
            context_authority_policy=policy,
        )
        with _workflow_tool_invocation(bridge, writer_id=TASK_BATCH_WRITER):
            for key in batch_keys & context.keys():
                bridge.set(key, context[key])
        snapshots.append(deepcopy(context))

    def request(message, *, reopen=False):
        return AG2NetworkRunnerRequest(
            workflow_name="AppGenerator", chat_id="repair-resume", app_id="repair-app",
            agents=agents, initial_agent_name="AppPlanAgent", initial_message=message,
            transition_rules=rules, context_variables=initial_context,
            agent_output_handler=output_hook, knowledge_store=store,
            context_authority_policy=policy,
            resume_existing_only=reopen, idle_timeout_seconds=3.0,
        )

    result = await AG2NetworkRunner().run(request("Build the app"))
    try:
        if pause_before_artifact_repairs:
            assert result.status is RunStatus.PAUSED, result.error
        else:
            # Both proposals are spent and repair is blocked: the run ends there.
            assert result.status is RunStatus.FAILED
            assert result.close_reason == "workflow_failed"
            assert result.live_run is None
        assert triggers[:2] == ["AppPlanAgent", "AppValidationAgent"]
        initial_evidence = snapshots[0]["app_task_batch_results"]
        assert initial_evidence["_failed"]["services"]["failure_kind"] == "output_rejected"
        assert initial_evidence["_failed"]["page"]["failure_kind"] == "dependency_blocked"
        assert snapshots[1]["app_task_recovery_status"] == "completed"
        assert task_calls == {"models": 1, "services": 2, "page": 1}
        proposals_before_resume = 0 if pause_before_artifact_repairs else 2
        assert len(artifact_repairs) == proposals_before_resume
        assert result.context_variables.get("bundle_repair_attempt_count", 0) == proposals_before_resume
        if not pause_before_artifact_repairs:
            assert result.context_variables["bundle_repair_status"] == "blocked"
        evidence = deepcopy(result.context_variables["app_task_batch_results"])
        assert evidence["models"] == initial_evidence["models"]
        assert evidence["_meta"]["recovery_attempted_tasks"] == ["services"]

        if continuation == "uninterrupted":
            return

        channel = result.channel_id
        if pause_before_artifact_repairs:
            before_triggers = len(triggers)
            if continuation == "reopen":
                await result.live_run.close()
                # The same store/channel owns the evidence; stale startup context cannot reset it.
                result = await AG2NetworkRunner().run(request("Retry the remaining repair", reopen=True))
            else:
                result = await result.live_run.continue_with_user_message("Retry the remaining repair")
            # The retry spends the remaining proposals, then blocked repair ends the run.
            assert result.status is RunStatus.FAILED, result.error
            assert result.close_reason == "workflow_failed"
            assert result.channel_id == channel
            assert triggers[before_triggers:] == ["AppValidationAgent"] * 3
        ended_context = result.context_variables
        assert ended_context["app_task_batch_results"] == evidence
        assert ended_context["app_task_recovery_request"] is None
        assert ended_context["app_task_recovery_status"] == "idle"
        assert ended_context["bundle_repair_attempt_count"] == 2
        assert ended_context["bundle_repair_status"] == "blocked"

        # A retry after the end neither reopens accepted tasks nor replenishes a budget.
        before_triggers = len(triggers)
        result = await AG2NetworkRunner().run(request("Try once more", reopen=True))
        assert result.status is RunStatus.FAILED
        assert result.error == CHANNEL_TERMINAL_ERROR
        assert result.close_reason == "workflow_failed"
        assert triggers[before_triggers:] == []
        assert agent_calls["AppPlanAgent"] == 1
        assert task_calls == {"models": 1, "services": 2, "page": 1}
        assert len(artifact_repairs) == 2
    finally:
        if result.live_run is not None:
            await result.live_run.close()
