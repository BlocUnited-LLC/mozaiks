"""SecurityReadiness reports a permissionless action unless what it reaches is protected.

Every case changes declared contracts or code of the recorded fdfa818e bundle.
The scanner tests assert which actions are reported and at what severity. The
real-Mongo tests compose a variant's module router and executor against a
private database, dispatch actions as different callers, and assert what each
caller can reach next to the scanner's verdict on the same files
(docs/architecture/app/generated-action-protection.md). Every variant a
dispatch test uses loads without a failed module.
"""
from __future__ import annotations

import ast
import json
import os
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import FastAPI, Request

from factory_app.workflows.AppGenerator.tools.module_persistence_guard import PersistenceResolver
from factory_app.workflows.SecurityReadiness.tools.inspect_generated_app_security import (
    _PERMISSION_GAPS,
    inspect_generated_app_security,
)
from factory_app.workflows.SecurityReadiness.tools.module_data_reach import read_source
from tests.test_security_readiness_target_binding import (
    invoke,
    security_build_fixture,  # noqa: F401
)

FIXTURE = Path(__file__).parent / "fixtures" / "runtime_smoke_good_bundle_fdfa818e.json"
MODULE = "modules/task_management/module.yaml"
TASKS = "modules/task_management/backend"
CONTRACT = "data/contract.json"
PLANS = "config/subscriptions.yaml"
TASK_ACTIONS = ("create_task", "update_task", "delete_task", "get_tasks", "list_tasks")
UNGATED = ("create_task", "delete_task", "get_tasks", "list_tasks")
UPDATE_GATE = "feature.module.task_management.update_task"
SUMMARY_GATE = "feature.module.task_management.summarize_tasks"


def _recorded() -> dict[str, str]:
    return dict(json.loads(FIXTURE.read_text(encoding="utf-8"))["files"])


def _yaml(files: dict[str, str], path: str, change: Callable[[dict[str, Any]], None]) -> None:
    document = yaml.safe_load(files[path])
    change(document)
    files[path] = yaml.safe_dump(document, sort_keys=False)


def _json(files: dict[str, str], path: str, change: Callable[[dict[str, Any]], None]) -> None:
    document = json.loads(files[path])
    change(document)
    files[path] = json.dumps(document, indent=2)


def _action(module: dict[str, Any], action_id: str) -> dict[str, Any]:
    return next(action for action in module["actions"] if action["id"] == action_id)


def _surface(contract: dict[str, Any], surface_id: str) -> dict[str, Any]:
    return next(item for item in contract["surfaces"] if item["surface_id"] == surface_id)


def _tasks_collection(contract: dict[str, Any]) -> dict[str, Any]:
    return _surface(contract, "task_management")["collections"][0]


def _plan(plans: dict[str, Any], plan_id: str) -> dict[str, Any]:
    return next(plan for plan in plans["plans"] if plan["plan_id"] == plan_id)


def _owned_collection(owner: str, name: str, entity: str) -> dict[str, Any]:
    """A per_user collection shaped like the recorded tasks collection."""
    collection = json.loads(json.dumps(_tasks_collection(json.loads(_recorded()[CONTRACT]))))
    collection.update(name=name, entity=entity, ownership={"surface_id": owner, "surface_kind": "module"})
    collection["indexes"] = []
    return collection


# --------------------------------------------------------------------------- bundle variants


def as_t041(files: dict[str, str]) -> None:
    """The recorded T041 shape: update is ungated, and a paid, owner-scoped summary only Pro grants."""
    summary = {
        "id": "summarize_tasks", "description": "Summarize current tasks.", "handler_method": "summarize_tasks",
        "api_surface": None,
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "output_schema": {
            "type": "object",
            "properties": {"active_count": {"type": "integer"}, "completed_count": {"type": "integer"}},
            "required": ["active_count", "completed_count"],
        },
        "permissions": [], "emits": [], "ask_context_safe": False, "entitlement_gate": SUMMARY_GATE,
    }

    def change(module: dict[str, Any]) -> None:
        _action(module, "update_task").pop("entitlement_gate")
        module["actions"].append(summary)

    _yaml(files, MODULE, change)
    _yaml(files, PLANS, lambda plans: _plan(plans, "pro")["capabilities"].append(SUMMARY_GATE))
    files[f"{TASKS}/handler.py"] += (
        "\n    async def summarize_tasks(self, ctx, **params):\n"
        "        from . import service\n"
        "        return await service.summarize_tasks(ctx)\n"
    )
    files[f"{TASKS}/service.py"] += (
        "\nasync def summarize_tasks(ctx):\n"
        "    active_count = await repo.count_open_tasks(ctx)\n"
        "    completed_count = await repo.count_completed_tasks(ctx)\n"
        "    return {'active_count': active_count, 'completed_count': completed_count}\n"
    )
    files[f"{TASKS}/repo.py"] += (
        "\nasync def count_open_tasks(ctx):\n"
        "    return await ctx.persistence.collection('task_management', 'tasks').count({'status': {'$ne': 'completed'}})\n"
        "\nasync def count_completed_tasks(ctx):\n"
        "    return await ctx.persistence.collection('task_management', 'tasks').count({'status': 'completed'})\n"
    )


def without_sign_in(files: dict[str, str]) -> None:
    _json(files, "app.json", lambda app: app.update(authRequired=False))
    files.pop("config/auth.yaml")


def without_auth_required(files: dict[str, str]) -> None:
    """A valid config/auth.yaml alone does not make the app a signed-in app."""
    _json(files, "app.json", lambda app: app.update(authRequired=False))


def with_invalid_auth_contract(files: dict[str, str]) -> None:
    _yaml(files, "config/auth.yaml", lambda auth: auth.pop("strategy"))


def with_app_wide_tasks(files: dict[str, str]) -> None:
    _json(files, CONTRACT, lambda contract: _tasks_collection(contract).update(tenancy="app_wide", owner_field=None))


def with_workspace_tasks(files: dict[str, str]) -> None:
    def change(contract: dict[str, Any]) -> None:
        tasks = _tasks_collection(contract)
        tasks["fields"].append(
            {"default": None, "enum": None, "name": "workspace_id", "nullable": False, "required": True, "type": "string"}
        )
        tasks.update(tenancy="per_workspace", owner_field="workspace_id")

    _json(files, CONTRACT, change)


def without_task_owner_field(files: dict[str, str]) -> None:
    """per_user without an owner field loads, and the runtime does not scope it."""
    _json(files, CONTRACT, lambda contract: _tasks_collection(contract).pop("owner_field"))


def with_app_wide_notes(files: dict[str, str]) -> None:
    """task_management also declares an app_wide collection that none of the task actions touch."""

    def change(contract: dict[str, Any]) -> None:
        notes = _owned_collection("task_management", "notes", "Note")
        notes.update(tenancy="app_wide", owner_field=None)
        _surface(contract, "task_management")["collections"].append(notes)

    _json(files, CONTRACT, change)


def with_default_plan_update(files: dict[str, str]) -> None:
    _yaml(files, PLANS, lambda plans: _plan(plans, "free")["capabilities"].append(UPDATE_GATE))


def without_subscriptions(files: dict[str, str]) -> None:
    files.pop(PLANS)


def with_invalid_subscriptions(files: dict[str, str]) -> None:
    _yaml(files, PLANS, lambda plans: plans.update(default_plan_id="missing"))


def _v2_subscriptions(files: dict[str, str], *, second_default_grants_update: bool) -> None:
    """Two products. A gate the default plan of any product grants admits every signed-in user."""

    def change(plans: dict[str, Any]) -> None:
        tasks_plans = plans.pop("plans")
        plans.pop("default_plan_id")
        extras = [UPDATE_GATE] if second_default_grants_update else ["feature.module.task_management.create_task"]
        plans.update(
            schema_version="mozaiks.subscriptions.v2",
            default_product_id="tasks",
            products=[
                {"product_id": "tasks", "label": "Tasks", "default_plan_id": "free", "plans": tasks_plans},
                {
                    "product_id": "extras", "label": "Extras", "default_plan_id": "basic",
                    "plans": [{"plan_id": "basic", "label": "Basic", "capabilities": extras}],
                },
            ],
        )

    _yaml(files, PLANS, change)


