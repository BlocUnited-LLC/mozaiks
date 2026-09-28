"""Canonical read contracts and implementations derive from approved ownership."""
from __future__ import annotations

import importlib
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.app_validation import (
    validate_module_implementation_contract,
)
from factory_app.workflows.AppGenerator.tools.assembly_phase import _merge_code_files
from factory_app.workflows.AppGenerator.tools.code_file_utils import save_generated_code
from factory_app.workflows.AppGenerator.tools.module_runtime_quality import (
    audit_module_runtime_quality,
)
from mozaiksai.core.adapters.ag2_task_batch_runner import AG2TaskBatchRunnerResult
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.runtime import ModuleRecordNotFoundError
from mozaiksai.core.runtime.composition.module_context import ModuleContext
from mozaiksai.core.workflow import task_batches
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.context.structured_output_overlay import StructuredOutputOverlay
from mozaiksai.core.workflow.generator_support.code_files import extract_code_file_map_from_payload
from mozaiksai.core.workflow.generator_support.module_action_inventory import (
    canonical_read_actions_for_surface,
)
from mozaiksai.core.workflow.generator_support.module_policy import materialize_module_policies
from mozaiksai.core.workflow.generator_support.module_read_actions import (
    close_module_read_actions,
    materialize_module_read_actions,
    materialize_module_read_implementations,
)
from mozaiksai.core.workflow.generator_support.module_write_actions import close_module_actions

MODULE = "task_management"
MANIFEST = f"modules/{MODULE}/module.yaml"
BACKEND = f"modules/{MODULE}/backend"


def _contract(tenancy="per_user"):
    field = "workspace_owner" if tenancy == "per_workspace" else "owner_id"
    return {"version": "1", "surfaces": [{
        "surface_id": MODULE, "surface_kind": "module", "collections": [{
            "name": "tasks", "scope": "app", "tenancy": tenancy,
            "owner_field": None if tenancy == "app_wide" else field, "entity": "Task",
            "ownership": {"surface_id": MODULE, "surface_kind": "module"},
            "fields": [{"name": name, "type": "string", "required": True} for name in ("id", "title", field)],
        }],
    }]}


def _plan():
    return {
        "capability_packs": [{
            "capability_pack_id": MODULE, "capability_source": "generated_module", "primary_entities": ["Task"],
        }],
        "pages": [],
    }


def _output():
    return {"module_contract": {
        "module_id": MODULE,
        "module_yaml": {
            "schema_version": "mozaiks.module.v1",
            "module": {"id": MODULE, "handler": "backend.handler:TaskManagementHandler"},
            "actions": [{"id": name, "handler_method": name} for name in (
                "create_task", "update_task", "delete_task",
            )],
        },
    }}


def _closed(output=None, contract=None, plan=None):
    context = ContextVariablesBridge({
        "structured_output": output or _output(), "app_build_plan": plan or _plan(),
        "data_contract": contract or _contract(),
    })
    return close_module_read_actions(
        context.get("structured_output"), app_build_plan=context.get("app_build_plan"),
        data_contract=context.get("data_contract"),
    )


def _files(tenancy="per_user"):
    output = _output()
    output["module_contract"]["module_yaml"]["actions"] = []
    contract = _contract(tenancy)
    files = extract_code_file_map_from_payload(_closed(output, contract))
    files.update(materialize_module_read_implementations(files, app_build_plan=_plan(), data_contract=contract))
    files.update(materialize_module_policies(files, contract))
    return files


@pytest.mark.parametrize("tenancy", ["per_user", "per_workspace"])
@pytest.mark.parametrize("restriction", [
    {"permissions": ["task_management.admin"]}, {"entitlement_gate": "task_management.reporting"},
    {"api_surface": "internal"}, {"api_surface": "admin_internal"},
])
def test_scoped_reads_construct_with_protected_siblings_without_role_inheritance(tenancy, restriction):
    output = _output()
    output["module_contract"]["module_yaml"]["actions"][0].update(restriction)
    before = deepcopy(output)
    closed = _closed(output, _contract(tenancy))
    reads = closed["module_contract"]["module_yaml"]["actions"][3:]
    assert [action["id"] for action in reads] == ["get_tasks", "list_tasks"]
    assert all(action["permissions"] == [] for action in reads)
    assert all(action["api_surface"] is None for action in reads)
    assert all(action["entitlement_gate"] is None for action in reads)
    assert output == before


