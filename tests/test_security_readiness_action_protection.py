"""SecurityReadiness reports a permissionless action exactly when nothing protects it.

Every case changes one declared contract of the recorded fdfa818e bundle. The
scanner tests assert which actions are reported and at what severity. The
real-Mongo tests dispatch the same bundle variants through the module router
and executor with two users and assert what each caller can reach, so the
scanner and the runtime are checked against one decision table
(docs/architecture/app/generated-action-protection.md).
"""
from __future__ import annotations

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

from factory_app.workflows.SecurityReadiness.tools.inspect_generated_app_security import (
    inspect_generated_app_security,
)
from tests.test_security_readiness_target_binding import (
    invoke,
    security_build_fixture,  # noqa: F401
)

FIXTURE = Path(__file__).parent / "fixtures" / "runtime_smoke_good_bundle_fdfa818e.json"
MODULE = "modules/task_management/module.yaml"
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


def _tasks_collection(contract: dict[str, Any]) -> dict[str, Any]:
    surface = next(item for item in contract["surfaces"] if item["surface_id"] == "task_management")
    return surface["collections"][0]


def _plan(plans: dict[str, Any], plan_id: str) -> dict[str, Any]:
    return next(plan for plan in plans["plans"] if plan["plan_id"] == plan_id)


# --------------------------------------------------------------------------- bundle variants


def with_paid_summary(files: dict[str, str]) -> None:
    """The T041 shape: a sixth, paid, owner-scoped action whose gate only Pro grants."""

    def add_summary(module: dict[str, Any]) -> None:
        summary = dict(_action(module, "list_tasks"))
        summary.update(id="summarize_tasks", handler_method="summarize_tasks", entitlement_gate=SUMMARY_GATE)
        module["actions"].append(summary)

    _yaml(files, MODULE, add_summary)
    _yaml(files, PLANS, lambda plans: _plan(plans, "pro")["capabilities"].append(SUMMARY_GATE))


def without_sign_in(files: dict[str, str]) -> None:
    _json(files, "app.json", lambda app: app.update(authRequired=False))
    files.pop("config/auth.yaml")


def with_invalid_auth_contract(files: dict[str, str]) -> None:
    _yaml(files, "config/auth.yaml", lambda auth: auth.pop("strategy"))


def with_app_wide_tasks(files: dict[str, str]) -> None:
    _json(files, CONTRACT, lambda contract: _tasks_collection(contract).update(tenancy="app_wide", owner_field=None))


def with_default_plan_update(files: dict[str, str]) -> None:
    _yaml(files, PLANS, lambda plans: _plan(plans, "free")["capabilities"].append(UPDATE_GATE))


def without_subscriptions(files: dict[str, str]) -> None:
    files.pop(PLANS)


def without_data_contract(files: dict[str, str]) -> None:
    files.pop(CONTRACT)


def without_task_data(files: dict[str, str]) -> None:
    def drop_tasks(contract: dict[str, Any]) -> None:
        contract["surfaces"] = [item for item in contract["surfaces"] if item["surface_id"] != "task_management"]

    _json(files, CONTRACT, drop_tasks)
    files.pop("modules/task_management/backend/repo.py")


def with_operator_list(files: dict[str, str]) -> None:
    _yaml(files, MODULE, lambda module: _action(module, "list_tasks").update(api_surface="admin_internal"))


def with_blank_surface(files: dict[str, str]) -> None:
    _yaml(files, MODULE, lambda module: _action(module, "list_tasks").update(api_surface=""))


def with_declared_permission_and_internal_read(files: dict[str, str]) -> None:
    def change(module: dict[str, Any]) -> None:
        module["permissions"].append({"id": "task_management.view", "description": "View tasks."})
        _action(module, "list_tasks")["permissions"] = ["task_management.view"]
        _action(module, "get_tasks").update(api_surface="internal", permissions=[])

    _yaml(files, MODULE, change)


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


async def _permission_findings(security_build, files: dict[str, str]) -> dict[str, str]:
    build = security_build.add(files)
    result = await invoke(inspect_generated_app_security, build.bridge)
    assert result["success"] is True
    return {
        item["finding_id"].removeprefix("module_permissions:missing:"): item["severity"]
        for item in result["findings"]
        if item["finding_id"].startswith("module_permissions:")
    }