def with_v2_subscriptions(files: dict[str, str]) -> None:
    _v2_subscriptions(files, second_default_grants_update=False)


def with_v2_second_product_granting_update(files: dict[str, str]) -> None:
    _v2_subscriptions(files, second_default_grants_update=True)


def without_data_contract(files: dict[str, str]) -> None:
    files.pop(CONTRACT)


def without_data_contract_or_repo(files: dict[str, str]) -> None:
    """No contract, and the persistence code lives in store.py rather than repo.py."""
    files.pop(CONTRACT)
    files[f"{TASKS}/store.py"] = files.pop(f"{TASKS}/repo.py")
    files[f"{TASKS}/service.py"] = files[f"{TASKS}/service.py"].replace("import repo", "import store as repo")


def with_unhashable_owner(files: dict[str, str]) -> None:
    def change(contract: dict[str, Any]) -> None:
        _surface(contract, "task_management")["collections"].append({"name": "notes", "module_id": {"id": "task_management"}})

    _json(files, CONTRACT, change)


def with_duplicate_collection(files: dict[str, str]) -> None:
    def change(contract: dict[str, Any]) -> None:
        collections = _surface(contract, "task_management")["collections"]
        collections.append(dict(collections[0]))

    _json(files, CONTRACT, change)


def with_entities_list_tenancy(files: dict[str, str]) -> None:
    _json(files, CONTRACT, lambda contract: contract.update(
        entities=[{"module_id": "task_management", "entity_name": "Note", "tenancy": ["per_user"]}],
    ))


def without_task_collections(files: dict[str, str]) -> None:
    """The contract drops task_management's surface; its repository still addresses tasks."""
    _json(files, CONTRACT, lambda contract: contract.update(
        surfaces=[item for item in contract["surfaces"] if item["surface_id"] != "task_management"],
    ))


def _list_tasks_as(**changes: Any) -> Callable[[dict[str, str]], None]:
    def change(files: dict[str, str]) -> None:
        _yaml(files, MODULE, lambda module: _action(module, "list_tasks").update(changes))

    return change


with_operator_list = _list_tasks_as(api_surface="admin_internal")
with_blank_surface = _list_tasks_as(api_surface="")
with_public_mutation_list = _list_tasks_as(api_surface="public_mutation")
with_padded_public_list = _list_tasks_as(api_surface=" public ")
with_permissions_mapping = _list_tasks_as(permissions={})


def with_declared_permission_and_internal_read(files: dict[str, str]) -> None:
    def change(module: dict[str, Any]) -> None:
        module["permissions"].append({"id": "task_management.view", "description": "View tasks."})
        _action(module, "list_tasks")["permissions"] = ["task_management.view"]
        _action(module, "get_tasks").update(api_surface="internal", permissions=[])

    _yaml(files, MODULE, change)


def with_task_permissions(files: dict[str, str]) -> None:
    def change(module: dict[str, Any]) -> None:
        module["permissions"].append({"id": "task_management.manage", "description": "Manage tasks."})
        for action in module["actions"]:
            action["permissions"] = ["task_management.manage"]

    _yaml(files, MODULE, change)


def with_module_directory_renamed(files: dict[str, str]) -> None:
    """Contracts name the module id; the directory name does not decide its collections."""
    for path in [path for path in files if path.startswith("modules/task_management/")]:
        files[path.replace("modules/task_management/", "modules/tasks/", 1)] = files.pop(path)


def under_app_root(files: dict[str, str]) -> None:
    for path in list(files):
        files[f"app/{path}"] = files.pop(path)


def with_decoy_app_contract(files: dict[str, str]) -> None:
    """The bundle root holds app.json, so the runtime binds it and never loads app/."""
    files["app/app.json"] = files["app.json"]
    files[f"app/{CONTRACT}"] = files[CONTRACT]
    with_app_wide_tasks(files)


def with_decoy_app_auth(files: dict[str, str]) -> None:
    files["app/app.json"] = files["app.json"]
    files["app/config/auth.yaml"] = files.pop("config/auth.yaml")


DIGEST_HANDLER = (
    "class TaskDigestModule:\n"
    "    async def list_digest(self, ctx):\n"
    "        from . import repo\n"
    "        return {'items': await repo.list_digest(ctx)}\n"
)
READ_TASKS = (
    "async def list_digest(ctx):\n"
    "    tasks = ctx.persistence.collection('task_management', 'tasks')\n"
    "    records = await tasks.find_many({}, limit=100)\n"
    "    return [{'task_id': record['task_id'], 'user_id': record.get('user_id')} for record in records]\n"
)
READ_OWN_DIGESTS = (
    "async def list_digest(ctx):\n"
    "    return await ctx.persistence.collection('task_digest', 'digests').find_many({}, limit=100)\n"
)


def _digest(
    repo: str, *, gate: str | None = None, own_collection: bool = True, extra: dict[str, str] | None = None,
) -> Callable[[dict[str, str]], None]:
    """Add task_digest: one permissionless signed-in action whose repository is ``repo``.

    ``extra`` adds or replaces app files, keyed by path.
    """

    def change(files: dict[str, str]) -> None:
        action = {
            "id": "list_digest", "description": "List the tasks the digest covers.", "handler_method": "list_digest",
            "api_surface": None,
            "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
            "output_schema": {"type": "object", "properties": {"items": {"type": "array"}}, "required": ["items"]},
            "permissions": [], "emits": [], "ask_context_safe": False,
        }
        if gate:
            action["entitlement_gate"] = gate
        files["modules/task_digest/module.yaml"] = yaml.safe_dump({
            "schema_version": "mozaiks.module.v1",
            "module": {
                "id": "task_digest", "display_name": "Task Digest", "description": "Summarizes tasks.",
                "handler": "backend.handler:TaskDigestModule", "owner": "app", "type": "standard",
                "version": "1.0.0", "visibility": "private",
            },
            "permissions": [],
            "actions": [action],
        }, sort_keys=False)
        files["modules/task_digest/backend/__init__.py"] = ""
        files["modules/task_digest/backend/handler.py"] = DIGEST_HANDLER
        files["modules/task_digest/backend/repo.py"] = repo
        files.update(extra or {})
        if own_collection:
            _json(files, CONTRACT, lambda contract: contract["surfaces"].append({
                "surface_id": "task_digest", "surface_kind": "module",
                "collections": [_owned_collection("task_digest", "digests", "Digest")],
            }))

    return change


def with_task_alias(files: dict[str, str]) -> None:
    """A data alias for the per_user tasks collection's literal name."""

    def change(contract: dict[str, Any]) -> None:
        _tasks_collection(contract)["mongo_collection"] = "task_records"
        contract["aliases"].append({"alias": "tasks.records", "collection": "task_records"})

    _json(files, CONTRACT, change)


def without_contract_version(files: dict[str, str]) -> None:
    _json(files, CONTRACT, lambda contract: contract.pop("version"))


def with_aliases_not_a_list(files: dict[str, str]) -> None:
    _json(files, CONTRACT, lambda contract: contract.update(aliases=7))


def with_shares_not_a_list(files: dict[str, str]) -> None:
    _json(files, CONTRACT, lambda contract: contract.update(shared_collections=True))


def with_auth_required_string(files: dict[str, str]) -> None:
    """Only the boolean true declares sign-in."""
    _json(files, "app.json", lambda app: app.update(authRequired="true"))


def with_long_constant_in_the_repository(files: dict[str, str]) -> None:
    """A loadable constant too deep for a recursive reading."""
    files[f"{TASKS}/repo.py"] += "\nLABELS = " + " + ".join(["'a'"] * 1200) + "\n"


