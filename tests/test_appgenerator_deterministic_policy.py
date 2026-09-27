"""Generated ownership policies close scope from contracts before quality checks."""

from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from factory_app.workflows.AppGenerator.tools.assembly_phase import _merge_code_files
from factory_app.workflows.AppGenerator.tools.code_file_utils import save_generated_code
from factory_app.workflows.AppGenerator.tools.hook_module_runtime_quality_gate import (
    run_module_runtime_quality_gate,
)
from factory_app.workflows.AppGenerator.tools.module_runtime_quality import (
    audit_module_runtime_quality,
)
from mozaiksai.core.runtime.composition.module_context import ModuleContext
from mozaiksai.core.runtime.persistence.intent_loader import load_data_contract
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.structured_output_overlay import StructuredOutputOverlay
from mozaiksai.core.workflow.generator_support.module_policy import (
    materialize_module_policies,
    render_module_policy,
)

POLICY_PATH = "modules/task_management/backend/policy.py"


def _collection(scope="user", field="owner_id", name="tasks"):
    return {
        "name": name,
        "scope": scope,
        "scope_field": field,
        "ownership": {"surface_id": "task_management", "surface_kind": "module"},
        "fields": [{"name": field, "type": "string", "required": True}],
        "indexes": [],
    }


def _contract(*collections):
    return {"version": "1", "surfaces": [{
        "surface_id": "task_management", "surface_kind": "module",
        "collections": list(collections) or [_collection()],
    }]}


def _policy(*collections):
    namespace = {}
    exec(render_module_policy("task_management", list(collections) or [_collection()]), namespace)
    return SimpleNamespace(**namespace)


@pytest.mark.parametrize(("scope", "attribute"), [
    ("app", "app_id"), ("user", "user_id"), ("tenant", "tenant_id"), ("workspace", "workspace_id"),
])
def test_policy_uses_declared_ownership_field_and_trusted_context(scope, attribute):
    policy = _policy(_collection(scope, "record_owner"))
    context = ModuleContext(app_id="app-1", user_id="user-1", tenant_id="tenant-1", workspace_id="workspace-1")
    payload = {"record_owner": "foreign", "status": "open"}
    expected = {"record_owner": getattr(context, attribute), "status": "open"}

    assert policy.scoped_query(context, payload) == expected
    assert policy.scope_record(context, payload) == expected
    assert payload == {"record_owner": "foreign", "status": "open"}
    setattr(context, attribute, None)
    with pytest.raises(PermissionError, match=f"Missing required scope identity: {attribute}"):
        policy.scoped_query(context, payload)
    with pytest.raises(PermissionError):
        policy.scope_record(context, payload)


def test_policy_requires_explicit_collection_when_module_owns_multiple_scopes():
    policy = _policy(_collection(), _collection("workspace", "space_id", "task_groups"))
    context = ModuleContext(app_id="app-1", user_id="user-1", workspace_id="space-1")
    assert policy.scoped_query(context, entity_name="tasks") == {"owner_id": "user-1"}
    assert policy.scoped_query(context, entity_name="task_groups") == {"space_id": "space-1"}
    with pytest.raises(ValueError, match="entity_name is required"):
        policy.scoped_query(context)
    with pytest.raises(ValueError, match="Undeclared policy collection"):
        policy.scope_record(context, {}, entity_name="foreign")


@pytest.mark.asyncio
@pytest.mark.parametrize(("scope", "field"), [
    ("app", "app_id"), ("app", "account_id"), ("user", "user_id"),
    ("tenant", "tenant_id"), ("workspace", "workspace_id"),
])
async def test_generated_policy_composes_with_real_mongo_persistence_scope(scope, field):
    from unittest.mock import AsyncMock

    from mozaiksai.core.runtime.persistence.mongo import MongoPersistenceCollection

    policy = _policy(_collection(scope, field))
    context = ModuleContext(app_id="app-1", user_id="user-1", tenant_id="tenant-1", workspace_id="workspace-1")
    raw = AsyncMock()
    collection = MongoPersistenceCollection(
        collection=raw, app_id=context.app_id, user_id=context.user_id,
        tenant_id=context.tenant_id, workspace_id=context.workspace_id,
    )
    query = policy.scoped_query(context, {"status": "open", field: "foreign"})
    # App scope is injected by PersistenceCollection; passing app_id as an extra
    # filter is forbidden even when its value matches the current app.
    assert "app_id" not in query
    await collection.find_one(query)
    expected = {"app_id": context.app_id, "status": "open", field: getattr(context, f"{scope}_id")}
    raw.find_one.assert_awaited_once_with(expected, None)
    record = policy.scope_record(context, {"status": "open", field: "foreign"})
    await collection.insert_one(record)
    stored = raw.insert_one.call_args.args[0]
    assert stored[field] == getattr(context, f"{scope}_id")
    assert stored["app_id"] == context.app_id