def test_canonical_read_gate_is_preserved_and_contract_is_code_owned():
    output = _output()
    output["module_contract"]["module_yaml"]["actions"].append({
        "id": "list_tasks", "handler_method": "foreign", "permissions": ["task_management.admin"],
        "entitlement_gate": "task_management.reporting",
    })
    closed = _closed(output)
    read = next(action for action in closed["module_contract"]["module_yaml"]["actions"] if action["id"] == "list_tasks")
    assert read["entitlement_gate"] == "task_management.reporting"
    assert read["permissions"] == []
    assert read["handler_method"] == "list_tasks"
    assert _closed(closed) == closed


def test_app_wide_protected_writes_require_exact_explicit_read_access_contract():
    output = _output()
    output["module_contract"]["module_yaml"]["actions"][0]["permissions"] = ["tasks.write"]
    with pytest.raises(ValueError, match="explicitly declare 'get_tasks'.*permissions.*api_surface"):
        _closed(output, _contract("app_wide"))
    declared = _closed()["module_contract"]["module_yaml"]["actions"][3:]
    for action in declared:
        action["permissions"] = ["tasks.read"]
        action["api_surface"] = "internal"
    output["module_contract"]["module_yaml"]["actions"].extend(declared)
    assert _closed(output, _contract("app_wide")) == output


def test_app_wide_gate_only_write_requires_explicit_reads_before_gate_compilation():
    context = ContextVariablesBridge({
        "app_build_plan": _plan(), "data_contract": _contract("app_wide"),
        "subscription_contract": {"module_contract_updates": [{
            "module_id": MODULE, "action_id": "update_task", "entitlement_gate": "task.edit",
        }]},
    })
    with pytest.raises(ValueError, match="app_wide.*protected writes.*explicitly declare 'get_tasks'"):
        close_module_read_actions(
            _output(), app_build_plan=context.get("app_build_plan"), data_contract=context.get("data_contract"),
            subscription_contract=context.get("subscription_contract"),
        )


@pytest.mark.parametrize("access", [
    {"permissions": ["tasks.audit"]}, {"api_surface": "internal"}, {"handler_method": "restricted_list"},
])
def test_app_wide_explicit_read_access_survives_unprotected_writes(access):
    output = _closed(contract=_contract("app_wide"))
    actions = output["module_contract"]["module_yaml"]["actions"]
    read = next(action for action in actions if action["id"] == "list_tasks")
    read.update(access)
    assert _closed(output, _contract("app_wide")) == output


def test_app_wide_gate_only_read_still_gets_canonical_backend():
    output = _closed(contract=_contract("app_wide"))
    read = next(action for action in output["module_contract"]["module_yaml"]["actions"] if action["id"] == "list_tasks")
    read["entitlement_gate"] = "tasks.reporting"
    files = extract_code_file_map_from_payload(output)
    changes = materialize_module_read_implementations(files, app_build_plan=_plan(), data_contract=_contract("app_wide"))
    assert "async def list_tasks" in changes[f"{BACKEND}/handler.py"]


def test_artifact_only_app_wide_write_gate_rejects_implicit_reads_on_save():
    context = ContextVariablesBridge({
        "app_build_plan": _plan(), "data_contract": _contract("app_wide"),
        "current_build_task": {"owned_paths": [MANIFEST]},
        "subscription_contract_artifact": {"commit_metadata": {"metadata": {"summary_payload": {
            "contract_required": True, "module_contract_updates": [{
                "module_id": MODULE, "action_id": "update_task", "entitlement_gate": "task.edit",
            }],
        }}}},
    })
    with pytest.raises(ValueError, match="app_wide.*protected writes.*explicitly declare 'get_tasks'"):
        save_generated_code(StructuredOutputOverlay(context, _output()))
    assert context.get("code_files") is None