def with_tasks_shared_with_digest(files: dict[str, str]) -> None:
    _json(files, CONTRACT, lambda contract: contract.update(shared_collections=[{
        "owner_module": "task_management", "name": "tasks",
        "shared_with": [{"module": "task_digest", "access": "read", "purpose": "Digest of tasks."}],
    }]))


def _write(root: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _variant(*changes: Callable[[dict[str, str]], None]) -> dict[str, str]:
    files = _recorded()
    for change in changes:
        change(files)
    return files


# --------------------------------------------------------------------------- scanner


async def _scan(security_build, files: dict[str, str]) -> list[dict[str, Any]]:
    build = security_build.add(files)
    result = await invoke(inspect_generated_app_security, build.bridge)
    assert result["success"] is True
    return [item for item in result["findings"] if item["finding_id"].startswith("module_permissions:")]


async def _permission_findings(security_build, files: dict[str, str], *, module: str | None = None) -> dict[str, str]:
    findings = {
        item["finding_id"].removeprefix("module_permissions:missing:"): item["severity"]
        for item in await _scan(security_build, files)
    }
    return {key: value for key, value in findings.items() if module is None or key.startswith(f"{module}:")}


def _expect(severity: str, actions: tuple[str, ...], module: str = "task_management") -> dict[str, str]:
    return {f"{module}:{action}": severity for action in actions}


PLAN_ONLY_UPDATE = {**_expect("high", UNGATED), **_expect("medium", ("update_task",))}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        pytest.param((), id="recorded_owner_scoped_crud"),
        pytest.param((as_t041,), id="t041_paid_owner_scoped_summary"),
        pytest.param((with_declared_permission_and_internal_read,), id="declared_permission_and_internal_read"),
        pytest.param((with_workspace_tasks,), id="per_workspace_ownership"),
        pytest.param((with_module_directory_renamed,), id="module_id_not_directory_names_its_collections"),
        pytest.param((under_app_root,), id="bundle_under_app_root"),
    ],
)
async def test_protected_actions_raise_no_permission_finding(security_build, changes) -> None:
    assert await _permission_findings(security_build, _variant(*changes)) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        pytest.param((without_sign_in,), _expect("high", TASK_ACTIONS), id="no_sign_in"),
        pytest.param((without_auth_required,), _expect("high", TASK_ACTIONS), id="auth_contract_without_auth_required"),
        pytest.param((with_invalid_auth_contract,), _expect("high", TASK_ACTIONS), id="invalid_auth_contract"),
        pytest.param((with_decoy_app_auth,), _expect("high", TASK_ACTIONS), id="auth_contract_outside_bound_root"),
        pytest.param((with_app_wide_tasks,), PLAN_ONLY_UPDATE, id="app_wide_gate_restricts_by_plan_only"),
        pytest.param((with_decoy_app_contract,), PLAN_ONLY_UPDATE, id="data_contract_outside_bound_root"),
        pytest.param((with_app_wide_notes,), PLAN_ONLY_UPDATE, id="module_with_one_app_wide_collection"),
        pytest.param(
            (with_app_wide_notes, with_module_directory_renamed), PLAN_ONLY_UPDATE,
            id="module_id_not_directory_names_declared_collections",
        ),
        pytest.param(
            (with_app_wide_tasks, with_default_plan_update), _expect("high", TASK_ACTIONS),
            id="app_wide_gate_granted_by_default_plan",
        ),
        pytest.param(
            (with_app_wide_tasks, without_subscriptions), _expect("high", TASK_ACTIONS),
            id="app_wide_gate_without_plan_catalog",
        ),
        pytest.param(
            (with_app_wide_tasks, with_invalid_subscriptions), _expect("high", TASK_ACTIONS),
            id="app_wide_gate_with_invalid_plan_catalog",
        ),
        pytest.param((with_app_wide_tasks, with_v2_subscriptions), PLAN_ONLY_UPDATE, id="app_wide_gate_v2_catalog"),
        pytest.param(
            (with_app_wide_tasks, with_v2_second_product_granting_update), _expect("high", TASK_ACTIONS),
            id="app_wide_gate_granted_by_any_v2_product_default",
        ),
        pytest.param((without_task_owner_field,), _expect("high", TASK_ACTIONS), id="per_user_without_owner_field"),
        pytest.param((without_data_contract,), _expect("high", TASK_ACTIONS), id="persistence_without_data_contract"),
        pytest.param(
            (without_data_contract_or_repo,), _expect("high", TASK_ACTIONS), id="no_data_contract_and_no_repo_py",
        ),
        pytest.param((with_unhashable_owner,), _expect("high", TASK_ACTIONS), id="contract_raising_type_error"),
        pytest.param((without_contract_version,), _expect("high", TASK_ACTIONS), id="contract_the_loader_rejects"),
        pytest.param((with_duplicate_collection,), _expect("high", TASK_ACTIONS), id="contract_raising_value_error"),
        pytest.param((with_entities_list_tenancy,), _expect("high", TASK_ACTIONS), id="entities_row_with_list_tenancy"),
        pytest.param((with_aliases_not_a_list,), _expect("high", TASK_ACTIONS), id="aliases_not_a_list"),
        pytest.param((with_shares_not_a_list,), _expect("high", TASK_ACTIONS), id="shared_collections_not_a_list"),
        pytest.param((with_auth_required_string,), _expect("high", TASK_ACTIONS), id="auth_required_not_boolean"),
        pytest.param(
            (with_long_constant_in_the_repository,), _expect("high", TASK_ACTIONS), id="code_too_deep_to_read",
        ),
        pytest.param((without_task_collections,), _expect("medium", UNGATED), id="no_declared_data_scope"),
        pytest.param((with_operator_list,), _expect("high", ("list_tasks",)), id="operator_action_without_permission"),
        pytest.param((with_blank_surface,), _expect("high", ("list_tasks",)), id="blank_surface"),
        pytest.param((with_public_mutation_list,), _expect("high", ("list_tasks",)), id="surface_the_loader_rejects"),
        pytest.param((with_padded_public_list,), _expect("high", ("list_tasks",)), id="padded_public_surface"),
        pytest.param((with_permissions_mapping,), _expect("high", ("list_tasks",)), id="permissions_not_a_list"),
    ],
)
async def test_unprotected_actions_raise_a_finding_at_their_severity(security_build, changes, expected) -> None:
    assert await _permission_findings(security_build, _variant(*changes)) == expected