@pytest.mark.parametrize(("scope", "field"), [("user", "app_id"), ("tenant", "user_id"), ("app", "workspace_id")])
def test_scope_cannot_conflict_with_runtime_owned_identity_metadata(scope, field):
    with pytest.raises(ValueError, match="reserved scope_field"):
        render_module_policy("task_management", [_collection(scope, field)])


@pytest.mark.parametrize(("changes", "error"), [
    ({"scope": "organization"}, "scope must be"),
    ({"scope_field": None}, "scope_field must be"),
    ({"scope_field": "owner.id"}, "scope_field must be"),
    ({"scope_field": "invented_id"}, "must be declared in fields"),
    ({"ownership": {"surface_id": "foreign", "surface_kind": "module"}}, "ownership must match"),
])
def test_policy_rejects_unresolved_scope_instead_of_guessing(changes, error):
    with pytest.raises(ValueError, match=error):
        render_module_policy("task_management", [{**_collection(), **changes}])


def test_live_policy_failure_is_replaced_before_frozen_context_quality_hook():
    unfinished = "def scoped_query(context, filters=None):\n    # implement logic\n    return filters or {}\n"
    original_files = [{"filename": POLICY_PATH, "content": unfinished}]
    assert "unfinished runtime logic ('implement logic')" in audit_module_runtime_quality(original_files)[0]
    bridge = ContextVariablesBridge({
        "data_contract": _contract(),
        "current_build_task": {"owned_paths": [POLICY_PATH]},
        "code_files": original_files,
    })

    saved = save_generated_code(StructuredOutputOverlay(bridge, {"python_files": [], "code_files": []}))
    assert saved["saved_files"] == [POLICY_PATH]
    files = bridge.snapshot()["code_files"]
    assert files[0]["content"] == render_module_policy("task_management", [_collection()])
    agent = SimpleNamespace(name="ModuleRuntimeQualityAgent", context_variables=bridge, system_message="Quality gate")
    agent.update_system_message = lambda message: setattr(agent, "system_message", message)
    run_module_runtime_quality_gate(agent, [])
    assert bridge.get("module_runtime_quality_status") == "passed"
    assert bridge.get("module_runtime_quality_warnings") == ()


def test_policy_materialization_failure_does_not_mutate_context():
    bridge = ContextVariablesBridge({
        "data_contract": _contract({**_collection(), "scope_field": None}),
        "current_build_task": {"owned_paths": [POLICY_PATH]},
        "code_files": [{"filename": POLICY_PATH, "content": "existing"}],
    })
    before = bridge.snapshot()
    with pytest.raises(ValueError, match="scope_field"):
        save_generated_code(StructuredOutputOverlay(bridge, {"python_files": [], "code_files": []}))
    assert bridge.snapshot() == before


def test_model_authored_policy_is_rejected_without_weakening_the_quality_gate():
    contract = _contract()
    files = {POLICY_PATH: "# implement logic\n", "data/contract.json": json.dumps(contract)}
    with pytest.raises(ValueError, match="omit model-authored policy source"):
        _merge_code_files([{"code_files": [{"filename": path, "content": value} for path, value in files.items()]}])
    bridge = ContextVariablesBridge({"data_contract": contract, "code_files": []})
    with pytest.raises(ValueError, match="omit model-authored policy source"):
        save_generated_code(StructuredOutputOverlay(bridge, {"code_files": [{"filename": POLICY_PATH, "content": files[POLICY_PATH]}]}))
    assert bridge.snapshot()["code_files"] == []


def test_assembly_materializes_policy_without_model_source_and_runtime_loads_contract(tmp_path):
    contract = _contract()
    assembled = _merge_code_files([
        {"code_files": [{"filename": "data/contract.json", "content": json.dumps(contract)}]},
        {"python_files": [{"path": "modules/task_management/backend/service.py", "content": "from .policy import scoped_query\n"}]},
    ])
    files = {item["filename"]: item["content"] for item in assembled}
    assert files[POLICY_PATH] == render_module_policy("task_management", [_collection()])
    assert audit_module_runtime_quality(assembled) == []
    (tmp_path / "data").mkdir()
    (tmp_path / "data/contract.json").write_text(files["data/contract.json"], encoding="utf-8")
    assert load_data_contract(tmp_path) == contract


def test_bundle_contract_is_authoritative_over_stale_context_contract():
    result = materialize_module_policies({
        "data/contract.json": json.dumps(_contract(_collection("workspace", "workspace_owner"))),
    }, _contract(), module_ids={"task_management"})
    namespace = {}
    exec(result[POLICY_PATH], namespace)
    context = ModuleContext(app_id="app-1", user_id="user-1", workspace_id="space-1")
    assert namespace["scoped_query"](context) == {"workspace_owner": "space-1"}


def test_owned_policy_requires_an_explicit_persistence_surface():
    with pytest.raises(ValueError, match="require data_contract"):
        materialize_module_policies({}, module_ids={"task_management"})
    with pytest.raises(ValueError, match="no data_contract surface"):
        materialize_module_policies({}, {"surfaces": []}, module_ids={"task_management"})


