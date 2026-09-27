"""Generated ownership policies derive solely from approved collection tenancy."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from factory_app.workflows.AppGenerator.tools.assembly_phase import _merge_code_files
from factory_app.workflows.AppGenerator.tools.code_file_utils import save_generated_code
from factory_app.workflows.AppGenerator.tools.hook_module_runtime_quality_gate import (
    run_module_runtime_quality_gate,
)
from factory_app.workflows.AppGenerator.tools.module_persistence_guard import (
    scan_module_persistence,
)
from factory_app.workflows.AppGenerator.tools.module_runtime_quality import (
    audit_module_runtime_quality,
)
from mozaiksai.core.runtime.composition.module_context import ModuleContext
from mozaiksai.core.runtime.persistence.adapter import PersistencePrincipal
from mozaiksai.core.runtime.persistence.intent_loader import load_data_contract
from mozaiksai.core.runtime.persistence.mongo import MongoPersistenceCollection
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.structured_output_overlay import StructuredOutputOverlay
from mozaiksai.core.workflow.generator_support.module_policy import (
    materialize_module_policies,
    render_module_policy,
)

POLICY_PATH = "modules/task_management/backend/policy.py"


def _collection(tenancy="per_user", field="owner_id", name="tasks"):
    return {
        "name": name, "scope": "app", "tenancy": tenancy,
        "owner_field": None if tenancy == "app_wide" else field,
        "entity": "Task" if name == "tasks" else "TaskGroup",
        "ownership": {"surface_id": "task_management", "surface_kind": "module"},
        "fields": [{"name": field, "type": "string", "required": True}], "indexes": [],
    }


def _contract(*collections):
    return {"version": "1", "surfaces": [{
        "surface_id": "task_management", "surface_kind": "module", "collections": list(collections) or [_collection()],
    }]}


def _policy(*collections):
    namespace = {}
    exec(render_module_policy("task_management", list(collections) or [_collection()]), namespace)
    return SimpleNamespace(**namespace)


def _context(app_id="app-1", user_id="user-1", workspace_id="workspace-1"):
    context = ModuleContext(app_id=app_id, user_id=user_id, workspace_id=workspace_id)
    context.persistence = SimpleNamespace(
        app_id=app_id, principal=PersistencePrincipal(user_id=user_id, workspace_id=workspace_id),
    )
    return context


@pytest.mark.parametrize("collections", [
    [_collection()],
    [_collection("per_workspace", "space_id")],
    [_collection("app_wide")],
    [_collection(), _collection("per_workspace", "space_id", "task_groups")],
])
def test_rendered_policy_satisfies_generated_persistence_admission(collections):
    source = render_module_policy("task_management", collections)
    assert scan_module_persistence({POLICY_PATH: source}) == []


@pytest.mark.parametrize(("tenancy", "attribute"), [("per_user", "user_id"), ("per_workspace", "workspace_id")])
def test_policy_preflight_uses_immutable_persistence_principal(tenancy, attribute):
    policy = _policy(_collection(tenancy, "record_owner"))
    context = _context()
    payload = {"record_owner": "foreign", "status": "open"}
    expected = {"record_owner": getattr(context.persistence.principal, attribute), "status": "open"}
    setattr(context, attribute, "request-controlled")
    context.app_id = "request-app"
    assert policy.scoped_query(context, payload) == expected
    assert policy.scope_record(context, {"status": "open"}) == {**expected, "app_id": "app-1"}
    with pytest.raises(PermissionError, match="disagrees with authenticated principal"):
        policy.scope_record(context, payload)
    assert payload == {"record_owner": "foreign", "status": "open"}
    context.persistence.principal = PersistencePrincipal(
        user_id=None if attribute == "user_id" else "user-1", workspace_id=None,
    )
    with pytest.raises(PermissionError, match=f"Missing required scope identity: {attribute}"):
        policy.scoped_query(context, payload)
    with pytest.raises(PermissionError):
        policy.scope_record(context, payload)


def test_workspace_policy_also_requires_login():
    policy = _policy(_collection("per_workspace", "space_id"))
    with pytest.raises(PermissionError, match="user_id"):
        policy.scoped_query(_context(app_id="app", user_id=None, workspace_id="workspace"))


def test_policy_rejects_untrusted_context_identity_without_persistence_principal():
    context = _context()
    context.persistence.principal = None
    with pytest.raises(PermissionError, match="user_id"):
        _policy().scoped_query(context)


def test_app_wide_policy_relies_on_runtime_app_isolation_without_owner_field():
    policy = _policy(_collection("app_wide"))
    ctx = _context(app_id="app")
    assert policy.scoped_query(ctx, {"app_id": "foreign", "status": "open"}) == {"status": "open"}
    assert policy.scope_record(ctx, {"app_id": "foreign", "status": "open"}) == {"app_id": "app", "status": "open"}
    with pytest.raises(PermissionError, match="app_id"):
        policy.scoped_query(_context(app_id=None))


def test_namespace_scope_does_not_determine_row_tenancy():
    for scope in ("app", "platform", "hosted"):
        policy = _policy({**_collection(), "scope": scope})
        assert policy.scoped_query(_context(app_id="app", user_id="user")) == {"owner_id": "user"}


def test_policy_requires_explicit_collection_when_module_owns_multiple_tenancies():
    policy = _policy(_collection(), _collection("per_workspace", "space_id", "task_groups"))
    context = _context(workspace_id="space-1")
    assert policy.scoped_query(context, entity_name="tasks") == {"owner_id": "user-1"}
    assert policy.scoped_query(context, entity_name="task_groups") == {"space_id": "space-1"}
    with pytest.raises(ValueError, match="entity_name is required"):
        policy.scoped_query(context)
    with pytest.raises(ValueError, match="Undeclared policy collection"):
        policy.scope_record(context, {}, entity_name="foreign")


@pytest.mark.asyncio
@pytest.mark.parametrize(("tenancy", "field"), [
    ("app_wide", "owner_id"), ("per_user", "owner_id"), ("per_user", "user_id"),
    ("per_workspace", "space_id"), ("per_workspace", "workspace_id"),
])
async def test_policy_composes_with_real_mongo_persistence_app_scope(tenancy, field):
    policy = _policy(_collection(tenancy, field))
    context = _context()
    raw = AsyncMock()
    collection = MongoPersistenceCollection(
        collection=raw, app_id=context.app_id, user_id=context.user_id, workspace_id=context.workspace_id,
    )
    query = policy.scoped_query(context, {"status": "open", "app_id": "foreign"})
    assert "app_id" not in query
    await collection.find_one(query)
    expected = {"app_id": "app-1", "status": "open"}
    if tenancy != "app_wide":
        expected[field] = context.user_id if tenancy == "per_user" else context.workspace_id
    raw.find_one.assert_awaited_once_with(expected, None)
    record = policy.scope_record(context, {"status": "open", "app_id": "foreign"})
    await collection.insert_one(record)
    stored = raw.insert_one.call_args.args[0]
    assert stored["app_id"] == "app-1"
    if tenancy != "app_wide":
        assert stored[field] == expected[field]


@pytest.mark.parametrize(("tenancy", "field"), [
    ("per_user", "app_id"), ("per_user", "workspace_id"), ("per_workspace", "user_id"),
])
def test_owner_field_cannot_conflict_with_runtime_identity_metadata(tenancy, field):
    with pytest.raises(ValueError, match="owner_field.*conflicts with"):
        render_module_policy("task_management", [_collection(tenancy, field)])


@pytest.mark.parametrize(("changes", "error"), [
    ({"tenancy": "organization"}, "tenancy must be"),
    ({"owner_field": None}, "owner_field must be"),
    ({"owner_field": "owner.id"}, "owner_field must be"),
    ({"owner_field": "invented_id"}, "must be one of its declared fields"),
    ({"tenancy": "app_wide"}, "owner_field must be null"),
    ({"ownership": {"surface_id": "foreign", "surface_kind": "module"}}, "ownership must match"),
])
def test_policy_rejects_ambiguous_tenancy_instead_of_guessing(changes, error):
    with pytest.raises(ValueError, match=error):
        render_module_policy("task_management", [{**_collection(), **changes}])


def test_live_policy_failure_is_replaced_before_frozen_context_quality_hook():
    unfinished = "def scoped_query(context, filters=None):\n    # implement logic\n    return filters or {}\n"
    original_files = [{"filename": POLICY_PATH, "content": unfinished}]
    assert "unfinished runtime logic ('implement logic')" in audit_module_runtime_quality(original_files)[0]
    bridge = ContextVariablesBridge({
        "data_contract": _contract(), "current_build_task": {"owned_paths": [POLICY_PATH]}, "code_files": original_files,
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
        "data_contract": _contract({**_collection(), "owner_field": None}),
        "current_build_task": {"owned_paths": [POLICY_PATH]},
        "code_files": [{"filename": POLICY_PATH, "content": "existing"}],
    })
    before = bridge.snapshot()
    with pytest.raises(ValueError, match="owner_field"):
        save_generated_code(StructuredOutputOverlay(bridge, {"python_files": [], "code_files": []}))
    assert bridge.snapshot() == before


def test_model_authored_policy_is_rejected_without_weakening_quality_gate():
    contract = _contract()
    files = {POLICY_PATH: "# implement logic\n", "data/contract.json": json.dumps(contract)}
    with pytest.raises(ValueError, match="omit model-authored policy source"):
        _merge_code_files(
            [{"code_files": [{"filename": path, "content": value} for path, value in files.items()]}],
            data_contract=contract,
        )
    bridge = ContextVariablesBridge({"data_contract": contract, "code_files": []})
    with pytest.raises(ValueError, match="omit model-authored policy source"):
        save_generated_code(StructuredOutputOverlay(bridge, {"code_files": [{"filename": POLICY_PATH, "content": files[POLICY_PATH]}]}))
    assert bridge.snapshot()["code_files"] == []


def test_assembly_renders_policy_from_approved_contract_and_runtime_loads_it(tmp_path):
    contract = _contract()
    assembled = _merge_code_files([
        {"code_files": [{"filename": "data/contract.json", "content": json.dumps(contract)}]},
        {"python_files": [{"path": "modules/task_management/backend/service.py", "content": "from .policy import scoped_query\n"}]},
    ], data_contract=contract)
    files = {item["filename"]: item["content"] for item in assembled}
    assert files[POLICY_PATH] == render_module_policy("task_management", [_collection()])
    assert scan_module_persistence(files) == []
    assert audit_module_runtime_quality(assembled) == []
    (tmp_path / "data").mkdir()
    (tmp_path / "data/contract.json").write_text(files["data/contract.json"], encoding="utf-8")
    assert load_data_contract(tmp_path) == contract


def test_serialized_contract_cannot_override_approved_context():
    with pytest.raises(ValueError, match="must match the approved data_contract"):
        materialize_module_policies({
            "data/contract.json": json.dumps(_contract(_collection("per_workspace", "workspace_owner"))),
        }, _contract(), module_ids={"task_management"})


def test_policy_rendering_ignores_facade_module_without_collection_surface():
    assert materialize_module_policies({}, {"surfaces": []}, module_ids={"billing_portal"}) == {}
    with pytest.raises(ValueError, match="no data_contract surface"):
        materialize_module_policies({POLICY_PATH: "source"}, {"surfaces": []})


@pytest.mark.parametrize("files", [{}, {POLICY_PATH: "source"}])
def test_policy_rendering_requires_approved_data_contract(files):
    with pytest.raises(ValueError, match="require data_contract"):
        materialize_module_policies(files, module_ids={"task_management"})