HIGH_DIGEST = _expect("high", ("list_digest",), "task_digest")
LITERAL = (
    "async def list_digest(ctx):\n"
    "    return await ctx.persistence.literal_collection('scratch_notes').find_many({}, limit=100)\n"
)
ALIAS = (
    "from mozaiksai.core.runtime.persistence import app_data_from_context\n\n"
    "async def list_digest(ctx):\n"
    "    return await app_data_from_context(ctx).collection('{alias}').find_many({{}}, limit=100)\n"
)
HANDLE_PASSED_ON = (
    "def _tasks(persistence):\n"
    "    return persistence.collection('task_management', 'tasks')\n\n"
    "async def list_digest(ctx):\n"
    "    return await _tasks(ctx.persistence).find_many({}, limit=100)\n"
)
HANDLE_ATTRIBUTE = (
    "async def list_digest(ctx):\n"
    "    database = getattr(ctx.persistence, 'client')\n"
    "    return database\n"
)
DYNAMIC_IMPORT = (
    "import importlib\n\n"
    "async def list_digest(ctx, module_name='modules.task_management.backend.repo'):\n"
    "    return (await importlib.import_module(module_name).list_tasks(ctx))['items']\n"
)
RELATIVE_IMPORT_ELSEWHERE = (
    "import importlib\n\n"
    "async def list_digest(ctx):\n"
    "    task_repo = importlib.import_module('.repo', 'modules.task_management.backend')\n"
    "    return (await task_repo.list_tasks(ctx))['items']\n"
)
IMPORTED_SERVICE = (
    "from modules.task_management.backend import service as task_service\n\n"
    "async def list_digest(ctx):\n"
    "    return (await task_service.list_tasks(ctx))['items']\n"
)
RAW_DRIVER = (
    "from motor.motor_asyncio import AsyncIOMotorClient\n\n"
    "async def list_digest(ctx):\n"
    "    client = AsyncIOMotorClient('mongodb://127.0.0.1')\n"
    "    return await client['app']['tasks'].find({}).to_list(100)\n"
)
ALIAS_FROM_PARAMETER = (
    "from mozaiksai.core.runtime.persistence import app_data_from_context\n\n"
    "def _records(ctx, alias):\n"
    "    return app_data_from_context(ctx).collection(alias)\n\n"
    "async def list_digest(ctx):\n"
    "    return await _records(ctx, 'billing.unknown').find_many({}, limit=100)\n"
)
OWN_MODULE_TASKS = (
    "async def list_digest(ctx):\n"
    "    return await ctx.persistence.collection('task_digest', 'tasks').find_many({}, limit=100)\n"
)
CONSTANT_MODULE = (
    "TASKS_MODULE = 'task_management'\n\n"
    "async def list_digest(ctx):\n"
    "    return await ctx.persistence.collection(TASKS_MODULE, 'tasks').find_many({}, limit=100)\n"
)
NAME_FROM_PARAMETER = (
    "def _collection(ctx, name):\n"
    "    return ctx.persistence.collection('task_management', name)\n\n"
    "async def list_digest(ctx):\n"
    "    return await _collection(ctx, 'tasks').find_many({}, limit=100)\n"
)
MODULE_FROM_PARAMETER = (
    "def _collection(ctx, module):\n"
    "    return ctx.persistence.collection(module, 'tasks')\n\n"
    "async def list_digest(ctx):\n"
    "    return await _collection(ctx, 'task_management').find_many({}, limit=100)\n"
)
# The runtime imports each module's backend as the package mozaiks_runtime_module_<directory>.
RUNTIME_PACKAGE_IMPORT = (
    "from mozaiks_runtime_module_task_management.backend import repo as task_repo\n\n"
    "async def list_digest(ctx):\n"
    "    return (await task_repo.list_tasks(ctx))['items']\n"
)
RUNTIME_PACKAGE_DYNAMIC_IMPORT = (
    "import importlib\n\n"
    "async def list_digest(ctx):\n"
    "    task_repo = importlib.import_module('.backend.repo', 'mozaiks_runtime_module_task_management')\n"
    "    return (await task_repo.list_tasks(ctx))['items']\n"
)
APP_PACKAGE_IMPORT = IMPORTED_SERVICE.replace("from modules.", "from app.modules.")
CONTEXT_NAMESPACE = (
    "async def list_digest(ctx):\n"
    "    return await vars(ctx)['persistence'].collection('task_digest', 'digests').find_many({}, limit=100)\n"
)
CONTEXT_DICT = CONTEXT_NAMESPACE.replace("vars(ctx)", "ctx.__dict__")
LOADED_MODULE_TABLE = (
    "import sys\n\n"
    "async def list_digest(ctx):\n"
    "    task_repo = sys.modules['mozaiks_runtime_module_task_management.backend.repo']\n"
    "    return (await task_repo.list_tasks(ctx))['items']\n"
)
MODULE_CONSTANT = (
    "MODULE = 'task_digest'\n\n"
    "async def list_digest(ctx):\n"
    "    return await ctx.persistence.collection(MODULE, 'tasks').find_many({}, limit=100)\n"
)
CONSTANT_REBOUND_BY_GLOBALS = MODULE_CONSTANT + "\nglobals()['MODULE'] = 'task_management'\n"
REBINDS_REPO_CONSTANT = (
    "from . import repo\n\n"
    "setattr(repo, 'MODULE', 'task_management')\n"
)
REBINDS_DIGEST_CONSTANT = (
    "from modules.task_digest.backend import repo as digest_repo\n\n"
    "digest_repo.MODULE = 'task_management'\n"
)
DATABASE_ADAPTER = (
    '"""Provider database mechanics."""\n'
    "from pymongo import MongoClient\n\n"
    "def collection(client: MongoClient, database_name, collection_name):\n"
    "    return client[database_name][collection_name]\n"
)
USES_DATABASE_ADAPTER = (
    "from services.adapters.database import record_store\n\n"
    "async def list_digest(ctx):\n"
    "    return record_store.collection(None, ctx.persistence.database_name, 'digests')\n"
)
LITERAL_FROM_PARAMETER = (
    "def records(ctx, name):\n"
    "    return ctx.persistence.literal_collection(name)\n"
)
USES_LITERAL_HELPER = (
    "from services import record_names\n\n"
    "async def list_digest(ctx):\n"
    "    return await record_names.records(ctx, 'digests').find_many({}, limit=100)\n"
)
HANDLER_IMPORTS_STORE = DIGEST_HANDLER.replace("from . import repo", "from . import store as repo")


def with_alias_to_a_storage_name(files: dict[str, str]) -> None:
    """An alias whose collection is a storage name the contract does not declare as a literal."""
    _json(files, CONTRACT, lambda contract: contract["aliases"].append(
        {"alias": "tasks.everything", "collection": "app_release__task_management__tasks"},
    ))


