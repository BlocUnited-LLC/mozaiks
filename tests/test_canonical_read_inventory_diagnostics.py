"""Read identity repair reports the complete mismatch without changing access."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from factory_app.workflows.AppGenerator.tools.app_plan_review import (
    _repair_contract_task_operations,
)
from factory_app.workflows.AppGenerator.tools.hook_file_contract_context import (
    _build_file_contracts_body,
)
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.generator_support.code_files import extract_code_file_map_from_payload
from mozaiksai.core.workflow.generator_support.module_action_inventory import all_module_actions
from mozaiksai.core.workflow.generator_support.module_entitlement_gates import (
    compile_module_entitlement_gates,
)
from mozaiksai.core.workflow.generator_support.module_read_actions import close_module_read_actions
from tests.test_appgenerator_module_read_actions import MODULE, _contract, _output, _plan


def _context():
    return {
        "app_build_plan": _plan(), "data_contract": _contract("app_wide"),
        "design_surface_map": {"surfaces": [{
            "surface_id": MODULE, "surface_kind": "module", "owner": "app", "primary_entities": ["Task"],
            "owned_mutations": ["create_task", "update_task", "delete_task"], "custom_reads": [],
        }]},
    }


def _candidate():
    output = _output()
    actions = output["module_contract"]["module_yaml"]["actions"]
    actions[0]["permissions"] = ["tasks.write"]
    actions.append({"id": "get_task", "handler_method": "get_task", "permissions": [], "api_surface": None,
                    "input_schema": {"type": "object", "properties": []}, "output_schema": {"type": "object", "properties": []}})
    return output


def _close(output, context):
    return close_module_read_actions(output, **context)


def test_first_rejection_names_every_missing_read_and_unapproved_spelling_without_mutation():
    context = _context()
    output = _candidate()
    before = deepcopy(output)

    with pytest.raises(ValueError) as rejected:
        _close(output, context)

    error = str(rejected.value)
    assert "explicitly declare 'get_tasks'" in error
    assert "explicitly declare 'list_tasks'" in error
    assert "unapproved module actions ['get_task']" in error
    assert "Remove unapproved declarations" in error
    assert "permissions" in error and "api_surface" in error
    assert output == before


def test_missing_read_diagnostics_cover_all_collections_in_stable_order():
    context = _context()
    collection = deepcopy(context["data_contract"]["surfaces"][0]["collections"][0])
    collection.update(name="archives", entity="Archive")
    context["data_contract"]["surfaces"][0]["collections"].append(collection)
    context["app_build_plan"]["capability_packs"][0]["primary_entities"].append("Archive")
    context["design_surface_map"]["surfaces"][0]["primary_entities"].append("Archive")
    with pytest.raises(ValueError) as rejected:
        _close(_candidate(), context)
    error = str(rejected.value)
    names = ["get_archives", "list_archives", "get_tasks", "list_tasks"]
    positions = [error.index(f"explicitly declare '{name}'") for name in names]
    assert positions == sorted(positions)
    assert "unapproved module actions ['get_task']" in error


def test_repair_retaining_unapproved_spelling_stays_rejected_by_shared_inventory():
    context = _context()
    output = _candidate()
    actions = output["module_contract"]["module_yaml"]["actions"]
    for action_id in ["get_tasks", "list_tasks"]:
        actions.append({**deepcopy(actions[-1]), "id": action_id, "handler_method": action_id,
                        "permissions": [], "api_surface": "internal"})
    before = deepcopy(output)
    closed = _close(output, context)
    assert closed == before
    files = extract_code_file_map_from_payload(closed)
    files_before = dict(files)
    with pytest.raises(ValueError, match="unapproved module actions.*get_task"):
        compile_module_entitlement_gates(files, gates_by_module=None, approved_actions=all_module_actions(context),
                                         ungated_actions={MODULE: ["get_tasks", "list_tasks"]})
    assert output == before and files == files_before


@pytest.mark.parametrize("agent_name", ["AppPlanAgent", "ConfigMiddlewareAgent"])
def test_actual_prompt_hook_projects_exact_ids_and_preserves_access_decision(agent_name):
    context = _context()
    context["current_build_task"] = {"capability_pack_id": MODULE, "task_type": "module_contract"}
    before = deepcopy(context)
    agent = SimpleNamespace(name=agent_name, context_variables=ContextVariablesBridge(context))

    text = _build_file_contracts_body(agent, {})

    assert f"Approved action ids for {MODULE}" in text
    assert "  - get_tasks\n" in text and "  - list_tasks\n" in text
    assert "  - get_task\n" not in text
    assert "code does not infer read access" in text
    assert "this inventory overrides contradictory task prose" in text
    assert context == before


def test_contract_task_gets_inventory_even_when_old_operations_were_already_named():
    context = _context()
    plan = deepcopy(context["app_build_plan"])
    plan["capability_packs"][0].update(surface_kind="module", operations=["create_task", "update_task", "delete_task"])
    message = "create_task, update_task, delete_task. Canonical list/get are code-constructed."
    task = {"task_id": "task_contract", "task_type": "module_contract", "capability_pack_id": MODULE, "initial_message": message}
    plan["build_tasks"] = [task]

    assert _repair_contract_task_operations(plan, ContextVariablesBridge(context))
    assert task["initial_message"].startswith(message + "\n\n")
    assert "`get_tasks`" in task["initial_message"] and "`list_tasks`" in task["initial_message"]
    assert "app_wide collections with protected writes, explicitly declare" in task["initial_message"]
    assert "Do not add alternate spellings" in task["initial_message"]
    assert _repair_contract_task_operations(plan, ContextVariablesBridge(context)) == []


def test_module_contract_prompt_excludes_other_modules_inventory():
    context = _context()
    context["current_build_task"] = {"capability_pack_id": MODULE, "task_type": "module_contract"}
    context["design_surface_map"]["surfaces"].append({
        "surface_id": "other", "surface_kind": "module", "owner": "app", "primary_entities": [],
        "owned_mutations": [], "custom_reads": ["foreign_read"],
    })
    agent = SimpleNamespace(name="ConfigMiddlewareAgent", context_variables=ContextVariablesBridge(context))
    text = _build_file_contracts_body(agent, {})
    assert "  - get_tasks\n" in text
    assert "foreign_read" not in text
    assert "Approved action ids for other" not in text
