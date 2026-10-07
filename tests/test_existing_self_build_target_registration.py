"""A loaded app can claim its Factory target only through enforced host authority."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from factory_app.app.modules.app_registry.backend.repo import AppRegistryRepo
from mozaiksai.core.runtime.composition.module_authority import (
    ModuleDispatchAudit,
    ModuleDispatchAuthority,
    ModulePermissionCheck,
)
from mozaiksai.core.runtime.composition.module_context import ModuleContext
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor, ModuleRequest
from mozaiksai.hosts import build_target_registration as registration
from mozaiksai.hosts import platform


class _Collection:
    def __init__(self) -> None:
        self.doc = None
        self.lock = asyncio.Lock()

    async def find_one_and_update(self, query, update, **kwargs):  # noqa: ANN001, ANN003
        assert query == {"app_id": "existing-app"}
        assert set(update) == {"$setOnInsert"}
        assert kwargs["upsert"] is True
        async with self.lock:
            if self.doc is None:
                self.doc = deepcopy(update["$setOnInsert"])
            return deepcopy(self.doc)

    async def find_one(self, query):  # noqa: ANN001
        return deepcopy(self.doc) if self.doc and self.doc["app_id"] == query["app_id"] else None


@pytest.mark.asyncio
async def test_insert_once_keeps_lifecycle_and_current_build_unchanged(monkeypatch) -> None:
    collection = _Collection()
    repo = AppRegistryRepo.__new__(AppRegistryRepo)
    monkeypatch.setattr(repo, "ensure_indexes", AsyncMock())
    monkeypatch.setattr(repo, "_collection", AsyncMock(return_value=collection))

    first, concurrent = await asyncio.gather(*[
        repo.register_existing_app_record(
            owner_user_id="operator", app_id="existing-app",
            chat_app_id="existing-app", name="Existing App",
        ) for _ in range(2)
    ])
    assert first["build_registry_id"] == concurrent["build_registry_id"]
    assert first["lifecycle_state"] == "draft"
    assert first["chat_app_id"] == "existing-app"
    assert first.get("current_build_run") is None

    collection.doc["lifecycle_state"] = "active"
    collection.doc["current_build_run"] = {"build_id": "build_1", "phase": "refinement"}
    collection.doc["name"] = "Renamed App"
    before = deepcopy(collection.doc)
    repeat = await repo.register_existing_app_record(
        owner_user_id="operator", app_id="existing-app",
        chat_app_id="existing-app", name="Stale Name",
    )
    assert repeat["build_registry_id"] == first["build_registry_id"]
    assert repeat["current_build_run"]["build_id"] == "build_1"
    assert collection.doc == before

    with pytest.raises(ValueError, match="different owner or host"):
        await repo.register_existing_app_record(
            owner_user_id="foreign", app_id="existing-app",
            chat_app_id="existing-app", name=None,
        )
    collection.doc["chat_app_id"] = "different-host"
    with pytest.raises(ValueError, match="different owner or host"):
        await repo.register_existing_app_record(
            owner_user_id="operator", app_id="existing-app",
            chat_app_id="existing-app", name=None,
        )
    assert collection.doc["lifecycle_state"] == "active"
    assert collection.doc["current_build_run"]["build_id"] == "build_1"


class _RegistrationHandler:
    async def register(self, ctx):  # noqa: ANN001
        result = await registration.register_existing_self_build_target(ctx)
        return result.model_dump()


def _host(monkeypatch, app_root: Path, *, app_id: str = "existing-app") -> None:
    monkeypatch.setenv("PLATFORM_PATH", str(app_root))
    monkeypatch.setattr(platform.app.state, "startup_degraded", False, raising=False)
    monkeypatch.setattr(platform.app.state, "loaded_app_root", app_root.resolve(), raising=False)
    monkeypatch.setattr(platform.app.state, "loaded_app_id", app_id, raising=False)
    monkeypatch.setattr(platform.app.state, "loaded_app_name", "Existing App", raising=False)


def _request(
    *, kind: str = "app_internal", permissions: tuple[str, ...] = (registration.REGISTER_SELF_PERMISSION,),
    app_id: str = "existing-app", actor_id: str = "operator", params: dict | None = None,
) -> ModuleRequest:
    return ModuleRequest(
        module="operator",
        action="register",
        params=params or {},
        app_id=app_id,
        user_id="operator",
        authority=ModuleDispatchAuthority(
            kind=kind,
            permission_mode="enforce",
            reason="verified app operator operation",
            actor_id=actor_id,
            permissions=permissions,
        ),
    )


@pytest.mark.asyncio
async def test_executor_rejects_ungranted_or_noninternal_registration(monkeypatch, tmp_path) -> None:
    _host(monkeypatch, tmp_path)
    service = SimpleNamespace(register_existing_app_record=AsyncMock(return_value={
        "build_registry_id": "appreg_1", "app_id": "existing-app",
        "chat_app_id": "existing-app", "owner_user_id": "operator",
    }))
    monkeypatch.setattr(registration, "_app_registry_service", lambda: service)
    executor = ModuleExecutor()
    executor.register(
        "operator", _RegistrationHandler(),
        action_method_map={"register": "register"},
        action_permissions={"register": [registration.REGISTER_SELF_PERMISSION]},
    )

    accepted = await executor.execute(_request())
    assert accepted.success is True
    assert accepted.data == {
        "build_registry_id": "appreg_1", "target_app_id": "existing-app",
        "execution_app_id": "existing-app", "owner_user_id": "operator",
    }
    service.register_existing_app_record.assert_awaited_once_with(
        owner_user_id="operator", app_id="existing-app",
        chat_app_id="existing-app", name="Existing App",
    )

    for request in (
        _request(permissions=()),
        _request(kind="authenticated_user"),
        _request(actor_id="different-actor"),
        _request(app_id="different-host"),
        _request(params={"target_app_id": "foreign-app"}),
        _request(params={"owner_user_id": "different-owner"}),
    ):
        rejected = await executor.execute(request)
        assert rejected.success is False
    service.register_existing_app_record.assert_awaited_once()

    service.register_existing_app_record.return_value["owner_user_id"] = "foreign"
    assert (await executor.execute(_request())).success is False


@pytest.mark.asyncio
async def test_in_process_code_can_construct_matching_dispatch_facts(monkeypatch, tmp_path) -> None:
    """Audit dataclasses check wiring; they cannot prove ModuleExecutor origin."""
    _host(monkeypatch, tmp_path)
    service = SimpleNamespace(register_existing_app_record=AsyncMock(return_value={
        "build_registry_id": "appreg_1", "app_id": "existing-app",
        "chat_app_id": "existing-app", "owner_user_id": "operator",
    }))
    monkeypatch.setattr(registration, "_app_registry_service", lambda: service)
    bare = ModuleContext(app_id="existing-app", user_id="operator")
    with pytest.raises(PermissionError, match="enforced operator action"):
        await registration.register_existing_self_build_target(bare)

    # No executor runs here. Trusted in-process code can construct every
    # structural field the registration seam checks.
    copied = ModuleContext(
        app_id="existing-app",
        user_id="operator",
        module_id="operator",
        action_id="register",
        dispatch_authority=_request().authority,
        dispatch_audit=ModuleDispatchAudit(
            app_id="existing-app",
            actor_id="operator",
            module="operator",
            action="register",
            authority_kind="app_internal",
            permission_mode="enforce",
            permission_check=ModulePermissionCheck(
                checked=True,
                granted=(registration.REGISTER_SELF_PERMISSION,),
                required_permissions=(registration.REGISTER_SELF_PERMISSION,),
            ),
        ),
    )
    result = await registration.register_existing_self_build_target(copied)
    assert result.build_registry_id == "appreg_1"
    service.register_existing_app_record.assert_awaited_once()
    for actor in ("anonymous", "system"):
        copied.user_id = actor
        copied.dispatch_authority = replace(copied.dispatch_authority, actor_id=actor)
        copied.dispatch_audit = replace(copied.dispatch_audit, actor_id=actor)
        with pytest.raises(PermissionError, match="enforced operator action"):
            await registration.register_existing_self_build_target(copied)
    service.register_existing_app_record.assert_awaited_once()


@pytest.mark.asyncio
async def test_undeclared_permission_and_unhealthy_or_changed_host_deny(monkeypatch, tmp_path) -> None:
    _host(monkeypatch, tmp_path)
    service = SimpleNamespace(register_existing_app_record=AsyncMock())
    monkeypatch.setattr(registration, "_app_registry_service", lambda: service)
    executor = ModuleExecutor()
    executor.register("operator", _RegistrationHandler(), action_method_map={"register": "register"})
    assert (await executor.execute(_request())).success is False

    executor = ModuleExecutor()
    executor.register(
        "operator", _RegistrationHandler(),
        action_method_map={"register": "register"},
        action_permissions={"register": [registration.REGISTER_SELF_PERMISSION]},
    )
    monkeypatch.setattr(platform.app.state, "startup_degraded", True)
    assert (await executor.execute(_request())).success is False
    monkeypatch.setattr(platform.app.state, "startup_degraded", False)
    monkeypatch.setattr(platform.app.state, "loaded_app_id", "foreign-app")
    assert (await executor.execute(_request())).success is False
    monkeypatch.setattr(platform.app.state, "loaded_app_id", "existing-app")
    monkeypatch.setattr(platform.app.state, "loaded_app_root", tmp_path / "different-root")
    assert (await executor.execute(_request())).success is False
    service.register_existing_app_record.assert_not_awaited()