def with_shared_literal_for_digest(files: dict[str, str]) -> None:
    """A share that also names a literal collection the contract does not declare."""
    with_tasks_shared_with_digest(files)
    _json(files, CONTRACT, lambda contract: contract["shared_collections"][0].update(mongo_collection="shared_task_records"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        pytest.param((with_app_wide_tasks, _digest(READ_TASKS)), HIGH_DIGEST, id="addresses_another_modules_app_wide"),
        pytest.param((_digest(READ_TASKS),), {}, id="addresses_another_modules_per_user"),
        pytest.param(
            (with_app_wide_tasks, _digest(READ_TASKS, own_collection=False)), HIGH_DIGEST,
            id="declares_nothing_and_addresses_app_wide",
        ),
        pytest.param(
            (with_app_wide_tasks, _digest(READ_OWN_DIGESTS), with_tasks_shared_with_digest), HIGH_DIGEST,
            id="shared_an_app_wide_collection",
        ),
        pytest.param(
            (_digest(READ_OWN_DIGESTS), with_tasks_shared_with_digest), {}, id="shared_a_per_user_collection",
        ),
        pytest.param((_digest(ALIAS.format(alias="billing.subscriptions")),), HIGH_DIGEST, id="addresses_a_data_alias"),
        pytest.param(
            (_digest(ALIAS.format(alias="billing.subscriptions"), gate=UPDATE_GATE),),
            _expect("medium", ("list_digest",), "task_digest"), id="addresses_a_data_alias_behind_a_paid_gate",
        ),
        pytest.param((_digest(ALIAS.format(alias="billing.unknown")),), {}, id="addresses_an_undeclared_alias"),
        pytest.param((_digest(LITERAL),), HIGH_DIGEST, id="addresses_a_literal_collection"),
        pytest.param((_digest(HANDLE_PASSED_ON),), HIGH_DIGEST, id="passes_a_persistence_handle_on"),
        pytest.param((_digest(HANDLE_ATTRIBUTE),), HIGH_DIGEST, id="reads_a_handle_attribute_outside_its_api"),
        pytest.param((_digest(DYNAMIC_IMPORT),), HIGH_DIGEST, id="imports_a_module_chosen_at_run_time"),
        pytest.param((with_app_wide_tasks, _digest(IMPORTED_SERVICE)), HIGH_DIGEST, id="imports_code_reaching_app_wide"),
        pytest.param(
            (with_app_wide_tasks, _digest(RELATIVE_IMPORT_ELSEWHERE)), HIGH_DIGEST,
            id="imports_another_modules_package_dynamically",
        ),
        pytest.param((_digest(IMPORTED_SERVICE),), {}, id="imports_code_reaching_per_user"),
        pytest.param((_digest(RAW_DRIVER),), HIGH_DIGEST, id="uses_a_raw_database_driver"),
        pytest.param((with_app_wide_tasks, _digest(OWN_MODULE_TASKS)), {}, id="pair_another_module_declares_is_refused"),
        pytest.param((_digest(ALIAS_FROM_PARAMETER),), HIGH_DIGEST, id="alias_from_a_parameter_reaches_every_alias"),
        pytest.param(
            (with_task_alias, _digest(ALIAS.format(alias="tasks.records"), own_collection=False)),
            _expect("medium", ("list_digest",), "task_digest"), id="alias_to_an_owned_collection_is_refused",
        ),
        pytest.param((with_app_wide_tasks, _digest(CONSTANT_MODULE)), HIGH_DIGEST, id="module_id_from_a_constant"),
        pytest.param((_digest(NAME_FROM_PARAMETER),), {}, id="name_from_a_parameter_bounded_per_user"),
        pytest.param(
            (with_app_wide_notes, _digest(NAME_FROM_PARAMETER)), HIGH_DIGEST,
            id="name_from_a_parameter_bounded_by_an_app_wide_collection",
        ),
        pytest.param(
            (with_app_wide_tasks, _digest(MODULE_FROM_PARAMETER)), HIGH_DIGEST,
            id="module_from_a_parameter_bounded_by_every_module",
        ),
        pytest.param((with_app_wide_tasks, _digest(APP_PACKAGE_IMPORT)), HIGH_DIGEST, id="imports_through_app_package"),
        pytest.param(
            (with_app_wide_tasks, _digest(READ_OWN_DIGESTS, extra={
                "modules/task_digest/backend/handler.py": HANDLER_IMPORTS_STORE,
                "modules/task_digest/backend/store.py": READ_TASKS,
            })), HIGH_DIGEST, id="reads_every_file_of_the_module",
        ),
        pytest.param(
            (with_alias_to_a_storage_name, _digest(ALIAS.format(alias="tasks.everything"))), HIGH_DIGEST,
            id="alias_to_an_undeclared_storage_name",
        ),
        pytest.param(
            (_digest(READ_OWN_DIGESTS), with_shared_literal_for_digest), HIGH_DIGEST,
            id="shared_an_undeclared_literal",
        ),
        pytest.param(
            (_digest(USES_LITERAL_HELPER, extra={"services/record_names.py": LITERAL_FROM_PARAMETER}),), HIGH_DIGEST,
            id="literal_name_from_a_parameter_in_imported_code",
        ),
        # Each way module code reaches records outside the forms the reading recognises.
        pytest.param(
            (with_app_wide_tasks, _digest(RUNTIME_PACKAGE_IMPORT)), HIGH_DIGEST, id="imports_by_runtime_package_name",
        ),
        pytest.param((_digest(RUNTIME_PACKAGE_IMPORT),), {}, id="runtime_package_import_bounded_per_user"),
        pytest.param(
            (with_app_wide_tasks, _digest(RUNTIME_PACKAGE_DYNAMIC_IMPORT)), HIGH_DIGEST,
            id="imports_by_runtime_package_name_dynamically",
        ),
        pytest.param((_digest(CONTEXT_NAMESPACE),), HIGH_DIGEST, id="reads_the_context_namespace"),
        pytest.param((_digest(CONTEXT_DICT),), HIGH_DIGEST, id="reads_the_context_dict"),
        pytest.param((_digest(LOADED_MODULE_TABLE),), HIGH_DIGEST, id="reads_the_loaded_module_table"),
        pytest.param((with_app_wide_tasks, _digest(MODULE_CONSTANT)), {}, id="constant_names_a_refused_pair"),
        pytest.param(
            (with_app_wide_tasks, _digest(CONSTANT_REBOUND_BY_GLOBALS)), HIGH_DIGEST, id="constant_rebound_by_globals",
        ),
        pytest.param(
            (with_app_wide_tasks, _digest(MODULE_CONSTANT, extra={
                "modules/task_digest/backend/setup.py": REBINDS_REPO_CONSTANT,
            })), HIGH_DIGEST, id="constant_rebound_by_setattr_on_a_module",
        ),
        pytest.param(
            (with_app_wide_tasks, _digest(MODULE_CONSTANT, extra={
                "modules/task_management/backend/patch.py": REBINDS_DIGEST_CONSTANT,
            })), HIGH_DIGEST, id="constant_rebound_from_code_the_module_does_not_import",
        ),
        pytest.param(
            (_digest(USES_DATABASE_ADAPTER, extra={"services/adapters/database/record_store.py": DATABASE_ADAPTER}),),
            HIGH_DIGEST, id="imports_a_database_adapter",
        ),
    ],
)
async def test_reach_includes_every_collection_module_code_addresses(security_build, changes, expected) -> None:
    files = _variant(with_task_permissions, *changes)
    assert await _permission_findings(security_build, files, module="task_digest") == expected


@pytest.mark.asyncio
async def test_findings_describe_the_caller_without_naming_a_bypass(security_build) -> None:
    findings = {
        item["finding_id"].rsplit(":", 1)[-1]: item
        for item in await _scan(security_build, _variant(with_app_wide_tasks))
    }
    assert findings["delete_task"]["title"] == "Signed-in action reaches shared records without a permission"
    assert findings["delete_task"]["evidence"] == {"path": MODULE}
    assert "per_user/per_workspace ownership" in findings["delete_task"]["recommendation"]
    assert findings["update_task"]["title"] == "Signed-in action reaches shared records restricted by plan only"
    assert findings["update_task"]["severity"] == "medium"
    nested = {item["finding_id"]: item for item in await _scan(security_build, _variant(with_app_wide_tasks, under_app_root))}
    assert nested["module_permissions:missing:task_management:delete_task"]["evidence"] == {"path": f"app/{MODULE}"}
    # A module without an id is named by its bundle path, under either root.
    unnamed = _variant(without_sign_in, under_app_root)
    _yaml(unnamed, f"app/{MODULE}", lambda module: module["module"].pop("id"))
    ids = {item["finding_id"] for item in await _scan(security_build, unnamed)}
    assert f"module_permissions:missing:app/{MODULE}:delete_task" in ids
    for _severity, title, description, recommendation in _PERMISSION_GAPS.values():
        text = f"{title} {description} {recommendation}".lower()
        assert not any(word in text for word in ("tenant", "workspace_id", "dev_user", "header", "query string"))


@pytest.mark.asyncio
async def test_contracts_outside_the_bound_root_and_malformed_modules_are_read_safely(security_build) -> None:
    """auth.yaml under app/ is not loaded when the bundle root holds app.json; malformed module shapes never raise."""
    build = security_build.add(_variant(with_decoy_app_auth))
    result = await invoke(inspect_generated_app_security, build.bridge)
    assert "auth_contract:missing_auth_yaml" in {item["finding_id"] for item in result["findings"]}
    for change in ({"module": "task_management"}, {"actions": 7}, {"permissions": True}):
        files = _variant()
        _yaml(files, MODULE, lambda module, change=change: module.update(change))
        result = await invoke(inspect_generated_app_security, security_build.add(files).bridge)
        assert result["success"] is True
    files = _variant()
    _yaml(files, MODULE, lambda module: _action(module, "list_tasks").update(permissions=5))
    assert await _permission_findings(security_build, files) == _expect("high", ("list_tasks",))


# --------------------------------------------------------------------------- code reading


