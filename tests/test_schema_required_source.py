"""Requiredness crosses structured output and task execution with one authority."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from ag2 import Agent
from pydantic import ValidationError

from factory_app.workflows.AppGenerator.tools.code_file_utils import save_generated_code
from mozaiksai.core.adapters.ag2_task_batch_runner import AG2TaskBatchRunnerResult
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.structured_output_overlay import StructuredOutputOverlay
from mozaiksai.core.workflow.generator_support.code_files import _materialize_schema_contract
from mozaiksai.core.workflow.outputs.runtime_validation import validate_agent_structured_output
from mozaiksai.core.workflow.outputs.structured import build_models_from_config
from mozaiksai.core.workflow.task_batches import (
    execute_task_batches_for_trigger,
    load_task_batches_config,
)

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / "factory_app/workflows"


@pytest.fixture(scope="module")
def models():
    config = yaml.safe_load((WORKFLOWS / "AppGenerator/structured_outputs.yaml").read_text(encoding="utf-8"))["models"]
    return build_models_from_config(config, exact_model_ids=frozenset(config))


def _schema():
    return {
        "type": "object", "description": None, "items_type": None,
        "properties": [
            {"name": "record_id", "type": "string", "description": None,
             "required": True, "enum_values": [], "items_type": None},
            {"name": "note", "type": "string", "description": None,
             "required": False, "enum_values": [], "items_type": None},
        ],
    }


def _output():
    return {
        "mode": "module_contract_bundle", "agent_message": "Declared records.",
        "service_foundation_bundle": None, "code_files": [],
        "module_contract": {
            "module_id": "records",
            "module_yaml": {
                "schema_version": "mozaiks.module.v1",
                "module": {
                    "id": "records", "display_name": "Records", "version": "1.0.0",
                    "type": "standard", "description": "Record lookup.", "owner": "app",
                    "visibility": "internal", "handler": "backend.handler:RecordsModule",
                },
                "permissions": [], "capabilities": [],
                "actions": [{
                    "id": "get_record", "description": "Get a record.", "handler_method": "get_record",
                    "input_schema": _schema(), "output_schema": _schema(),
                    "permissions": [], "emits": [],
                }],
            },
            **{name: None for name in (
                "events_yaml", "reactions_yaml", "notifications_yaml", "settings_yaml", "admin_yaml",
                "profile_yaml", "relationships_yaml", "policy_hooks_yaml", "runtime_extensions_yaml",
            )},
            "python_stubs": [], "js_stubs": [],
        },
    }


@pytest.mark.parametrize("retired_required", [[], ["record_id"], ["note"]])
def test_structured_schema_rejects_retired_required_list(models, retired_required):
    with pytest.raises(ValidationError, match="required.*|Extra inputs"):
        models["JsonSchemaContract"].model_validate({**_schema(), "required": retired_required})


@pytest.mark.parametrize("retired_required", [[], ["record_id"], ["note"]])
def test_materializer_rejects_retired_required_list(retired_required):
    with pytest.raises(ValueError, match="required"):
        _materialize_schema_contract({**_schema(), "required": retired_required})


def test_structured_contract_has_no_second_requiredness_field(models):
    validated = models["JsonSchemaContract"].model_validate(_schema()).model_dump(mode="json")
    assert "required" not in validated
    assert "required" not in models["JsonSchemaContract"].model_json_schema()["properties"]
    assert _materialize_schema_contract(validated)["required"] == ["record_id"]


async def _execute_module_task(monkeypatch, models, output):
    module_id = output["module_contract"]["module_id"]
    task_id = f"{module_id}.module_contract"
    task = {
        "task_id": task_id, "task_type": "module_contract", "capability_pack_id": module_id,
        "initial_agent": "ConfigMiddlewareAgent", "initial_message": "Declare the module contract.",
        "owned_paths": [f"modules/{module_id}/module.yaml"], "depends_on": [],
    }
    bridge = ContextVariablesBridge({"app_task_batch_items": [task]})
    context = bridge.snapshot()
    attempted = []

    async def run(_runner, request):
        attempted.append(request.task_id)
        validated = validate_agent_structured_output(
            agent_name=request.agent_name, reply=deepcopy(output),
            structured_registry={"ConfigMiddlewareAgent": models["ConfigMiddlewareOutput"]},
        )
        assert validated is not None and validated.validation_passed, validated
        worker_bridge = ContextVariablesBridge(request.context_variables)
        saved = save_generated_code(StructuredOutputOverlay(worker_bridge, validated.structured_data))
        assert f"modules/{module_id}/module.yaml" in saved["saved_files"]
        assert worker_bridge.snapshot()["code_files"]
        assert "structured_output" not in worker_bridge.snapshot()
        return AG2TaskBatchRunnerResult(status=RunStatus.COMPLETED, output=validated.structured_data)

    monkeypatch.setattr("mozaiksai.core.workflow.task_batches.AG2TaskBatchRunner.run", run)
    config = load_task_batches_config("AppGenerator", workflows_root=WORKFLOWS)
    assert config is not None

    async def checkpoint(updates):
        for key, value in updates.items():
            bridge.set(key, value)

    await execute_task_batches_for_trigger(
        workflow_name="AppGenerator", trigger_agent="AppPlanAgent", batches_config=config,
        agents={"ConfigMiddlewareAgent": Agent("ConfigMiddlewareAgent", prompt="Test contract worker.")},
        context_variables=context, chat_id="schema-required-test", app_id="schema-required-test",
        user_id="schema-required-test", fresh_agents_per_task=False,
        checkpoint=checkpoint, parent_channel_id="schema-required-parent-channel",
    )
    snapshot = context
    assert snapshot["app_task_batch_status"] == "completed", snapshot["app_task_batch_results"]
    assert attempted == [task_id]
    results = snapshot["app_task_batch_results"]
    assert not results.get("_failed")
    files = {item["filename"]: item["content"] for item in results[task_id]["code_files"]}
    return yaml.safe_load(files[f"modules/{module_id}/module.yaml"])


@pytest.mark.asyncio
async def test_flags_only_contract_completes_with_real_context_bridge(monkeypatch, models):
    module = await _execute_module_task(monkeypatch, models, _output())
    action = module["actions"][0]
    assert action["input_schema"]["required"] == ["record_id"]
    assert action["output_schema"]["required"] == ["record_id"]
    assert "note" in action["input_schema"]["properties"]
    assert action["input_schema"]["additionalProperties"] is False


@pytest.fixture
def live_billing_action():
    fixture = ROOT / "tests/fixtures/billing_portal_required_mismatch.json"
    return json.loads(fixture.read_text(encoding="utf-8"))["action"]


def test_live_billing_contradiction_is_rejected_at_structured_boundary(models, live_billing_action):
    schema = live_billing_action["output_schema"]
    assert schema["required"] == ["usage_data"]
    assert schema["properties"][0]["required"] is False
    with pytest.raises(ValidationError, match="required.*|Extra inputs"):
        models["ModuleAction"].model_validate(live_billing_action)


@pytest.mark.asyncio
async def test_live_billing_action_with_only_flags_completes_task(monkeypatch, models, live_billing_action):
    # Replay only the captured action: the full rejected worker output also
    # emitted an unrelated raw admin.yaml while its typed admin field was null.
    output = _output()
    bundle = output["module_contract"]
    bundle["module_id"] = "billing_portal"
    bundle["module_yaml"]["module"].update(id="billing_portal", display_name="Billing Portal")
    bundle["module_yaml"]["actions"] = [live_billing_action]
    # Gate decisions now enter through the approved subscription context.
    live_billing_action.pop("entitlement_gate", None)
    for key in ("input_schema", "output_schema"):
        del live_billing_action[key]["required"]
    module = await _execute_module_task(monkeypatch, models, output)
    action = module["actions"][0]
    assert action["id"] == "get_usage_status"
    assert action["input_schema"]["required"] == ["user_id"]
    assert "required" not in action["output_schema"]
    assert action["output_schema"]["properties"]["usage_data"]["type"] == "object"
