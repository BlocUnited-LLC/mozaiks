"""Runtime smoke gate over recorded generated bundles.

The dead bundle is the recorded 93a7 replay that passed every static gate of
its day while it could not start, crashed on writes and denied paying users.
The good bundle is the recorded fdfa818e run replayed through AppGenerator at
c8b9ea2e. Both run against a real Mongo (MONGO_URI); nothing about the gate is
mocked except where a test says so, and those seams are the host process
collaborators the gate must never reach.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from factory_app.workflows.AppGenerator.tools import app_runtime_smoke
from factory_app.workflows.AppGenerator.tools.app_runtime_smoke import run_app_runtime_smoke
from factory_app.workflows.AppGenerator.tools.app_validation import run_app_bundle_acceptance_gate

FIXTURES = Path(__file__).parent / "fixtures"
SMOKE_PREFIX = "mozaiks_runtime_smoke_"


def _bundle(name: str) -> dict[str, str]:
    return dict(json.loads((FIXTURES / name).read_text(encoding="utf-8"))["files"])


def _dead() -> dict[str, str]:
    return _bundle("runtime_smoke_dead_bundle_93a7.json")


def _good() -> dict[str, str]:
    return _bundle("runtime_smoke_good_bundle_fdfa818e.json")


def _by_check(result: dict) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for row in result["results"]:
        rows.setdefault(row["check"], row)
    return rows


@pytest.fixture
async def mongo_client():
    from motor.motor_asyncio import AsyncIOMotorClient

    uri = os.environ.get("MONGO_URI")
    if not uri:
        if os.getenv("MOZAIKS_REQUIRE_REAL_MONGO"):
            pytest.fail("Real MongoDB is required")
        pytest.skip("MONGO_URI is not set")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=2000)
    try:
        await client.admin.command("ping")
    except Exception:
        client.close()
        if os.getenv("MOZAIKS_REQUIRE_REAL_MONGO"):
            pytest.fail("Real MongoDB is required")
        pytest.skip("MongoDB is unavailable")
    before = set(await client.list_database_names())
    yield client
    left = sorted(name for name in set(await client.list_database_names()) - before if name.startswith(SMOKE_PREFIX))
    for name in left:
        await client.drop_database(name)
    client.close()
    assert not left, f"runtime smoke left databases behind: {left}"


# --------------------------------------------------------------------------- no database


async def test_no_database_is_reported_as_skipped_never_as_a_pass():
    result = await run_app_runtime_smoke(_good(), mongo_client=None)

    assert result["status"] == "skipped"
    assert result["passed"] is False
    assert result["skipped_reason"] == "no database configured"
    assert result["failed_tests"] == []
    assert result["checks"] == [{
        "id": "app_runtime_smoke",
        "passed": False,
        "message": "Runtime smoke skipped: no database configured. Boot, two-user CRUD and entitlement checks did not run.",
        "details": {"status": "skipped", "skipped_reason": "no database configured", "blocking": False},
    }]


async def test_unreachable_database_is_skipped_with_the_driver_error():
    class _Admin:
        async def command(self, name):
            raise ConnectionRefusedError("connection refused")

    result = await run_app_runtime_smoke(_good(), mongo_client=SimpleNamespace(admin=_Admin()))

    assert result["status"] == "skipped"
    assert result["passed"] is False
    assert result["skipped_reason"] == "database unreachable (ConnectionRefusedError: connection refused)"


async def test_acceptance_gate_keeps_a_skipped_smoke_out_of_completed_and_failed():
    # The suite-wide conftest default resolves no smoke database.
    result = await run_app_bundle_acceptance_gate(files=_good())

    smoke = result["app_runtime_smoke"]
    assert smoke["status"] == "skipped"
    assert result["validation_evidence"]["skipped"] == ["app_runtime_smoke"]
    assert "app_runtime_smoke" not in result["validation_evidence"]["completed"]
    assert "app_runtime_smoke" not in result["validation_evidence"]["failed"]
    assert result["checks"][-1]["id"] == "app_runtime_smoke"
    assert result["checks"][-1]["passed"] is False
    assert "app_runtime_smoke: Runtime smoke skipped: no database configured." in " ".join(result["warnings"])


# --------------------------------------------------------------------------- recorded bundles, real Mongo


async def test_recorded_good_bundle_boots_and_passes_every_runtime_check(mongo_client):
    result = await run_app_runtime_smoke(_good(), mongo_client=mongo_client)

    assert result["status"] == "passed", json.dumps(result["failed_tests"], indent=1)
    checks = _by_check(result)
    assert {row["status"] for row in checks.values()} == {"passed"}
    tag = "crud.task_management.tasks"
    assert set(checks) == {
        "boot.app_load", "boot.indexes",
        f"{tag}.a_create", f"{tag}.a_list", f"{tag}.get_missing", f"{tag}.a_get",
        f"{tag}.b_list_isolated", f"{tag}.b_get_isolated", f"{tag}.b_update_denied", f"{tag}.b_delete_denied",
        f"{tag}.a_update", f"{tag}.a_delete",
        "entitlement.task_management.update_task.default_plan_denied",
        "entitlement.task_management.update_task.entitled_allowed",
    }
    assert checks[f"{tag}.b_update_denied"]["message"] == "B's update of A's record is refused (404); the record is unchanged."
    assert checks["entitlement.task_management.update_task.default_plan_denied"]["message"].endswith("denied with 402.")
    assert checks["entitlement.task_management.update_task.entitled_allowed"]["message"].endswith("allowed (200).")
    assert result["events_emitted"] == ["domain.task.created", "domain.task.deleted", "domain.task.updated"]


async def test_recorded_dead_bundle_fails_for_its_known_runtime_reasons(mongo_client):
    result = await run_app_runtime_smoke(_dead(), mongo_client=mongo_client)

    assert result["status"] == "failed"
    checks = _by_check(result)
    failed = {check: row for check, row in checks.items() if row["status"] == "failed"}
    tag = "crud.task_management.tasks"
    expected = {
        "boot.indexes": ("data_contract collection task_management.tasks.indexes[0].name is required", "data/contract.json"),
        "boot.migrations": ("001_billing_portal_collections.json.version or", "data/migrations/*.json"),
        "subscriptions.assignment_store": (
            "assignment_store.data_alias 'billing.subscriptions' is not declared in data/contract.json aliases",
            "data/contract.json",
        ),
        "permission.task_management.create_task": (
            "requires permissions ['task.create'] that no signed-in user holds", "modules/task_management/module.yaml",
        ),
        "permission.task_management.update_task": (
            "requires permissions ['task.update'] that no signed-in user holds", "modules/task_management/module.yaml",
        ),
        f"{tag}.a_create": (
            "AttributeError: 'dict' object has no attribute 'title' (at modules/task_management/backend/repo.py:",
            "modules/task_management/backend/repo.py",
        ),
        f"{tag}.get_missing": (
            "LookupError: Record not found (at modules/task_management/backend/repo.py:",
            "modules/task_management/backend/repo.py",
        ),
        "entitlement.task_management.summarize_tasks.entitled_allowed": (
            "got 402 ENTITLEMENT_REQUIRED", "config/subscriptions.yaml",
        ),
        "entitlement.task_management.update_task.entitled_allowed": (
            "got 402 ENTITLEMENT_REQUIRED", "config/subscriptions.yaml",
        ),
    }
    assert set(failed) == set(expected)
    for check, (fragment, path) in expected.items():
        assert fragment in failed[check]["message"], (check, failed[check]["message"])
        assert failed[check]["path"] == path, (check, failed[check]["path"])
    # Nothing that needs A's record pretends to pass without one.
    for step in ("a_get", "b_list_isolated", "b_get_isolated", "b_update_denied", "b_delete_denied", "a_update", "a_delete"):
        assert checks[f"{tag}.{step}"]["status"] == "not_run"
    assert all(item["error"] and "Not run" not in item["error"] for item in result["failed_tests"])
    assert len(result["failed_tests"]) == len(expected)


async def test_dead_bundle_write_path_reports_its_wrong_id_and_owner_fields(mongo_client):
    files = _dead()
    # The replay3 repair of the TypedDict crash, so the write path behind it is reached.
    key = "modules/task_management/backend/schemas.py"
    source = files[key].replace(
        "from typing import TypedDict, Optional",
        "from typing import TypedDict, Optional\n\n\nclass _AttrRequest(dict):\n"
        "    def __getattr__(self, key):\n        return self.get(key)\n",
    )
    source = source.replace("class TaskCreateRequest(TypedDict):", "class TaskCreateRequest(_AttrRequest):")
    files[key] = source.replace("class TaskUpdateRequest(TypedDict):", "class TaskUpdateRequest(_AttrRequest):")

    result = await run_app_runtime_smoke(files, mongo_client=mongo_client)

    create = _by_check(result)["crud.task_management.tasks.a_create"]
    assert create["status"] == "failed"
    assert "but no stored task_management.tasks record has that task_id" in create["message"]
    assert "'owner_id'" in create["message"]
    assert "id field 'task_id' and owner field 'user_id'" in create["message"]
    assert create["path"] == "modules/task_management/backend/repo.py"


async def test_smoke_never_reaches_host_hooks_audit_log_or_usage_metering(mongo_client, monkeypatch):
    from mozaiksai.core.runtime.composition import module_executor
    from mozaiksai.core.runtime.composition.platform_hooks import PlatformHookRegistry
    from mozaiksai.hosts.routers import modules as module_router

    used: list[str] = []
    host_hooks = PlatformHookRegistry()
    host_hooks._loaded = True

    def host_policy(*args, **kwargs):
        used.append("host platform hook")
        return False

    host_hooks._before_module_execution_hooks.append(host_policy)
    host_hooks._module_scope_resolver_hooks.append(host_policy)
    host_hooks._module_dispatch_audit_hooks.append(host_policy)
    monkeypatch.setattr(PlatformHookRegistry, "_instance", host_hooks)

    def host_collaborator(*args, **kwargs):
        used.append("host audit log or usage metering")
        raise AssertionError("host collaborator")

    monkeypatch.setattr(module_executor, "get_audit_logger", host_collaborator)
    monkeypatch.setattr(module_router, "record_action_invocation", host_collaborator)

    result = await run_app_runtime_smoke(_good(), mongo_client=mongo_client)

    assert used == []
    assert result["status"] == "passed", json.dumps(result["failed_tests"], indent=1)


async def test_migration_history_stays_in_the_disposable_database(mongo_client):
    from mozaiksai.core.runtime.persistence.migrations import (
        APP_DATA_MIGRATIONS_COLLECTION,
        SYSTEM_DATABASE,
    )

    files = _good()
    app_id = json.loads(files["data/contract.json"])["app_id"]
    files["data/migrations/001_task_indexes.json"] = json.dumps({
        "migration_id": "smoke_history_probe",
        "version": "1",
        "description": "Add a lookup index",
        "operations": [{
            "type": "ensure_index", "module_id": "task_management", "entity_name": "tasks",
            "index": {"name": "tasks_title_idx", "keys": [["title", 1]]},
        }],
    })
    history = mongo_client[SYSTEM_DATABASE][APP_DATA_MIGRATIONS_COLLECTION]
    before = await history.count_documents({"app_id": app_id, "migration_id": "smoke_history_probe"})

    result = await run_app_runtime_smoke(files, mongo_client=mongo_client)

    assert _by_check(result)["boot.migrations"] == {
        "check": "boot.migrations", "status": "passed", "message": "1 data migration(s) applied.", "path": None,
    }
    assert await history.count_documents({"app_id": app_id, "migration_id": "smoke_history_probe"}) == before


async def test_acceptance_gate_fails_the_dead_bundle_and_routes_smoke_diagnostics_to_repair(mongo_client, monkeypatch):
    monkeypatch.setattr(app_runtime_smoke, "resolve_smoke_mongo_client", lambda: mongo_client)

    result = await run_app_bundle_acceptance_gate(files=_dead())

    assert "app_runtime_smoke" in result["validation_evidence"]["failed"]
    assert result["app_runtime_smoke"]["status"] == "failed"
    smoke_errors = [error for error in result["bundle_repair"]["errors"] if error.startswith("app_runtime_smoke: ")]
    assert len(smoke_errors) == len(result["app_runtime_smoke"]["failed_tests"])
    assert any("indexes[0].name is required" in error for error in smoke_errors)


async def test_acceptance_gate_passes_the_smoke_for_the_good_bundle(mongo_client, monkeypatch):
    monkeypatch.setattr(app_runtime_smoke, "resolve_smoke_mongo_client", lambda: mongo_client)

    result = await run_app_bundle_acceptance_gate(files=_good())

    assert result["app_runtime_smoke"]["status"] == "passed"
    assert "app_runtime_smoke" in result["validation_evidence"]["completed"]
    assert result["validation_evidence"]["skipped"] == []