@pytest.mark.parametrize(
    ("body", "unresolved"),
    [
        pytest.param("persistence = getattr(ctx, 'persistence', None)\nif persistence is None:\n    return None", False, id="assigned_and_tested"),
        pytest.param("return ctx.persistence.collection('m', 'c')", False, id="collection_call"),
        pytest.param("principal = getattr(ctx.persistence, 'principal', None)", False, id="getattr_of_api_attribute"),
        pytest.param("if not ctx.persistence or ctx.persistence.app_id != 'a':\n    return None", False, id="truth_test"),
        pytest.param("helper(ctx.persistence)", True, id="positional_argument"),
        pytest.param("helper(store=ctx.persistence)", True, id="keyword_argument"),
        pytest.param("handles = [ctx.persistence]", True, id="container"),
        pytest.param("handle = ctx.persistence or None", True, id="kept_boolean_operand"),
        pytest.param("handle = (lambda: ctx.persistence)()", True, id="lambda"),
        pytest.param("client = ctx.persistence.client", True, id="attribute_outside_api"),
        pytest.param("method = getattr(ctx.persistence, name)", True, id="dynamic_getattr"),
        pytest.param("factory = ctx.persistence.collection\nreturn use(factory)", True, id="bound_method_passed_on"),
        pytest.param("exec('ctx.persistence')", True, id="dynamic_code"),
        pytest.param("rebuilt = type(ctx.persistence)(app_id='a')", True, id="handle_type"),
        pytest.param("def _handle():\n    return ctx.persistence\nreturn _handle().collection('m', 'c')", False, id="helper_called"),
        pytest.param("def _handle():\n    return ctx.persistence\nuse(_handle)", True, id="helper_passed_on"),
        pytest.param("import importlib\nimportlib.import_module('.repo', __name__)", False, id="own_package_import"),
        pytest.param("import importlib\nimportlib.import_module('.repo', name)", True, id="package_chosen_at_run_time"),
        pytest.param("handles = {}\nhandles['p'] = ctx.persistence", True, id="kept_by_subscript"),
        pytest.param("hook = globals().get('before_create')\nreturn hook", False, id="global_read_by_constant_name"),
        pytest.param("hook = globals()['before_create']", False, id="global_subscript_by_constant_name"),
        pytest.param("hook = globals().get(name)", True, id="global_read_by_run_time_name"),
        pytest.param("globals().update(MODULE='other')", True, id="globals_written"),
        pytest.param("names = locals()", True, id="local_namespace"),
        pytest.param("values = vars(ctx)", True, id="object_namespace"),
        pytest.param("values = ctx.__dict__", True, id="object_dict"),
        pytest.param("import sys\nmodule = sys.modules[name]", True, id="loaded_module_table"),
        pytest.param("import importlib.util\nspec = importlib.util.find_spec(name)", True, id="import_loader"),
        pytest.param("from . import names\nsetattr(names, 'MODULE', 'other')", True, id="setattr_on_a_module"),
        pytest.param("from . import names\ndelattr(names, 'MODULE')", True, id="delattr_on_a_module"),
        pytest.param("from . import names\nnames.MODULE = 'other'", True, id="attribute_set_on_a_module"),
        pytest.param("setattr(self, 'cache', None)", False, id="setattr_on_self"),
        pytest.param("for attr in ('auth_token', 'user_id'):\n    value = getattr(ctx, attr, None)", False,
                     id="getattr_name_from_known_strings"),
        pytest.param("value = getattr(ctx, name, None)", True, id="getattr_name_at_run_time"),
        pytest.param("principal = ctx.persistence.principal\nvalue = getattr(principal, name, None)", False,
                     id="getattr_of_a_principal"),
        pytest.param("client = store._client", True, id="private_storage_of_another_object"),
        pytest.param("client = self._client", False, id="own_private_attribute"),
        pytest.param("from motor.motor_asyncio import AsyncIOMotorClient", True, id="database_driver"),
        pytest.param("import importlib\nimportlib.import_module('pymongo')", True, id="database_driver_imported_dynamically"),
        pytest.param("from mozaiksai.core.core_config import get_mongo_client", True, id="runtime_client"),
        pytest.param("code = compile(text, 'x', 'exec')", True, id="compiled_code"),
        pytest.param("import importlib\nsetattr(importlib.import_module('modules.m.names'), 'MODULE', 'x')", True,
                     id="setattr_on_a_dynamically_imported_module"),
        pytest.param("from . import names\nnames.__setattr__('MODULE', 'x')", True, id="module_setattr_method"),
        pytest.param("for attr in ('persistence',):\n    handle = getattr(ctx, attr)\n    helper(handle)", True,
                     id="getattr_known_name_holds_the_handle"),
        pytest.param("handles = list(map(getattr, [ctx], ['persistence']))", True, id="getattr_passed_as_a_value"),
        pytest.param("from .code import helpers\nhelpers.run()", False, id="relative_import_named_like_a_library"),
        pytest.param("import subprocess\nsubprocess.run(['task'])", True, id="other_process"),
        pytest.param("import importlib\nimportlib.import_module('mozaiksai.core.core_config')", True,
                     id="runtime_internals_imported_dynamically"),
        pytest.param("table = __builtins__", True, id="builtins_namespace"),
    ],
)
def test_reading_marks_untracked_persistence_use_unresolved(body: str, unresolved: bool) -> None:
    source = "async def action(ctx, name=None):\n" + "".join(f"    {line}\n" for line in body.splitlines())
    assert read_source(source).unresolved is unresolved


def test_reading_resolves_constant_arguments_and_bounds_the_rest() -> None:
    source = (
        "MODULE = 'task_management'\nPREFIX = 'ta'\nOTHER = 'notes'\n\n"
        "async def action(ctx, name):\n"
        "    ctx.persistence.collection(MODULE, PREFIX + 'sks')\n"
        "    ctx.persistence.collection(module_id=MODULE, collection_name=name)\n"
        "    ctx.persistence.collection(*[MODULE, 'tasks'])\n"
        "    for OTHER in ('a', 'b'):\n"
        "        ctx.persistence.collection('task_management', OTHER)\n"
        "    app_data_from_context(ctx).collection('billing.subscriptions')\n"
    )
    assert set(read_source(source).addresses) == {
        ("<persistence_collection>", ("task_management", "tasks")),
        ("<persistence_collection>", ("task_management", None)),
        ("<persistence_collection>", (None, None)),
        ("<app_data_collection>", ("billing.subscriptions",)),
    }


@pytest.mark.parametrize(
    ("call", "arguments"),
    [
        pytest.param("collection(module_id='a', collection_name='b')", ("a", "b"), id="keywords"),
        pytest.param("collection(collection_name='b', module_id='a')", ("a", "b"), id="keywords_reordered"),
        pytest.param("collection('a', *rest)", (None, None), id="starred_argument"),
        pytest.param("collection('a', **options)", (None, None), id="keyword_mapping"),
    ],
)
def test_reading_unpacked_arguments_bounds_every_argument(call: str, arguments: tuple[str | None, ...]) -> None:
    source = f"async def action(ctx, rest, options):\n    return ctx.persistence.{call}\n"
    assert read_source(source).addresses == (("<persistence_collection>", arguments),)


def test_reading_records_the_constants_each_argument_comes_from() -> None:
    reading = read_source("MODULE = 'a'\nNAME = 'b'\n\nasync def action(ctx):\n    ctx.persistence.collection(MODULE, 'x' + NAME)\n")
    assert reading.addresses == (("<persistence_collection>", ("a", "xb")),)
    assert reading.constants == ((frozenset({"MODULE"}), frozenset({"NAME"})),)


@pytest.mark.parametrize(
    ("source", "writes"),
    [
        pytest.param("from . import names\nnames.MODULE = 'a'\n", frozenset({"MODULE"}), id="attribute_set"),
        pytest.param("def f(record):\n    setattr(record, 'title', 'a')\n", frozenset({"title"}), id="setattr_constant"),
        pytest.param("def f(record, field):\n    setattr(record, field, 'a')\n", None, id="setattr_any_name"),
        pytest.param("class A:\n    def f(self):\n        self.cache = {}\n", frozenset(), id="own_attribute"),
        pytest.param("names = vars()\n", None, id="namespace"),
        pytest.param("def (\n", frozenset(), id="does_not_parse"),
    ],
)
def test_reading_records_what_a_source_may_rebind(source: str, writes: frozenset[str] | None) -> None:
    assert read_source(source).writes == writes


def test_reading_marks_source_it_cannot_parse_or_walk_unresolved() -> None:
    assert read_source("def (\n").unresolved is True
    long_constant = "LABELS = " + " + ".join(["'a'"] * 1200) + "\n"
    assert read_source(long_constant).unresolved is True


