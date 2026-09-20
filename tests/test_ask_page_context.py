"""Page-declared ask context: declaration normalization, fail-closed
eligibility, enforce-mode dispatch, and the Studio dogfood contract."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from mozaiksai.core.runtime.app import ask_page_context as apc

REPO_ROOT = Path(__file__).resolve().parents[1]


def _fake_app(eligibility: dict[str, dict[str, bool]] | None) -> SimpleNamespace:
    state = SimpleNamespace()
    if eligibility is not None:
        state.module_ask_context_actions = eligibility
    return SimpleNamespace(state=state)


def test_normalize_drops_malformed_and_caps_declarations() -> None:
    raw = [
        {"module": "support", "action": "list", "params": {"limit": 5}},
        {"module": "", "action": "list"},
        "not-a-dict",
        {"module": "support", "action": ""},
        {"module": "a", "action": "b", "params": [{"key": "status", "value": "open"}]},
        {"module": "c", "action": "d", "label": "Custom label"},
        {"module": "e", "action": "f"},
    ]
    declarations = apc.normalize_ask_context_declarations(raw)

    assert len(declarations) == apc.MAX_DECLARED_ACTIONS
    assert declarations[0] == {
        "module": "support",
        "action": "list",
        "params": {"limit": 5},
        "label": "support.list",
    }
    assert declarations[1]["params"] == {"status": "open"}
    assert declarations[2]["label"] == "Custom label"
    assert apc.normalize_ask_context_declarations(None) == []
    assert apc.normalize_ask_context_declarations({"module": "x"}) == []


@pytest.mark.asyncio
async def test_resolve_dispatches_only_ask_context_safe_actions(monkeypatch) -> None:
    dispatched: list[Any] = []

    async def fake_dispatch(request, *, app=None):
        dispatched.append(request)
        return SimpleNamespace(
            success=True, data={"requests": [], "total": 0}, error=None, error_code=None
        )

    import mozaiksai.core.runtime.composition as composition

    monkeypatch.setattr(composition, "dispatch_module_action", fake_dispatch)

    app = _fake_app(
        {
            "workspace_support": {
                "list_support_requests": True,
                "create_support_request": False,
            },
            "other_module": {"read_things": False},
        }
    )
    declarations = apc.normalize_ask_context_declarations(
        [
            {"module": "workspace_support", "action": "list_support_requests", "label": "Support requests"},
            {"module": "workspace_support", "action": "create_support_request"},
            {"module": "other_module", "action": "read_things"},
        ]
    )

    context = await apc.resolve_page_ask_context(
        declarations, app=app, app_id="app_1", user_id="user_1"
    )

    # Only the ask_context_safe action dispatched; the others failed closed.
    assert len(dispatched) == 1
    request = dispatched[0]
    assert request.module == "workspace_support"
    assert request.action == "list_support_requests"
    assert request.scope.app_id == "app_1"
    assert request.scope.user_id == "user_1"
    assert request.authority.kind == "authenticated_user"
    assert request.authority.permission_mode == "enforce"
    assert request.authority.permissions == ()
    assert context == {
        "Support requests": json.dumps({"requests": [], "total": 0}, ensure_ascii=False)
    }


@pytest.mark.asyncio
async def test_resolve_skips_failures_and_truncates(monkeypatch) -> None:
    calls = {"n": 0}

    async def fake_dispatch(request, *, app=None):
        calls["n"] += 1
        if request.action == "boom":
            raise RuntimeError("executor offline")
        if request.action == "denied":
            return SimpleNamespace(
                success=False, data=None, error="denied", error_code="PERMISSION_DENIED"
            )
        return SimpleNamespace(
            success=True, data={"rows": ["x" * 4000]}, error=None, error_code=None
        )

    import mozaiksai.core.runtime.composition as composition

    monkeypatch.setattr(composition, "dispatch_module_action", fake_dispatch)

    surfaces = {"m": {"boom": True, "denied": True, "big": True}}
    declarations = apc.normalize_ask_context_declarations(
        [
            {"module": "m", "action": "boom"},
            {"module": "m", "action": "denied"},
            {"module": "m", "action": "big", "label": "Big"},
        ]
    )

    context = await apc.resolve_page_ask_context(
        declarations, app=_fake_app(surfaces), app_id="app_1", user_id="user_1"
    )

    assert calls["n"] == 3
    assert list(context.keys()) == ["Big"]
    assert context["Big"].endswith("(truncated)")
    assert len(context["Big"]) <= apc.MAX_RENDERED_CHARS + 20


@pytest.mark.asyncio
async def test_resolve_fails_closed_without_surface_map() -> None:
    declarations = apc.normalize_ask_context_declarations([{"module": "m", "action": "a"}])
    context = await apc.resolve_page_ask_context(
        declarations, app=_fake_app(None), app_id="app_1", user_id="user_1"
    )
    assert context == {}


def test_page_schema_meta_accepts_ask_context_declarations() -> None:
    from mozaiksai.core.runtime.app.page_schema import AppAskContextAction, AppPageMeta

    meta = AppPageMeta(
        ask_context=[
            AppAskContextAction(
                module="workspace_support",
                action="list_support_requests",
                params={"limit": 10},
                label="Support requests",
            )
        ]
    )
    assert meta.ask_context is not None
    assert meta.ask_context[0].module == "workspace_support"

    with pytest.raises(ValidationError):
        AppAskContextAction(module="m", action="a", unexpected_field=True)


def test_studio_support_page_dogfood_declaration_is_eligible() -> None:
    """The shipped Studio declaration must satisfy the resolver's own
    fail-closed eligibility rules against the real module contract."""
    manifest = json.loads(
        (REPO_ROOT / "factory_app/app/ui/route_manifest.json").read_text(encoding="utf-8")
    )
    support_entry = next(entry for entry in manifest["pages"] if entry.get("path") == "/support")
    declarations = apc.normalize_ask_context_declarations(support_entry["meta"]["ask_context"])
    assert declarations, "Studio /support page must declare ask context"

    module_yaml = yaml.safe_load(
        (REPO_ROOT / "factory_app/app/modules/workspace_support/module.yaml").read_text(
            encoding="utf-8"
        )
    )
    actions = {action["id"]: action for action in module_yaml["actions"]}
    for declaration in declarations:
        assert declaration["module"] == "workspace_support"
        action = actions[declaration["action"]]
        assert action.get("ask_context_safe") is True
        assert action.get("permissions") == []
        # Ask eligibility must not widen HTTP exposure.
        assert action.get("api_surface") is None


def test_platform_host_registers_page_ask_context_hook() -> None:
    """The platform host must actually land the page resolver in a registry.

    Asserted against a fresh registry rather than the process singleton:
    another test module resets that singleton, and a cached module import does
    not re-run import-time registration.
    """
    from mozaiksai.core.runtime.composition.platform_hooks import PlatformHookRegistry
    from mozaiksai.hosts import platform as platform_app

    registry = PlatformHookRegistry()
    registry._loaded = True  # skip env-extension loading
    platform_app.register_platform_ask_context_hooks(registry)

    assert registry.has_ask_context is True
    names = {
        getattr(hook, "__qualname__", repr(hook)) for hook in registry._ask_context_hooks
    }
    assert "_page_declared_ask_context" in names


@pytest.mark.asyncio
async def test_resolve_routes_scope_through_module_scope_resolver(monkeypatch) -> None:
    """Host scope resolvers must narrow ask-context dispatch the same way they
    narrow HTTP and workflow dispatch, and must never be able to change actor."""
    from mozaiksai.core.runtime.composition.platform_hooks import PlatformHookRegistry

    registry = PlatformHookRegistry()
    registry._loaded = True
    seen: dict = {}

    def scope_hook(**kwargs):
        seen.update(kwargs)
        return {"tenant_id": "tenant_9", "workspace_id": "ws_3", "permissions": ["should.be.ignored"]}

    registry._register_bundle({"module_scope_resolver": scope_hook})

    dispatched: list[Any] = []

    async def fake_dispatch(request, *, app=None):
        dispatched.append(request)
        return SimpleNamespace(success=True, data={"ok": True}, error=None, error_code=None)

    import mozaiksai.core.runtime.composition as composition

    monkeypatch.setattr(composition, "dispatch_module_action", fake_dispatch)
    monkeypatch.setattr(composition, "get_platform_hooks", lambda: registry)

    declarations = apc.normalize_ask_context_declarations([{"module": "m", "action": "a"}])
    context = await apc.resolve_page_ask_context(
        declarations, app=_fake_app({"m": {"a": True}}), app_id="app_1", user_id="user_1"
    )

    assert seen["module_name"] == "m"
    assert seen["action_name"] == "a"
    assert seen["requested_scope"]["app_id"] == "app_1"
    assert seen["requested_scope"]["user_id"] == "user_1"
    assert len(dispatched) == 1
    # Resolver-supplied tenant/workspace flow through; permissions never do.
    assert dispatched[0].scope.tenant_id == "tenant_9"
    assert dispatched[0].scope.workspace_id == "ws_3"
    assert dispatched[0].authority.permissions == ()
    assert context == {"m.a": '{"ok": true}'}


@pytest.mark.asyncio
async def test_resolve_skips_action_when_scope_resolver_rewrites_actor(monkeypatch) -> None:
    from mozaiksai.core.runtime.composition.platform_hooks import PlatformHookRegistry

    registry = PlatformHookRegistry()
    registry._loaded = True
    registry._register_bundle({"module_scope_resolver": lambda **kw: {"user_id": "someone_else"}})

    dispatched: list[Any] = []

    async def fake_dispatch(request, *, app=None):
        dispatched.append(request)
        return SimpleNamespace(success=True, data={}, error=None, error_code=None)

    import mozaiksai.core.runtime.composition as composition

    monkeypatch.setattr(composition, "dispatch_module_action", fake_dispatch)
    monkeypatch.setattr(composition, "get_platform_hooks", lambda: registry)

    declarations = apc.normalize_ask_context_declarations([{"module": "m", "action": "a"}])
    context = await apc.resolve_page_ask_context(
        declarations, app=_fake_app({"m": {"a": True}}), app_id="app_1", user_id="user_1"
    )

    assert dispatched == []
    assert context == {}


def test_module_loader_exposes_ask_context_eligibility() -> None:
    """The eligibility flag is a real module-contract field, defaulting off."""
    from mozaiksai.core.runtime.app.module_loader import ActionDef

    default_action = ActionDef(id="a", description="d", handler_method="h")
    assert default_action.ask_context_safe is False

    opted_in = ActionDef(id="a", description="d", handler_method="h", ask_context_safe=True)
    assert opted_in.ask_context_safe is True
    # Ask eligibility is independent of HTTP exposure.
    assert opted_in.api_surface is None