@pytest.mark.parametrize("entity", [None, "tasks", "Unknown"])
def test_entity_mapping_is_explicit_even_for_one_collection(entity):
    contract = _contract()
    contract["surfaces"][0]["collections"][0]["entity"] = entity
    with pytest.raises(ValueError, match="entity must name" if entity is None else "must declare entity.*Task"):
        _closed(contract=contract)


def test_duplicate_entity_mapping_is_rejected():
    contract = _contract()
    collections = contract["surfaces"][0]["collections"]
    collections.append({**collections[0], "name": "archived_tasks"})
    with pytest.raises(ValueError, match="entity duplicates task_management.Task"):
        _closed(contract=contract)


def test_shared_collection_has_the_same_canonical_reads_policy_and_implementation():
    contract = _contract()
    collection = contract["surfaces"][0]["collections"].pop()
    contract["shared_collections"] = [collection]
    output = _output()
    output["module_contract"]["module_yaml"]["actions"] = []
    files = extract_code_file_map_from_payload(_closed(output, contract))
    files.update(materialize_module_read_implementations(files, app_build_plan=_plan(), data_contract=contract))
    files.update(materialize_module_policies(files, contract))
    assert files == _files()


def test_design_custom_reads_are_required_but_not_invented():
    # Approved custom actions are checked after both canonical families close.
    with pytest.raises(ValueError, match="declare approved actions.*task_summary"):
        close_module_actions(
            _output(), app_build_plan=_plan(), data_contract=_contract(),
            design_surface_map={"surfaces": [{
                "surface_id": MODULE, "surface_kind": "module", "custom_reads": ["task_summary"],
            }]},
        )
    assert close_module_read_actions(
        _output(), app_build_plan=_plan(), data_contract=_contract(),
        design_surface_map={"surfaces": [{
            "surface_id": MODULE, "surface_kind": "module", "custom_reads": ["task_summary"],
        }]},
    )["module_contract"]["module_yaml"]["actions"][-1]["id"] == "list_tasks"


def test_nonpersistent_facade_does_not_receive_reads():
    assert close_module_read_actions(_output(), app_build_plan=_plan(), data_contract={"surfaces": []}) == _output()


@pytest.mark.parametrize("metadata", [
    {}, {"entity": "Task"}, {"tenancy": "per_user"}, {"owner_field": "owner_id"},
    {"entity": "Task", "tenancy": "per_user"},
])
def test_legacy_or_partial_metadata_cannot_authorize_generated_reads_or_policies(metadata):
    contract = _contract()
    collection = contract["surfaces"][0]["collections"][0]
    for field in ("entity", "tenancy", "owner_field"):
        collection.pop(field)
    collection.update(metadata)
    assert _closed(contract=contract) == _output()
    files = extract_code_file_map_from_payload(_output())
    files[f"{BACKEND}/policy.py"] = "# Existing application policy remains application-owned.\n"
    before = dict(files)
    assert materialize_module_read_actions(files, app_build_plan=_plan(), data_contract=contract) == {}
    assert materialize_module_read_implementations(files, app_build_plan=_plan(), data_contract=contract) == {}
    assert materialize_module_policies(files, contract) == {}
    assert canonical_read_actions_for_surface({
        "surface_id": MODULE, "surface_kind": "module", "primary_entities": ["Task"],
    }, contract) == []
    assert files == before


def test_assembly_and_typed_read_contracts_are_identical_and_idempotent():
    files = extract_code_file_map_from_payload(_output())
    changes = materialize_module_read_actions(files, app_build_plan=_plan(), data_contract=_contract())
    files.update(changes)
    assert list(changes) == [MANIFEST]
    assert files == extract_code_file_map_from_payload(_closed())
    assert materialize_module_read_actions(files, app_build_plan=_plan(), data_contract=_contract()) == {}


def test_constructed_implementations_pass_module_checks_without_service_agent():
    files = _files()
    result = validate_module_implementation_contract(files)
    assert result["passed"], result["failed_tests"]
    assert audit_module_runtime_quality([{"filename": path, "content": content} for path, content in files.items()]) == []
    assert materialize_module_read_implementations(files, app_build_plan=_plan(), data_contract=_contract()) == {}