def _expect(severity: str, actions: tuple[str, ...]) -> dict[str, str]:
    return {f"task_management:{action}": severity for action in actions}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        pytest.param((), id="recorded_owner_scoped_crud"),
        pytest.param((with_paid_summary,), id="t041_paid_owner_scoped_summary"),
        pytest.param((with_declared_permission_and_internal_read,), id="declared_permission_and_internal_read"),
    ],
)
async def test_protected_actions_raise_no_permission_finding(security_build, changes) -> None:
    assert await _permission_findings(security_build, _variant(*changes)) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        pytest.param((without_sign_in,), _expect("high", TASK_ACTIONS), id="no_sign_in"),
        pytest.param((with_invalid_auth_contract,), _expect("high", TASK_ACTIONS), id="invalid_auth_contract"),
        pytest.param((with_app_wide_tasks,), _expect("high", UNGATED), id="app_wide_without_gate"),
        pytest.param(
            (with_app_wide_tasks, with_default_plan_update), _expect("high", TASK_ACTIONS),
            id="app_wide_gate_granted_by_default_plan",
        ),
        pytest.param(
            (with_app_wide_tasks, without_subscriptions), _expect("high", TASK_ACTIONS),
            id="app_wide_gate_without_plan_catalog",
        ),
        pytest.param((without_data_contract,), _expect("high", UNGATED), id="persistence_without_data_contract"),
        pytest.param((without_task_data,), _expect("medium", UNGATED), id="no_declared_data_scope"),
        pytest.param((with_operator_list,), _expect("high", ("list_tasks",)), id="operator_action_without_permission"),
        pytest.param((with_blank_surface,), _expect("high", ("list_tasks",)), id="blank_surface"),
    ],
)
async def test_unprotected_actions_raise_a_finding_at_their_severity(security_build, changes, expected) -> None:
    assert await _permission_findings(security_build, _variant(*changes)) == expected


@pytest.mark.asyncio
async def test_unprotected_finding_describes_the_caller_without_naming_a_bypass(security_build) -> None:
    build = security_build.add(_variant(with_app_wide_tasks))
    result = await invoke(inspect_generated_app_security, build.bridge)
    finding = next(
        item for item in result["findings"]
        if item["finding_id"] == "module_permissions:missing:task_management:delete_task"
    )
    assert finding["title"] == "Signed-in action reaches shared records without a permission"
    assert finding["evidence"] == {"path": MODULE}
    assert "per_user/per_workspace ownership" in finding["recommendation"]


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

    async def call(self, action: str, token: str | None = None, **params: Any) -> Any:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        if action in {"get_tasks", "list_tasks"}:
            return await self.http.get(f"/api/modules/task_management/{action}", params=params, headers=headers)
        return await self.http.post(
            f"/api/modules/task_management/{action}", json={"params": params}, headers=headers,
        )

    def tasks(self) -> Any:
        from mozaiksai.core.runtime.persistence import MongoPersistenceContext

        names = MongoPersistenceContext(
            app_id=APP_ID, database_name=self.database.name, client=self.database.client, data_contract=self.contract,
        )
        return self.database[names.collection_name("task_management", "tasks")]

    async def subscribe(self, user_id: str, plan_id: str) -> None:
        from mozaiksai.core.runtime.persistence.app_data import collection_name_for_alias

        assignments = collection_name_for_alias("billing.subscriptions", contract=self.contract or {})
        await self.database[assignments].insert_one(
            {"app_id": APP_ID, "user_id": user_id, "tenant_id": None, "plan_id": plan_id, "status": "active"}
        )


async def _compose(tmp_path: Path, files: dict[str, str], mongo: tuple[Any, str], *, signed_in: bool) -> _App:
    """Compose the bundle the way the platform host does, against a private database."""
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
        def principal(user_id: str, *scopes: str) -> UserPrincipal:
            return UserPrincipal(
                user_id=user_id, email=None, name=user_id, roles=[],
                scopes=["openid", "profile", "email", *scopes],
                raw_claims={"sub": user_id, "app_id": APP_ID}, provider="action_protection_test",
                app_id=APP_ID, workspace_id=None, auth_provenance="token_validated",
            )

        users = {
            "alice": principal("alice"),
            "bob": principal("bob"),
            "alice-viewer": principal("alice", "task_management.view"),
        }

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
async def test_runtime_app_wide_records_are_reachable_by_any_signed_in_user(tmp_path, mongo, security_build) -> None:
    files = _variant(with_app_wide_tasks)
    app = await _compose(tmp_path, files, mongo, signed_in=True)
    now = datetime.now(UTC)
    for task_id in ("task-a", "task-b"):
        await app.tasks().insert_one({
            "app_id": APP_ID, "task_id": task_id, "title": "alice's", "status": "pending",
            "created_at": now, "updated_at": now, "user_id": "alice",
        })

    assert (await app.call("list_tasks")).status_code == 401
    listed = (await app.call("list_tasks", "bob")).json()
    assert {item["task_id"] for item in listed["items"]} == {"task-a", "task-b"}
    assert (await app.call("get_tasks", "bob", id="task-a")).status_code == 200
    assert (await app.call("delete_task", "bob", task_id="task-a")).status_code == 200
    assert await app.tasks().find_one({"task_id": "task-a"}) is None
    # The plan-restricted gate is the only thing between a signed-in user and the shared update.
    assert (await app.call("update_task", "bob", task_id="task-b", title="free plan")).status_code == 402
    await app.subscribe("bob", "pro")
    assert (await app.call("update_task", "bob", task_id="task-b", title="pro plan")).status_code == 200

    assert await _permission_findings(security_build, files) == _expect("high", UNGATED)


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
