"""Schema rejections name the field, its location, and the fix.

The 2026-11-10 live traversal (mozaiks#1005, run 5) lost two module_contract
tasks to "Nullable schema properties are supported only for non-null output
types; closed requests require false" and "Only object properties may declare
properties". Neither said which capability, event or field was wrong, so both
repair attempts failed. No log line recorded either rejection.
"""
from __future__ import annotations

import logging

import pytest

from mozaiksai.core.workflow import task_batches
from mozaiksai.core.workflow.generator_support.code_files import (
    _materialize_module_contract_file_map,
    _materialize_schema_contract,
)


def _object(*properties):
    return {"type": "object", "properties": list(properties)}


def test_nullable_request_field_names_its_path_and_the_fix():
    schema = _object({"name": "address", "type": "object", "properties": [
        {"name": "zip", "type": "string", "nullable": True},
    ]})
    with pytest.raises(ValueError) as error:
        _materialize_schema_contract(schema, closed_request=True)
    assert str(error.value) == (
        "property 'address.zip': request schemas (input_schema) cannot be nullable; set nullable "
        "to false and use required: false for a value the caller may omit"
    )
    # Output schemas may still be nullable.
    assert _materialize_schema_contract(schema)["properties"]["address"]["properties"]["zip"]["type"] == [
        "string", "null",
    ]


def test_properties_on_a_scalar_name_the_field_and_the_object_shape():
    schema = _object({"name": "email", "type": "string", "properties": [{"name": "domain", "type": "string"}]})
    with pytest.raises(ValueError, match=r"^property 'email': type 'string' cannot declare properties; "
                                         r"use type 'object' for nested fields"):
        _materialize_schema_contract(schema)


def test_null_type_and_non_boolean_nullable_stay_rejected_with_their_own_reasons():
    with pytest.raises(ValueError, match=r"^property 'gone': a 'null' type property cannot also be nullable"):
        _materialize_schema_contract(_object({"name": "gone", "type": "null", "nullable": True}))
    with pytest.raises(ValueError, match=r"^property 'flag': nullable must be a boolean$"):
        _materialize_schema_contract(_object({"name": "flag", "type": "string", "nullable": "yes"}))


@pytest.mark.parametrize("key,bundle,location", [
    ("module_yaml", {"actions": [], "capabilities": [{
        "capability_id": "tools.create", "input_schema": _object({"name": "note", "type": "string", "nullable": True}),
    }]}, "tools capability tools.create.input_schema: property 'note': request schemas"),
    ("events_yaml", {"events": [{
        "type": "domain.tools.created",
        "payload_schema": _object({"name": "tool_id", "type": "string", "properties": [{"name": "x", "type": "string"}]}),
    }]}, "tools event domain.tools.created.payload_schema: property 'tool_id': type 'string' cannot declare"),
])
def test_module_contract_rejections_name_the_module_and_item(key, bundle, location):
    with pytest.raises(ValueError) as error:
        _materialize_module_contract_file_map({"module_contract": {"module_id": "tools", key: bundle}})
    assert location in str(error.value)


@pytest.mark.asyncio
async def test_rejected_task_output_reaches_the_log(monkeypatch, caplog):
    from tests.test_draining_a_dependent_needs_another_pass import _spec

    batch = _spec()
    error = "Invalid module action schemas: tools capability tools.create.input_schema: property 'note'"

    async def rejected(*, task, **_):
        raise task_batches._TaskRejected(task_batches.TaskBatchFailure(
            task_id=str(task["task_id"]), failure_kind="output_rejected", error=error,
            worker_agent="ConfigMiddlewareAgent", attempts=2,
        ))

    monkeypatch.setattr(task_batches, "_run_one_task", rejected)
    caplog.set_level(logging.WARNING, logger=task_batches.logger.name)
    result = await task_batches._execute_one_batch(
        workflow_name="AppGenerator", batch=batch,
        task_items=[{"task_id": "module_contract_tools", "agent": "A", "message": "m", "depends_on": []}],
        agents={}, context_variables={}, chat_id="chat-1", app_id=None, user_id=None, wf_logger=None,
        fresh_agents_per_task=False, agents_factory=None, context_authority_policy=None,
    )
    assert result["failed_tasks"] == ["module_contract_tools"]
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("TASK_OUTPUT_REJECTED")]
    assert lines == [
        f"TASK_OUTPUT_REJECTED: batch={batch.id} task=module_contract_tools chat=chat-1 "
        f"worker=ConfigMiddlewareAgent attempts=2: {error}"
    ]