@pytest.mark.parametrize("contract", [None, {"surfaces": []}])
def test_assembly_policy_cannot_bypass_its_missing_contract(contract):
    files = {POLICY_PATH: render_module_policy("task_management", [_collection()])}
    with pytest.raises(ValueError, match="data_contract"):
        materialize_module_policies(files, contract)


def test_scope_refinement_requires_the_policy_owner_before_assembly():
    from factory_app.workflows.AppGenerator.tools.code_file_utils import admitted_app_file_map

    original = _contract()
    revised = _contract(_collection("workspace", "workspace_owner"))
    prior_policy = render_module_policy("task_management", [_collection()])
    bridge = ContextVariablesBridge({
        "data_contract": revised,
        "generated_files": {"data/contract.json": json.dumps(original), POLICY_PATH: prior_policy},
        "current_build_task": {"owned_paths": ["data/contract.json"]},
    })
    saved = save_generated_code(StructuredOutputOverlay(bridge, {
        "code_files": [{"filename": "data/contract.json", "content": json.dumps(revised)}],
    }))
    assert saved["saved_files"] == ["data/contract.json"]
    assert admitted_app_file_map(bridge)[POLICY_PATH] == prior_policy
    with pytest.raises(ValueError, match="policy.py is rendered from data_contract"):
        materialize_module_policies(admitted_app_file_map(bridge))

    bridge.set("current_build_task", {"owned_paths": [POLICY_PATH]})
    saved = save_generated_code(StructuredOutputOverlay(bridge, {"python_files": [], "code_files": []}))
    assert saved["saved_files"] == [POLICY_PATH]
    current = admitted_app_file_map(bridge)
    assert current[POLICY_PATH] != prior_policy
    assert materialize_module_policies(current)[POLICY_PATH] == current[POLICY_PATH]


@pytest.mark.asyncio
async def test_exact_live_sections_close_read_policy_and_wiring_in_one_context(tmp_path, monkeypatch):
    from factory_app.workflows.AppGenerator.tools.save_app_schema import save_app_schema
    from factory_app.workflows.AppGenerator.tools.validate_wiring import validate_wiring
    from mozaiksai.core.workflow.agents.factory import _workflow_tool_invocation
    from tests.test_appgenerator_module_read_actions import _output, _plan
    from tests.test_appgenerator_page_data_sources import _bridge, _pages

    monkeypatch.setenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", str(tmp_path))
    plan, output, pages = _plan(), _output(), _pages()
    # The existing list action contract returns total, so the metric selects
    # that read instead of inventing a dashboard module or summary action.
    pages[0]["sections"][0]["config"] = {
        "data_source": {"module_id": "task_management", "action_id": "list_tasks"},
        "items": [{"label": "Total tasks", "value_key": "total"}],
    }
    untouched = deepcopy((plan, output, pages))
    context = _bridge({
        "app_build_plan": plan, "data_contract": plan["data_contract"],
        "current_build_task": {"owned_paths": ["modules/task_management/module.yaml", POLICY_PATH]},
    })
    with _workflow_tool_invocation(context):
        saved = save_generated_code(StructuredOutputOverlay(context, output))
        assert saved["saved_files"] == [POLICY_PATH, "modules/task_management/module.yaml"]
        module = next(item for item in context.get("code_files") if item["filename"].endswith("module.yaml"))
        import yaml

        assert [item["id"] for item in yaml.safe_load(module["content"])["actions"]] == [
            "create_task", "update_task", "delete_task", "list_tasks",
        ]
        context.set("current_build_task", {"owned_paths": ["app.json", "ui/pages/dashboard.yaml", "ui/pages/tasks.yaml"]})
        save_app_schema(
            manifest={"app_name": "Task tracker", "pages": ["dashboard", "tasks"], "default_route": "/dashboard"},
            pages=pages, context_variables=context,
        )
        agent = SimpleNamespace(name="ModuleRuntimeQualityAgent", context_variables=context, system_message="Quality gate")
        agent.update_system_message = lambda message: setattr(agent, "system_message", message)
        run_module_runtime_quality_gate(agent, [])
        wiring = await validate_wiring(context)
    assert wiring["passed"] is True, wiring["failed_tests"]
    assert {(item["page"], item["section"], item["endpoint"]) for item in wiring["wired"]} == {
        ("dashboard", "kpi-metrics", "/api/modules/task_management/list_tasks"),
        ("dashboard", "task-list-overview", "/api/modules/task_management/list_tasks"),
        ("tasks", "task-data-table", "/api/modules/task_management/list_tasks"),
    }
    assert context.get("module_runtime_quality_status") == "passed"
    assert context.get("module_runtime_quality_warnings") == ()
    assert (plan, output, pages) == untouched
    namespace = {}
    exec(context.get("generated_files")[POLICY_PATH], namespace)
    assert namespace["scoped_query"](ModuleContext(app_id="app", user_id="owner"), {"owner_id": "foreign"}) == {"owner_id": "owner"}