def test_implementation_renderer_preserves_writes_and_exclusive_task_ownership():
    files = _files()
    files[f"{BACKEND}/handler.py"] = (
        "# Authored write behavior stays in place.\n"
        "class TaskManagementHandler:\n"
        "    async def create_task(self, ctx, **params):\n"
        "        return {'created': params['title']}\n"
    )
    changes = materialize_module_read_implementations(
        files, app_build_plan=_plan(), data_contract=_contract(), owned_paths=[f"{BACKEND}/handler.py"],
    )
    assert list(changes) == [f"{BACKEND}/handler.py"]
    assert "# Authored write behavior stays in place." in changes[f"{BACKEND}/handler.py"]
    assert "return {'created': params['title']}" in changes[f"{BACKEND}/handler.py"]
    assert "async def get_tasks" in changes[f"{BACKEND}/handler.py"]


@pytest.mark.parametrize("tenancy", ["per_user", "per_workspace"])
@pytest.mark.asyncio
async def test_generated_reads_use_runtime_collection_pagination_and_allowlist(tmp_path, monkeypatch, tenancy):
    files = _files(tenancy)
    package_name = f"generated_secure_reads_{tenancy}"
    package = tmp_path / package_name
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    for path, source in files.items():
        if path.startswith(BACKEND):
            (package / Path(path).name).write_text(source, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    handler = importlib.import_module(f"{package_name}.handler").TaskManagementHandler()
    owner_field = "owner_id" if tenancy == "per_user" else "workspace_owner"
    owner = "owner" if tenancy == "per_user" else "workspace"
    collection = SimpleNamespace(
        count=AsyncMock(return_value=4),
        aggregate=AsyncMock(return_value=[{"id": "1", "title": "Safe", owner_field: owner, "future_secret": "private"}]),
        find_one=AsyncMock(return_value={"id": "1", "title": "Safe", owner_field: owner, "future_secret": "private"}),
    )
    requested = []

    def resolve(module, name):
        requested.append((module, name))
        return collection

    ctx = ModuleContext(app_id="app", user_id="owner", workspace_id="workspace")
    ctx.persistence = SimpleNamespace(collection=resolve)
    result = await handler.list_tasks(ctx, page=2, page_size=2, search="a.*")
    assert result == {"items": [{"id": "1", "title": "Safe", owner_field: owner}], "total": 4}
    query = collection.count.call_args.args[0]
    assert owner_field not in query
    assert query["$or"][0]["id"]["$regex"] == "a\\.\\*"
    assert collection.aggregate.call_args.args[0][-2:] == [{"$skip": 2}, {"$limit": 2}]
    assert await handler.get_tasks(ctx, id="1") == {"item": {"id": "1", "title": "Safe", owner_field: owner}}
    collection.find_one.assert_awaited_once_with({"id": "1"})
    assert requested == [(MODULE, "tasks"), (MODULE, "tasks")]
    with pytest.raises(TypeError):
        await handler.list_tasks(ctx, owner_id="foreign")
    assert "scoped_query" not in files[f"{BACKEND}/repo.py"]
    collection.find_one.return_value = None
    with pytest.raises(ModuleRecordNotFoundError, match="Record not found"):
        await handler.get_tasks(ctx, id="foreign-id")


def test_real_context_save_generates_only_owned_read_backend_files():
    files = _files()
    owned = [f"{BACKEND}/handler.py", f"{BACKEND}/service.py", f"{BACKEND}/repo.py"]
    bridge = ContextVariablesBridge({
        "app_build_plan": _plan(), "data_contract": _contract(),
        "code_files": [{"filename": MANIFEST, "content": files[MANIFEST]}],
        "current_build_task": {"owned_paths": owned},
    })
    result = save_generated_code(StructuredOutputOverlay(bridge, {"python_files": [], "code_files": []}))
    assert result["saved_files"] == sorted(owned)
    assert json.loads(json.dumps(bridge.snapshot()["data_contract"])) == _contract()


@pytest.mark.asyncio
@pytest.mark.parametrize("typed_output", [True, False])
@pytest.mark.parametrize("artifact_only", [True, False])
async def test_task_dependency_keeps_custom_read_and_write_gates_and_ungated_canonical_reads(monkeypatch, typed_output, artifact_only):
    contract_task = {
        "task_id": "task_contract", "task_type": "module_contract", "capability_pack_id": MODULE,
        "initial_agent": "ConfigMiddlewareAgent", "initial_message": "Declare task actions.",
        "owned_paths": [MANIFEST], "depends_on": [],
    }
    service_task = {
        "task_id": "task_service", "task_type": "business_services", "capability_pack_id": MODULE,
        "initial_agent": "ServiceAgent", "initial_message": "Implement declared task actions.",
        "owned_paths": [f"{BACKEND}/handler.py"], "depends_on": ["task_contract"],
    }
    bridge = ContextVariablesBridge({
        "app_build_plan": _plan(), "data_contract": _contract(),
        "app_task_batch_items": [contract_task, service_task],
        "design_surface_map": {"surfaces": [{
            "surface_id": MODULE, "surface_kind": "module", "owner": "app",
            "primary_entities": ["Task"], "owned_mutations": ["create_task", "update_task", "delete_task"],
            "custom_reads": ["task_summary"],
        }]},
        "subscription_contract": {"contract_required": True, "module_contract_updates": [
            {"module_id": MODULE, "action_id": "update_task", "entitlement_gate": "task.edit"},
            {"module_id": MODULE, "action_id": "task_summary", "entitlement_gate": "task.summary"},
        ]},
    })
    if artifact_only:
        bridge.set("subscription_contract_artifact", {"metadata": {"summary_payload": bridge.get("subscription_contract")}})
        bridge.set("subscription_contract", None)
    observed = []

    async def run(_runner, request):
        worker = ContextVariablesBridge(request.context_variables)
        if request.task_id == "task_contract":
            output = _output()
            output["module_contract"]["module_yaml"]["actions"].append({"id": "task_summary", "handler_method": "task_summary"})
            if not typed_output:
                output = {"code_files": [{"filename": path, "content": content}
                                         for path, content in extract_code_file_map_from_payload(output).items()]}
        else:
            dependency = worker.get("dependency_task_outputs")["task_contract"]
            files = extract_code_file_map_from_payload(detach(dependency))
            actions = {action["id"]: action for action in yaml.safe_load(files[MANIFEST])["actions"]}
            assert actions["update_task"]["entitlement_gate"] == "task.edit"
            assert actions["task_summary"]["entitlement_gate"] == "task.summary"
            assert "entitlement_gate" not in actions["list_tasks"]
            assert actions["list_tasks"]["permissions"] == []
            assert "entitlement_gate" not in actions["get_tasks"]
            observed.append(actions)
            output = {"code_files": []}
        return AG2TaskBatchRunnerResult(status=RunStatus.COMPLETED, output=output)

    monkeypatch.setattr(task_batches.AG2TaskBatchRunner, "run", run)
    config = task_batches.load_task_batches_config(
        "AppGenerator", workflows_root=Path(__file__).resolve().parents[1] / "factory_app" / "workflows",
    )

    async def checkpoint(updates):
        for key, value in updates.items():
            bridge.set(key, value)

    snapshot = bridge.snapshot()
    await task_batches.execute_task_batches_for_trigger(
        workflow_name="AppGenerator", trigger_agent="AppPlanAgent", batches_config=config,
        agents={"ConfigMiddlewareAgent": object(), "ServiceAgent": object()}, context_variables=snapshot,
        chat_id="read-actions", app_id="read-actions", user_id="user-1", fresh_agents_per_task=False,
        parent_channel_id="read-actions-parent", checkpoint=checkpoint,
    )
    assert snapshot["app_task_batch_status"] == "completed", snapshot["app_task_batch_results"]
    assert len(observed) == 1


@pytest.mark.asyncio
async def test_app_wide_authored_read_survives_task_assembly_and_repeated_materialization(monkeypatch):
    contract = _contract("app_wide")
    output = _closed(contract=contract)
    actions = output["module_contract"]["module_yaml"]["actions"]
    actions[:] = [action for action in actions if action["id"] in {"create_task", "list_tasks"}]
    read = next(action for action in actions if action["id"] == "list_tasks")
    read.update(permissions=["tasks.audit"], api_surface="internal", handler_method="restricted_list")
    original_read = next(
        action for action in yaml.safe_load(extract_code_file_map_from_payload(output)[MANIFEST])["actions"]
        if action["id"] == "list_tasks"
    )
    authored = {
        f"{BACKEND}/handler.py": (
            "from . import service\n\nclass TaskManagementHandler:\n"
            "    async def create_task(self, ctx, **params):\n        return {'created': params['title']}\n"
            "    async def restricted_list(self, ctx, **params):\n        return await service.restricted_list(ctx)\n"
        ),
        f"{BACKEND}/service.py": (
            "from . import repo\n\nasync def restricted_list(ctx):\n"
            "    return await repo.restricted_list(ctx)\n"
        ),
        f"{BACKEND}/repo.py": "async def restricted_list(ctx):\n    return {'items': [], 'access': 'audit'}\n",
    }
    tasks = [{
        "task_id": "contract", "task_type": "module_contract", "capability_pack_id": MODULE,
        "initial_agent": "ConfigMiddlewareAgent", "initial_message": "Declare actions.",
        "owned_paths": [MANIFEST], "depends_on": [],
    }, {
        "task_id": "service", "task_type": "business_services", "capability_pack_id": MODULE,
        "initial_agent": "ServiceAgent", "initial_message": "Implement actions.",
        "owned_paths": [*authored, f"{BACKEND}/policy.py"], "depends_on": ["contract"],
    }]
    plan = {**_plan(), "build_tasks": tasks}
    bridge = ContextVariablesBridge({
        "app_build_plan": plan, "data_contract": contract, "app_task_batch_items": tasks,
    })

    async def run(_runner, request):
        candidate = output if request.task_id == "contract" else {"code_files": [
            {"filename": path, "content": source} for path, source in authored.items()
        ]}
        return AG2TaskBatchRunnerResult(status=RunStatus.COMPLETED, output=deepcopy(candidate))

    monkeypatch.setattr(task_batches.AG2TaskBatchRunner, "run", run)
    config = task_batches.load_task_batches_config(
        "AppGenerator", workflows_root=Path(__file__).resolve().parents[1] / "factory_app" / "workflows",
    )

    async def checkpoint(updates):
        for key, value in updates.items():
            bridge.set(key, value)

    snapshot = bridge.snapshot()
    await task_batches.execute_task_batches_for_trigger(
        workflow_name="AppGenerator", trigger_agent="AppPlanAgent", batches_config=config,
        agents={"ConfigMiddlewareAgent": object(), "ServiceAgent": object()}, context_variables=snapshot,
        chat_id="authored-read", app_id="authored-read", user_id="user-1", fresh_agents_per_task=False,
        parent_channel_id="authored-read-parent", checkpoint=checkpoint,
    )
    results = snapshot["app_task_batch_results"]
    assert snapshot["app_task_batch_status"] == "completed", results
    accepted = [results["contract"], results["service"]]
    task_files = {path: source for candidate in accepted for path, source in extract_code_file_map_from_payload(candidate).items()}
    compiled_read = next(action for action in yaml.safe_load(task_files[MANIFEST])["actions"] if action["id"] == "list_tasks")
    original_read.pop("entitlement_gate", None)
    assert compiled_read == original_read
    for path, source in authored.items():
        assert source.rstrip() in task_files[path]
        assert "async def get_tasks" in task_files[path]
        assert "async def list_tasks" not in task_files[path]
    assembled = _merge_code_files(accepted, app_build_plan=plan, data_contract=contract)
    assembled_files = {item["filename"]: item["content"] for item in assembled}
    assembled_read = next(
        action for action in yaml.safe_load(assembled_files[MANIFEST])["actions"] if action["id"] == "list_tasks"
    )
    assert assembled_read == original_read
    # Assembly renders the code-owned schemas.py for every persistent module, like policy.py.
    schemas_path = f"{BACKEND}/schemas.py"
    assert "class TaskRecord(TypedDict, total=False):" in assembled_files[schemas_path]
    assert {path: source for path, source in assembled_files.items() if path not in {MANIFEST, schemas_path}} == {
        path: source for path, source in task_files.items() if path != MANIFEST
    }
    assert _merge_code_files([{"code_files": assembled}], app_build_plan=plan, data_contract=contract) == assembled