def test_bounded_resolution_widens_a_self_extending_chain() -> None:
    filler = "".join(f"def helper_{index}(value):\n    return value.field_{index}\n\n" for index in range(160))
    loop = filler + "def root(node):\n    while node.parent is not None:\n        node = node.parent\n    return node\n"
    resolver = PersistenceResolver(ast.parse(loop), bounded=True)
    assert not resolver.truncated
    assert len(resolver.aliases["node"]) <= 26
    assert read_source(loop).unresolved is False
    # A handle-derived chain, too many values for one name, and propagation that
    # does not settle are each cut short, and the source is unresolved.
    handles = "def walk(ctx):\n    node = ctx.persistence\n" + "    node = node.app_id\n" * 30 + "    return node\n"
    branching = "def walk(node):\n" + "".join(f"    node = node.side{index % 2}\n" for index in range(9))
    unsettled = "".join(f"value_{index + 1} = value_{index}\n" for index in reversed(range(70))) + "value_0 = ctx\n"
    for source in (handles, branching, unsettled):
        assert PersistenceResolver(ast.parse(source), bounded=True).truncated is True
        assert read_source(source).unresolved is True


# --------------------------------------------------------------------------- runtime (real Mongo)


APP_ID = "release-greenfield-value-target"


@pytest.fixture
async def mongo():
    from motor.motor_asyncio import AsyncIOMotorClient

    uri = os.environ.get("MONGO_URI")
    if not uri:
        if os.getenv("MOZAIKS_REQUIRE_REAL_MONGO"):
            pytest.fail("Real MongoDB is required")
        pytest.skip("MONGO_URI is not set")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=2000)
    name = f"security_protection_{uuid.uuid4().hex[:12]}"
    try:
        await client.admin.command("ping")
    except Exception as exc:
        client.close()
        pytest.fail(f"MONGO_URI is set but MongoDB is unreachable: {exc}")
    try:
        yield client, name
    finally:
        await client.drop_database(name)
        client.close()


class _App:
    """One composed bundle: HTTP client, its private database, and its signed-in users."""

    def __init__(self, http: Any, database: Any, contract: dict[str, Any] | None) -> None:
        self.http = http
        self.database = database
        self.contract = contract

    async def call(self, action: str, token: str | None = None, *, module_id: str = "task_management", **params: Any) -> Any:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        if action in {"get_tasks", "list_tasks"}:
            return await self.http.get(f"/api/modules/{module_id}/{action}", params=params, headers=headers)
        return await self.http.post(f"/api/modules/{module_id}/{action}", json={"params": params}, headers=headers)

    def tasks(self) -> Any:
        from mozaiksai.core.runtime.persistence import MongoPersistenceContext

        names = MongoPersistenceContext(
            app_id=APP_ID, database_name=self.database.name, client=self.database.client, data_contract=self.contract,
        )
        return self.database[names.collection_name("task_management", "tasks")]

    async def seed(self, task_id: str, **values: Any) -> None:
        now = datetime.now(UTC)
        await self.tasks().insert_one({
            "app_id": APP_ID, "task_id": task_id, "title": task_id, "status": "pending",
            "created_at": now, "updated_at": now, **values,
        })

    async def subscribe(self, user_id: str, plan_id: str) -> None:
        from mozaiksai.core.runtime.persistence.app_data import collection_name_for_alias

        assignments = collection_name_for_alias("billing.subscriptions", contract=self.contract or {})
        await self.database[assignments].insert_one(
            {"app_id": APP_ID, "user_id": user_id, "tenant_id": None, "plan_id": plan_id, "status": "active"}
        )


# token -> (user id, workspace id, extra scopes)
USERS = {
    "alice": ("alice", "ws-a", ()),
    "bob": ("bob", "ws-b", ()),
    "carol": ("carol", "ws-a", ()),
    "alice-viewer": ("alice", "ws-a", ("task_management.view",)),
}


async def _compose(tmp_path: Path, files: dict[str, str], mongo: tuple[Any, str], *, signed_in: bool) -> _App:
    """Compose the bundle's module router and executor, against a private database.

    Signed-in callers are principals injected through the ``optional_user``
    dependency, not validated tokens; the platform host's token adapter is not
    part of this composition.
    """
    from httpx import ASGITransport, AsyncClient

    from mozaiksai.core.audit.audit_logger import AuditLogger
    from mozaiksai.core.auth import optional_user
    from mozaiksai.core.auth.dependencies import UserPrincipal
    from mozaiksai.core.runtime.app.entitlements import ConfiguredEntitlementAdapter
    from mozaiksai.core.runtime.app.loader import AppLoader
    from mozaiksai.core.runtime.composition.executor_registry import ExecutorRegistry
    from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor
    from mozaiksai.core.runtime.composition.platform_hooks import PlatformHookRegistry
    from mozaiksai.core.runtime.persistence import (
        MongoPersistenceContext,
        PersistencePrincipal,
        apply_database_indexes,
    )
    from mozaiksai.core.runtime.persistence.app_data import collection_name_for_alias
    from mozaiksai.hosts.routers import modules as module_router

    client, database_name = mongo
    root = tmp_path / "app"
    _write(root, files)
    load = await AppLoader.load(str(root))
    assert not load.failed_module_names, load.module_load_errors
    contract = load.data_contract
    database = client[database_name]
    if contract:
        await apply_database_indexes(
            contract, app_id=APP_ID,
            persistence=MongoPersistenceContext(app_id=APP_ID, database_name=database_name, client=client),
        )

    class _NoAudit(AuditLogger):
        async def log(self, record: Any) -> None:
            return None

    async def emit(*_args: Any, **_kwargs: Any) -> None:
        return None

    hooks = PlatformHookRegistry()
    executor = ModuleExecutor(
        event_emitter=emit,
        entitlement_checker=(
            ConfiguredEntitlementAdapter(
                config=load.subscriptions_config,
                collection_resolver=lambda alias: database[collection_name_for_alias(alias, contract=contract or {})],
            )
            if load.subscriptions_config is not None else None
        ),
        data_contract=contract,
        platform_hooks=hooks,
        audit_logger=_NoAudit(),
        persistence_database=database_name,
        persistence_client=client,
    )
    for loaded_module in load.modules:
        executor.register_loaded_module(loaded_module)
    registry = ExecutorRegistry()
    registry.register(executor)

    app = FastAPI()
    app.state.executor_registry = registry
    app.state.module_action_surfaces = {module.name: module.action_api_surface_map for module in load.modules}
    app.state.failed_module_names = []
    app.include_router(module_router.router)

    if signed_in:
        def principal(user_id: str, workspace_id: str, scopes: tuple[str, ...]) -> UserPrincipal:
            return UserPrincipal(
                user_id=user_id, email=None, name=user_id, roles=[],
                scopes=["openid", "profile", "email", *scopes],
                raw_claims={"sub": user_id, "app_id": APP_ID}, provider="action_protection_test",
                app_id=APP_ID, workspace_id=workspace_id, auth_provenance="token_validated",
            )

        users = {token: principal(*identity) for token, identity in USERS.items()}

        async def resolve_principal(request: Request) -> UserPrincipal | None:
            header = request.headers.get("authorization") or ""
            return users.get(header.removeprefix("Bearer ").strip())

        app.dependency_overrides[optional_user] = resolve_principal
        environment = module_router.ModuleDispatchEnvironment(
            authentication_enabled=True,
            platform_hooks=hooks,
            record_invocation=lambda **_: None,
            persistence_principal=lambda user: (
                PersistencePrincipal(user_id=user.user_id, workspace_id=user.workspace_id) if user else None
            ),
        )
    else:
        # The real no-auth identity path: optional_user and the persistence
        # principal resolve exactly as the platform host does without sign-in.
        from mozaiksai.core.auth.adapters.registry import is_auth_enabled

        assert is_auth_enabled() is False
        environment = module_router.ModuleDispatchEnvironment(
            authentication_enabled=False,
            platform_hooks=hooks,
            record_invocation=lambda **_: None,
            persistence_principal=PersistencePrincipal.from_authenticated_user,
        )
    app.dependency_overrides[module_router.module_dispatch_environment] = lambda: environment
    return _App(AsyncClient(transport=ASGITransport(app=app), base_url="http://action-protection"), database, contract)


