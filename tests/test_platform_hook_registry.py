"""
PlatformHookRegistry unit tests.

Covers:
  - Empty registry no-op behavior (all callers return safe defaults)
  - Bundle registration from dict and from object with attributes
  - call_chat_prereqs: passes when all hooks allow, first denial wins,
    exception in hook is tolerated, empty reason normalized
  - call_chat_session_fields: merges extra fields from all hooks, tolerates exceptions
  - call_module_permissions: lets host hooks resolve module permissions
  - call_workflow_ordering: returns original list when no hooks, applies hook chain
  - call_workflow_name_resolver: hook override, case-insensitive built-in fallback,
    no match returns None, empty request returns None
  - run_startup: no-op when empty, exception in hook is tolerated,
    sync and async hooks both called
  - summary: counts hooks per slot
  - has_* properties: False when no hooks
  - reset() clears singleton state
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mozaiksai.core.runtime.composition.platform_hooks import PlatformHookRegistry

# ---------------------------------------------------------------------------
# Fixture: fresh registry (bypasses singleton for unit tests)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_singleton():
    """Ensure each test starts with a clean singleton."""
    PlatformHookRegistry.reset()
    yield
    PlatformHookRegistry.reset()


def _fresh() -> PlatformHookRegistry:
    """Return a new, unloaded PlatformHookRegistry (no env extension)."""
    reg = PlatformHookRegistry()
    reg._loaded = True  # skip _load() so no env var is needed
    return reg


def _with_bundle(bundle: dict | object) -> PlatformHookRegistry:
    reg = _fresh()
    reg._register_bundle(bundle, source="<test>")
    return reg


# ---------------------------------------------------------------------------
# 1. Empty registry — safe no-ops
# ---------------------------------------------------------------------------

class TestEmptyRegistry:
    @pytest.mark.asyncio
    async def test_run_startup_is_noop(self):
        reg = _fresh()
        await reg.run_startup(MagicMock())  # no exception

    @pytest.mark.asyncio
    async def test_call_chat_prereqs_allows(self):
        reg = _fresh()
        ok, reason = await reg.call_chat_prereqs("app", "user", "wf", None)
        assert ok is True
        assert reason is None

    @pytest.mark.asyncio
    async def test_call_chat_session_fields_returns_empty(self):
        reg = _fresh()
        result = await reg.call_chat_session_fields("app", "user", "wf", "chat")
        assert result == {}

    def test_call_workflow_ordering_returns_original(self):
        reg = _fresh()
        names = ["B", "A", "C"]
        assert reg.call_workflow_ordering(names) == names

    def test_call_workflow_name_resolver_returns_none_for_no_match(self):
        reg = _fresh()
        assert reg.call_workflow_name_resolver("nonexistent", ["a", "b"]) is None

    def test_call_workflow_name_resolver_returns_none_for_empty_request(self):
        reg = _fresh()
        assert reg.call_workflow_name_resolver("", ["a", "b"]) is None

    def test_summary_all_zeros(self):
        reg = _fresh()
        s = reg.summary()
        assert all(v == 0 for v in s.values())

    def test_has_prereqs_false(self):
        reg = _fresh()
        assert reg.has_prereqs is False

    def test_has_session_fields_false(self):
        reg = _fresh()
        assert reg.has_session_fields is False

    def test_has_module_permission_resolver_false(self):
        reg = _fresh()
        assert reg.has_module_permission_resolver is False

    def test_has_startup_false(self):
        reg = _fresh()
        assert reg.has_startup is False


# ---------------------------------------------------------------------------
# 2. Bundle registration
# ---------------------------------------------------------------------------

class TestBundleRegistration:
    def test_register_from_dict_adds_hooks(self):
        hook = MagicMock()
        reg = _with_bundle({"on_startup": hook})
        assert reg.has_startup is True

    def test_register_from_object_with_attr_adds_hooks(self):
        class Bundle:
            on_startup = MagicMock()
        reg = _with_bundle(Bundle())
        assert reg.has_startup is True

    def test_non_callable_value_not_registered(self):
        reg = _with_bundle({"on_startup": "not_callable"})
        assert reg.has_startup is False

    def test_missing_key_not_registered(self):
        reg = _with_bundle({})
        assert reg.has_startup is False
        assert reg.has_prereqs is False

    def test_multiple_bundles_stack_hooks(self):
        hook1 = MagicMock(return_value=(True, None))
        hook2 = MagicMock(return_value=(True, None))
        reg = _fresh()
        reg._register_bundle({"chat_prereqs": hook1})
        reg._register_bundle({"chat_prereqs": hook2})
        assert len(reg._chat_prereqs_hooks) == 2

    def test_all_slots_populated_from_one_bundle(self):
        bundle = {
            "on_startup": MagicMock(),
            "chat_prereqs": MagicMock(return_value=(True, None)),
            "chat_session_fields": MagicMock(return_value={}),
            "module_permission_resolver": MagicMock(return_value=[]),
            "workflow_ordering": MagicMock(return_value=[]),
            "workflow_name_resolver": MagicMock(return_value=None),
        }
        reg = _with_bundle(bundle)
        assert reg.has_startup is True
        assert reg.has_prereqs is True
        assert reg.has_session_fields is True
        assert reg.has_module_permission_resolver is True


# ---------------------------------------------------------------------------
# 3. call_chat_prereqs
# ---------------------------------------------------------------------------

class TestCallChatPrereqs:
    @pytest.mark.asyncio
    async def test_all_hooks_allow_returns_ok(self):
        reg = _fresh()
        reg._register_bundle({"chat_prereqs": lambda **kw: (True, None)})
        reg._register_bundle({"chat_prereqs": lambda **kw: (True, None)})
        ok, reason = await reg.call_chat_prereqs("app", "user", "wf", None)
        assert ok is True

    @pytest.mark.asyncio
    async def test_first_denial_returns_false(self):
        called_second = []

        def deny(**kw):
            return (False, "no_quota")

        def allow(**kw):
            called_second.append(True)
            return (True, None)

        reg = _fresh()
        reg._register_bundle({"chat_prereqs": deny})
        reg._register_bundle({"chat_prereqs": allow})
        ok, reason = await reg.call_chat_prereqs("app", "user", "wf", None)
        assert ok is False
        assert reason == "no_quota"
        assert called_second == []  # short-circuit

    @pytest.mark.asyncio
    async def test_denial_with_empty_reason_normalized(self):
        reg = _fresh()
        reg._register_bundle({"chat_prereqs": lambda **kw: (False, None)})
        ok, reason = await reg.call_chat_prereqs("app", "user", "wf", None)
        assert ok is False
        assert reason is not None  # normalized

    @pytest.mark.asyncio
    async def test_exception_in_hook_is_tolerated(self):
        def bad_hook(**kw):
            raise RuntimeError("prereq failure")

        reg = _fresh()
        reg._register_bundle({"chat_prereqs": bad_hook})
        ok, reason = await reg.call_chat_prereqs("app", "user", "wf", None)
        assert ok is True  # exception doesn't deny

    @pytest.mark.asyncio
    async def test_async_hook_awaited(self):
        async def async_allow(**kw):
            return (True, None)

        reg = _fresh()
        reg._register_bundle({"chat_prereqs": async_allow})
        ok, _ = await reg.call_chat_prereqs("app", "user", "wf", None)
        assert ok is True


# ---------------------------------------------------------------------------
# 4. call_chat_session_fields
# ---------------------------------------------------------------------------

class TestCallChatSessionFields:
    @pytest.mark.asyncio
    async def test_merges_fields_from_all_hooks(self):
        reg = _fresh()
        reg._register_bundle({"chat_session_fields": lambda **kw: {"journey_id": "j-1"}})
        reg._register_bundle({"chat_session_fields": lambda **kw: {"tier": "pro"}})
        result = await reg.call_chat_session_fields("app", "user", "wf", "chat")
        assert result == {"journey_id": "j-1", "tier": "pro"}

    @pytest.mark.asyncio
    async def test_exception_in_hook_is_tolerated(self):
        def bad(**kw):
            raise RuntimeError("session fields error")

        reg = _fresh()
        reg._register_bundle({"chat_session_fields": bad})
        result = await reg.call_chat_session_fields("app", "user", "wf", "chat")
        assert result == {}

    @pytest.mark.asyncio
    async def test_non_dict_return_ignored(self):
        reg = _fresh()
        reg._register_bundle({"chat_session_fields": lambda **kw: "invalid"})
        result = await reg.call_chat_session_fields("app", "user", "wf", "chat")
        assert result == {}

    @pytest.mark.asyncio
    async def test_async_hook_awaited(self):
        async def async_fields(**kw):
            return {"key": "value"}

        reg = _fresh()
        reg._register_bundle({"chat_session_fields": async_fields})
        result = await reg.call_chat_session_fields("app", "user", "wf", "chat")
        assert result == {"key": "value"}


# ---------------------------------------------------------------------------
# 5. call_module_permissions
# ---------------------------------------------------------------------------

class TestCallModulePermissions:
    @pytest.mark.asyncio
    async def test_returns_default_permissions_when_no_hooks(self):
        reg = _fresh()
        result = await reg.call_module_permissions(
            principal=None,
            module_name="orders",
            action_name="list",
            app_id="app",
            tenant_id=None,
            user_id="user",
            params={},
            default_permissions=["orders.read"],
        )
        assert result == ["orders.read"]


# ---------------------------------------------------------------------------
# 5b. call_module_scope
# ---------------------------------------------------------------------------

class TestCallModuleScope:
    @pytest.mark.asyncio
    async def test_returns_requested_scope_and_default_permissions_when_no_hooks(self):
        reg = _fresh()
        result = await reg.call_module_scope(
            principal=None,
            module_name="orders",
            action_name="list",
            requested_scope={
                "app_id": "app",
                "tenant_id": "tenant",
                "workspace_id": "workspace",
                "user_id": "user",
            },
            params={},
            default_permissions=[],
        )
        assert result == {
            "app_id": "app",
            "tenant_id": "tenant",
            "workspace_id": "workspace",
            "user_id": "user",
            "permissions": [],
        }

    @pytest.mark.asyncio
    async def test_scope_hook_can_replace_scope_and_permissions(self):
        reg = _fresh()
        reg._register_bundle({
            "module_scope_resolver": lambda **kw: {
                "tenant_id": "resolved-tenant",
                "workspace_id": "resolved-workspace",
                "permissions": ["orders.read", "orders.manage"],
            },
        })
        result = await reg.call_module_scope(
            principal=object(),
            module_name="orders",
            action_name="create",
            requested_scope={"app_id": "app", "tenant_id": "requested", "workspace_id": None, "user_id": "user"},
            params={"x": 1},
            default_permissions=["access_as_user"],
        )
        assert result["app_id"] == "app"
        assert result["tenant_id"] == "resolved-tenant"
        assert result["workspace_id"] == "resolved-workspace"
        assert result["permissions"] == ["orders.read", "orders.manage"]

    @pytest.mark.asyncio
    async def test_permission_hook_runs_after_scope_hook(self):
        reg = _fresh()
        reg._register_bundle({
            "module_scope_resolver": lambda **kw: {
                "tenant_id": "tenant",
                "permissions": ["orders.read"],
            },
            "module_permission_resolver": lambda **kw: [
                *(kw["default_permissions"] or []),
                "orders.manage",
            ],
        })
        result = await reg.call_module_scope(
            principal=object(),
            module_name="orders",
            action_name="create",
            requested_scope={"app_id": "app", "tenant_id": None, "workspace_id": None, "user_id": "user"},
            params={},
            default_permissions=[],
        )
        assert result["tenant_id"] == "tenant"
        assert result["permissions"] == ["orders.read", "orders.manage"]

    @pytest.mark.asyncio
    async def test_hook_can_replace_permission_list(self):
        reg = _fresh()
        reg._register_bundle({
            "module_permission_resolver": lambda **kw: [
                *(kw["default_permissions"] or []),
                "orders.manage",
            ],
        })
        result = await reg.call_module_permissions(
            principal=object(),
            module_name="orders",
            action_name="create",
            app_id="app",
            tenant_id="tenant",
            user_id="user",
            params={"x": 1},
            default_permissions=["orders.read"],
        )
        assert result == ["orders.read", "orders.manage"]

    @pytest.mark.asyncio
    async def test_hook_none_keeps_current_permissions(self):
        reg = _fresh()
        reg._register_bundle({"module_permission_resolver": lambda **kw: None})
        result = await reg.call_module_permissions(
            principal=object(),
            module_name="orders",
            action_name="list",
            app_id="app",
            tenant_id=None,
            user_id="user",
            params={},
            default_permissions=["orders.read"],
        )
        assert result == ["orders.read"]

    @pytest.mark.asyncio
    async def test_exception_in_hook_is_tolerated(self):
        def bad(**kw):
            raise RuntimeError("permission resolver failed")

        reg = _fresh()
        reg._register_bundle({"module_permission_resolver": bad})
        result = await reg.call_module_permissions(
            principal=object(),
            module_name="orders",
            action_name="list",
            app_id="app",
            tenant_id=None,
            user_id="user",
            params={},
            default_permissions=["orders.read"],
        )
        assert result == ["orders.read"]

    @pytest.mark.asyncio
    async def test_async_hook_awaited(self):
        async def resolver(**kw):
            return ["orders.read"]

        reg = _fresh()
        reg._register_bundle({"module_permission_resolver": resolver})
        result = await reg.call_module_permissions(
            principal=object(),
            module_name="orders",
            action_name="list",
            app_id="app",
            tenant_id=None,
            user_id="user",
            params={},
            default_permissions=[],
        )
        assert result == ["orders.read"]


# ---------------------------------------------------------------------------
# 6. call_workflow_ordering
# ---------------------------------------------------------------------------

class TestCallWorkflowOrdering:
    def test_returns_original_when_no_hooks(self):
        reg = _fresh()
        names = ["B", "A", "C"]
        assert reg.call_workflow_ordering(names) == names

    def test_applies_hook_transform(self):
        reg = _fresh()
        reg._register_bundle({"workflow_ordering": sorted})
        result = reg.call_workflow_ordering(["B", "A", "C"])
        assert result == ["A", "B", "C"]

    def test_exception_in_hook_is_tolerated(self):
        def bad(names):
            raise RuntimeError("ordering error")

        reg = _fresh()
        reg._register_bundle({"workflow_ordering": bad})
        result = reg.call_workflow_ordering(["A", "B"])
        assert result == ["A", "B"]  # original preserved

    def test_non_list_return_ignored(self):
        reg = _fresh()
        reg._register_bundle({"workflow_ordering": lambda names: None})
        result = reg.call_workflow_ordering(["A", "B"])
        assert result == ["A", "B"]


# ---------------------------------------------------------------------------
# 7. call_workflow_name_resolver
# ---------------------------------------------------------------------------

class TestCallWorkflowNameResolver:
    def test_hook_override_wins(self):
        reg = _fresh()
        reg._register_bundle({"workflow_name_resolver": lambda req, names: "custom_wf"})
        result = reg.call_workflow_name_resolver("anything", ["custom_wf", "other"])
        assert result == "custom_wf"

    def test_built_in_case_insensitive_fallback(self):
        reg = _fresh()
        result = reg.call_workflow_name_resolver("MYWF", ["myWf", "other"])
        assert result == "myWf"

    def test_returns_none_when_no_match(self):
        reg = _fresh()
        result = reg.call_workflow_name_resolver("missing", ["a", "b"])
        assert result is None

    def test_empty_requested_name_returns_none(self):
        reg = _fresh()
        assert reg.call_workflow_name_resolver("", ["a", "b"]) is None
        assert reg.call_workflow_name_resolver("  ", ["a", "b"]) is None

    def test_exception_in_hook_is_tolerated_fallback_used(self):
        def bad(req, names):
            raise RuntimeError("resolver error")

        reg = _fresh()
        reg._register_bundle({"workflow_name_resolver": bad})
        # Falls back to built-in case-insensitive match
        result = reg.call_workflow_name_resolver("WF", ["wf"])
        assert result == "wf"

    def test_hook_returning_empty_string_falls_through(self):
        reg = _fresh()
        reg._register_bundle({"workflow_name_resolver": lambda req, names: ""})
        result = reg.call_workflow_name_resolver("wf", ["wf"])
        assert result == "wf"  # built-in fallback used


# ---------------------------------------------------------------------------
# 8. run_startup
# ---------------------------------------------------------------------------

class TestRunStartup:
    @pytest.mark.asyncio
    async def test_sync_hook_called(self):
        called = []

        def sync_hook(app):
            called.append(True)

        reg = _fresh()
        reg._register_bundle({"on_startup": sync_hook})
        await reg.run_startup(MagicMock())
        assert called == [True]

    @pytest.mark.asyncio
    async def test_async_hook_awaited(self):
        called = []

        async def async_hook(app):
            called.append(True)

        reg = _fresh()
        reg._register_bundle({"on_startup": async_hook})
        await reg.run_startup(MagicMock())
        assert called == [True]

    @pytest.mark.asyncio
    async def test_exception_in_hook_is_tolerated(self):
        def bad_hook(app):
            raise RuntimeError("startup failure")

        reg = _fresh()
        reg._register_bundle({"on_startup": bad_hook})
        await reg.run_startup(MagicMock())  # no exception raised


# ---------------------------------------------------------------------------
# 9. summary()
# ---------------------------------------------------------------------------

class TestSummary:
    def test_summary_counts_hooks(self):
        hook = MagicMock()
        reg = _fresh()
        reg._register_bundle({
            "on_startup": hook,
            "chat_prereqs": hook,
            "module_permission_resolver": hook,
            "workflow_ordering": hook,
        })
        s = reg.summary()
        assert s["startup_hooks"] == 1
        assert s["chat_prereqs_hooks"] == 1
        assert s["module_permission_resolver_hooks"] == 1
        assert s["workflow_ordering_hooks"] == 1
        assert s["chat_session_fields_hooks"] == 0

    def test_multiple_hooks_counted(self):
        hook = MagicMock()
        reg = _fresh()
        reg._register_bundle({"on_startup": hook})
        reg._register_bundle({"on_startup": hook})
        assert reg.summary()["startup_hooks"] == 2


# ---------------------------------------------------------------------------
# 10. Singleton reset
# ---------------------------------------------------------------------------

class TestSingletonReset:
    def test_get_instance_returns_same_object(self):
        inst1 = PlatformHookRegistry.get_instance()
        inst2 = PlatformHookRegistry.get_instance()
        assert inst1 is inst2

    def test_reset_clears_singleton(self):
        inst1 = PlatformHookRegistry.get_instance()
        PlatformHookRegistry.reset()
        inst2 = PlatformHookRegistry.get_instance()
        assert inst1 is not inst2
