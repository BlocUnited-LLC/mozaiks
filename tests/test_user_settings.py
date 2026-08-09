"""Tests for /api/me/settings/{module_id} persistence.

Covers:
- GET returns defaults when nothing is stored
- PUT persists values; subsequent GET returns the stored override
- PUT merges on top of existing stored values (partial update)
- PUT rejects app-scoped settings with 422
- PUT rejects type violations with 422
- _user_settings_doc_id format
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Minimal in-memory collection stub (same pattern as test_profile_contract.py)
# ---------------------------------------------------------------------------

class _Collection:
    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}

    async def find_one(self, query, *_args, **_kwargs):
        doc = self.docs.get(query["_id"])
        return deepcopy(doc) if doc is not None else None

    async def update_one(self, query, update, upsert=False):
        doc_id = query["_id"]
        exists = doc_id in self.docs
        if not exists and not upsert:
            return None
        doc = deepcopy(self.docs.get(doc_id, {"_id": doc_id}))
        if not exists:
            doc.update(deepcopy(update.get("$setOnInsert", {})))
        doc.update(deepcopy(update.get("$set", {})))
        doc["_id"] = doc_id
        self.docs[doc_id] = doc
        return None


# ---------------------------------------------------------------------------
# Minimal ModuleExecutor stub with typed SettingDefs
# ---------------------------------------------------------------------------

def _make_executor(defs: list[dict]):
    """Build a minimal executor stub with the given setting definitions."""
    from mozaiksai.core.runtime.app.module_loader import SettingDef
    from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor

    executor = ModuleExecutor()

    class _Stub:
        async def noop(self, ctx, **_):
            return {}

    typed_defs = [SettingDef.model_validate(d) for d in defs]
    executor.register(
        "mymodule",
        _Stub(),
        action_method_map={"noop": "noop"},
        settings=typed_defs,
    )
    return executor


class _FakeRegistry:
    """Drop-in replacement for ExecutorRegistry with a real (or None) module_executor."""

    def __init__(self, executor):
        self.module_executor = executor


def _make_principal(user_id: str = "user-1"):
    from mozaiksai.core.auth.dependencies import UserPrincipal
    return UserPrincipal(
        user_id=user_id, email=None, name=None, roles=[], scopes=[], raw_claims={}
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def test_user_settings_doc_id_format():
    from mozaiksai.hosts.platform import _user_settings_doc_id
    assert _user_settings_doc_id("app-1", "user-1", "commerce") == "app-1:user-1:commerce"


# ---------------------------------------------------------------------------
# GET — returns defaults when nothing stored
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_returns_defaults_when_nothing_stored(monkeypatch):
    from mozaiksai.hosts import platform as platform_app

    col = _Collection()
    executor = _make_executor([
        {"id": "notif.digest", "type": "enum", "scope": "user",
         "default": "daily", "label": "Digest", "enum_values": ["never", "daily", "weekly"]},
    ])

    monkeypatch.setattr(platform_app, "_user_settings_collection", lambda: _async(col))
    monkeypatch.setattr(platform_app, "executor_registry", _FakeRegistry(executor))
    monkeypatch.setattr(platform_app, "_resolve_default_app_id", lambda: "app-1")

    result = await platform_app.get_module_settings_for_user(
        module_id="mymodule",
        app_id="app-1",
        principal=_make_principal(),
    )

    assert result["module_id"] == "mymodule"
    assert len(result["settings"]) == 1
    s = result["settings"][0]
    assert s["id"] == "notif.digest"
    assert s["value"] == "daily"   # default, nothing stored
    assert s["default"] == "daily"
    assert s["enum_values"] == ["never", "daily", "weekly"]


# ---------------------------------------------------------------------------
# PUT → GET round-trip
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_put_persists_and_get_returns_stored_value(monkeypatch):
    from mozaiksai.hosts import platform as platform_app

    col = _Collection()
    executor = _make_executor([
        {"id": "notif.digest", "type": "enum", "scope": "user",
         "default": "daily", "label": "Digest", "enum_values": ["never", "daily", "weekly"]},
    ])

    monkeypatch.setattr(platform_app, "_user_settings_collection", lambda: _async(col))
    monkeypatch.setattr(platform_app, "executor_registry", _FakeRegistry(executor))
    monkeypatch.setattr(platform_app, "_resolve_default_app_id", lambda: "app-1")

    principal = _make_principal()

    # PUT overrides to "weekly"
    put_result = await platform_app.update_module_settings_for_user(
        module_id="mymodule",
        body={"notif.digest": "weekly"},
        app_id="app-1",
        principal=principal,
    )
    assert put_result["settings"][0]["value"] == "weekly"

    # GET should now return "weekly" from storage
    get_result = await platform_app.get_module_settings_for_user(
        module_id="mymodule",
        app_id="app-1",
        principal=principal,
    )
    assert get_result["settings"][0]["value"] == "weekly"


# ---------------------------------------------------------------------------
# PUT partial update semantics
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_put_merges_on_top_of_existing_stored_values(monkeypatch):
    from mozaiksai.hosts import platform as platform_app

    col = _Collection()
    executor = _make_executor([
        {"id": "a", "type": "string", "scope": "user", "default": "x", "label": "A"},
        {"id": "b", "type": "string", "scope": "user", "default": "y", "label": "B"},
    ])

    monkeypatch.setattr(platform_app, "_user_settings_collection", lambda: _async(col))
    monkeypatch.setattr(platform_app, "executor_registry", _FakeRegistry(executor))
    monkeypatch.setattr(platform_app, "_resolve_default_app_id", lambda: "app-1")

    principal = _make_principal()

    # First PUT sets a="hello"
    await platform_app.update_module_settings_for_user(
        module_id="mymodule", body={"a": "hello"}, app_id="app-1", principal=principal,
    )
    # Second PUT sets b="world" — must not wipe a
    await platform_app.update_module_settings_for_user(
        module_id="mymodule", body={"b": "world"}, app_id="app-1", principal=principal,
    )

    result = await platform_app.get_module_settings_for_user(
        module_id="mymodule", app_id="app-1", principal=principal,
    )
    values = {s["id"]: s["value"] for s in result["settings"]}
    assert values["a"] == "hello"
    assert values["b"] == "world"


# ---------------------------------------------------------------------------
# PUT rejects app-scoped settings
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_put_rejects_app_scoped_setting(monkeypatch):
    from fastapi import HTTPException
    from mozaiksai.hosts import platform as platform_app

    col = _Collection()
    executor = _make_executor([
        {"id": "currency", "type": "string", "scope": "app", "default": "USD", "label": "Currency"},
    ])

    monkeypatch.setattr(platform_app, "_user_settings_collection", lambda: _async(col))
    monkeypatch.setattr(platform_app, "executor_registry", _FakeRegistry(executor))
    monkeypatch.setattr(platform_app, "_resolve_default_app_id", lambda: "app-1")

    with pytest.raises(HTTPException) as exc_info:
        await platform_app.update_module_settings_for_user(
            module_id="mymodule",
            body={"currency": "EUR"},
            app_id="app-1",
            principal=_make_principal(),
        )

    assert exc_info.value.status_code == 422
    assert any("currency" in str(e) for e in exc_info.value.detail["errors"])


# ---------------------------------------------------------------------------
# PUT rejects type violations
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_put_rejects_boolean_type_violation(monkeypatch):
    from fastapi import HTTPException
    from mozaiksai.hosts import platform as platform_app

    col = _Collection()
    executor = _make_executor([
        {"id": "enabled", "type": "boolean", "scope": "user", "default": True, "label": "Enabled"},
    ])

    monkeypatch.setattr(platform_app, "_user_settings_collection", lambda: _async(col))
    monkeypatch.setattr(platform_app, "executor_registry", _FakeRegistry(executor))
    monkeypatch.setattr(platform_app, "_resolve_default_app_id", lambda: "app-1")

    with pytest.raises(HTTPException) as exc_info:
        await platform_app.update_module_settings_for_user(
            module_id="mymodule",
            body={"enabled": "yes"},   # string instead of bool
            app_id="app-1",
            principal=_make_principal(),
        )

    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_put_rejects_enum_value_not_in_allowed_set(monkeypatch):
    from fastapi import HTTPException
    from mozaiksai.hosts import platform as platform_app

    col = _Collection()
    executor = _make_executor([
        {"id": "theme", "type": "enum", "scope": "user", "default": "light",
         "label": "Theme", "enum_values": ["light", "dark"]},
    ])

    monkeypatch.setattr(platform_app, "_user_settings_collection", lambda: _async(col))
    monkeypatch.setattr(platform_app, "executor_registry", _FakeRegistry(executor))
    monkeypatch.setattr(platform_app, "_resolve_default_app_id", lambda: "app-1")

    with pytest.raises(HTTPException) as exc_info:
        await platform_app.update_module_settings_for_user(
            module_id="mymodule",
            body={"theme": "solarized"},   # not in enum_values
            app_id="app-1",
            principal=_make_principal(),
        )

    assert exc_info.value.status_code == 422


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

async def _async(value: Any) -> Any:
    return value