def _task_id(response: Any) -> str:
    assert response.status_code == 200, response.text
    return response.json()["item"]["task_id"]


@pytest.mark.asyncio
async def test_runtime_recorded_bundle_protects_every_permissionless_action(tmp_path, mongo, security_build) -> None:
    files = _variant()
    app = await _compose(tmp_path, files, mongo, signed_in=True)

    assert (await app.call("list_tasks")).status_code == 401
    assert (await app.call("create_task", title="anonymous", status="pending")).status_code == 401
    alice_task = _task_id(await app.call("create_task", "alice", title="alice's", status="pending"))

    listed = (await app.call("list_tasks", "bob")).json()
    assert listed == {"items": [], "total": 0}
    assert (await app.call("get_tasks", "bob", id=alice_task)).status_code == 404
    assert (await app.call("delete_task", "bob", task_id=alice_task)).status_code == 404
    assert (await app.call("update_task", "alice", task_id=alice_task, title="free plan")).status_code == 402
    await app.subscribe("bob", "pro")
    assert (await app.call("update_task", "bob", task_id=alice_task, title="bob's edit")).status_code == 404
    await app.subscribe("alice", "pro")
    assert (await app.call("update_task", "alice", task_id=alice_task, title="pro plan")).status_code == 200
    stored = await app.tasks().find_one({"task_id": alice_task})
    assert (stored["user_id"], stored["title"]) == ("alice", "pro plan")

    assert await _permission_findings(security_build, files) == {}


@pytest.mark.asyncio
async def test_runtime_t041_paid_summary_counts_only_the_callers_records(tmp_path, mongo, security_build) -> None:
    files = _variant(as_t041)
    app = await _compose(tmp_path, files, mongo, signed_in=True)
    _task_id(await app.call("create_task", "alice", title="alice's", status="pending"))
    _task_id(await app.call("create_task", "bob", title="bob's", status="completed"))

    assert (await app.call("summarize_tasks")).status_code == 401
    assert (await app.call("summarize_tasks", "alice")).status_code == 402
    await app.subscribe("alice", "pro")
    summary = await app.call("summarize_tasks", "alice")
    assert summary.status_code == 200, summary.text
    assert summary.json() == {"active_count": 1, "completed_count": 0}

    assert await _permission_findings(security_build, files) == {}


@pytest.mark.asyncio
async def test_runtime_per_workspace_records_are_shared_inside_one_workspace_only(
    tmp_path, mongo, security_build,
) -> None:
    files = _variant(with_workspace_tasks)
    app = await _compose(tmp_path, files, mongo, signed_in=True)
    await app.seed("task-a", workspace_id="ws-a", user_id="alice")

    assert [item["task_id"] for item in (await app.call("list_tasks", "carol")).json()["items"]] == ["task-a"]
    assert (await app.call("get_tasks", "carol", id="task-a")).status_code == 200
    assert (await app.call("list_tasks", "bob")).json() == {"items": [], "total": 0}
    assert (await app.call("get_tasks", "bob", id="task-a")).status_code == 404
    assert (await app.call("delete_task", "bob", task_id="task-a")).status_code == 404
    assert await app.tasks().find_one({"task_id": "task-a"}) is not None

    assert await _permission_findings(security_build, files) == {}


@pytest.mark.asyncio
async def test_runtime_app_wide_records_are_reachable_by_any_signed_in_user(tmp_path, mongo, security_build) -> None:
    files = _variant(with_app_wide_tasks)
    app = await _compose(tmp_path, files, mongo, signed_in=True)
    for task_id in ("task-a", "task-b"):
        await app.seed(task_id, user_id="alice")

    assert (await app.call("list_tasks")).status_code == 401
    listed = (await app.call("list_tasks", "bob")).json()
    assert {item["task_id"] for item in listed["items"]} == {"task-a", "task-b"}
    assert (await app.call("get_tasks", "bob", id="task-a")).status_code == 200
    assert (await app.call("delete_task", "bob", task_id="task-a")).status_code == 200
    assert await app.tasks().find_one({"task_id": "task-a"}) is None
    # The gate admits callers by plan; a plan holder changes another user's shared record.
    assert (await app.call("update_task", "bob", task_id="task-b", title="free plan")).status_code == 402
    await app.subscribe("bob", "pro")
    assert (await app.call("update_task", "bob", task_id="task-b", title="pro plan")).status_code == 200
    stored = await app.tasks().find_one({"task_id": "task-b"})
    assert (stored["user_id"], stored["title"]) == ("alice", "pro plan")

    assert await _permission_findings(security_build, files) == PLAN_ONLY_UPDATE


@pytest.mark.asyncio
async def test_runtime_module_reaches_collections_another_module_declares(tmp_path, mongo, security_build) -> None:
    files = _variant(with_task_permissions, with_app_wide_tasks, _digest(READ_TASKS))
    app = await _compose(tmp_path, files, mongo, signed_in=True)
    await app.seed("task-a", user_id="alice")

    assert (await app.call("list_tasks", "bob")).status_code == 403
    digest = await app.call("list_digest", "bob", module_id="task_digest")
    assert digest.status_code == 200, digest.text
    assert digest.json()["items"] == [{"task_id": "task-a", "user_id": "alice"}]

    assert await _permission_findings(security_build, files) == HIGH_DIGEST


@pytest.mark.asyncio
async def test_runtime_contract_refuses_collections_it_does_not_declare(tmp_path, mongo, security_build) -> None:
    files = _variant(without_task_collections)
    app = await _compose(tmp_path, files, mongo, signed_in=True)

    assert (await app.call("create_task", "alice", title="alice's", status="pending")).status_code == 403
    assert (await app.call("list_tasks", "alice")).status_code == 403

    assert await _permission_findings(security_build, files) == _expect("medium", UNGATED)


@pytest.mark.asyncio
async def test_runtime_without_sign_in_neither_ownership_nor_gate_separates_callers(
    tmp_path, mongo, security_build, monkeypatch,
) -> None:
    for name in ("AUTH_PROVIDER", "SUPABASE_URL", "KEYCLOAK_URL", "AUTH_JWKS_URL", "AUTH_ISSUER",
                 "MOZAIKS_OIDC_AUTHORITY", "MOZAIKS_OIDC_DISCOVERY_URL", "ENVIRONMENT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("ENV", "test")
    files = _variant(without_sign_in)
    app = await _compose(tmp_path, files, mongo, signed_in=False)

    # Without authentication every caller shares one unverified identity.
    first = _task_id(await app.call("create_task", title="first caller", status="pending"))
    listed = (await app.call("list_tasks")).json()
    assert [item["task_id"] for item in listed["items"]] == [first]
    # No plan was ever assigned: the update gate is not consulted without sign-in.
    assert (await app.call("update_task", task_id=first, title="second caller")).status_code == 200
    assert (await app.call("delete_task", task_id=first)).status_code == 200

    assert await _permission_findings(security_build, files) == _expect("high", TASK_ACTIONS)


@pytest.mark.asyncio
async def test_runtime_declared_permission_and_internal_surface_protect_actions(
    tmp_path, mongo, security_build,
) -> None:
    files = _variant(with_declared_permission_and_internal_read)
    app = await _compose(tmp_path, files, mongo, signed_in=True)
    alice_task = _task_id(await app.call("create_task", "alice", title="alice's", status="pending"))

    assert (await app.call("list_tasks", "alice")).status_code == 403
    assert (await app.call("list_tasks", "alice-viewer")).json()["total"] == 1
    for token in (None, "alice", "alice-viewer"):
        assert (await app.call("get_tasks", token, id=alice_task)).status_code == 404

    assert await _permission_findings(security_build, files) == {}
