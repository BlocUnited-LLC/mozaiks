"""
ModuleExecutor dispatch tests — comprehensive coverage for dispatch lifecycle:
module/action resolution, permission enforcement, entitlement gates, schema
validation, action_method_map aliasing, event envelope structure, sync/async
dispatch, error handling, and registry queries.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from mozaiksai.core.ports.entitlement import EntitlementResult
from mozaiksai.core.runtime.composition.module_executor import (
    ModuleExecutor,
    ModuleRequest,
    _validate_schema,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _EchoHandler:
    """Sync action: echoes params back."""
    def echo(self, ctx, **kwargs) -> dict:
        return {"echo": kwargs}


class _AsyncEchoHandler:
    """Async action: echoes params back."""
    async def echo_async(self, ctx, **kwargs) -> dict:
        return {"async_echo": kwargs}


class _ErrorHandler:
    """Action that always raises."""
    async def blow_up(self, ctx) -> None:
        raise ValueError("something went wrong")

    def bad_params(self, ctx, *, required_arg: str) -> dict:
        return {"ok": required_arg}

    async def restricted(self, ctx) -> None:
        raise PermissionError("contacts.admin permission required")


def _request(
    module: str = "contacts",
    action: str = "echo",
    params: dict | None = None,
    app_id: str = "app-1",
    user_id: str | None = "user-1",
    tenant_id: str | None = None,
    workspace_id: str | None = None,
    granted_permissions: list[str] | None = None,
) -> ModuleRequest:
    return ModuleRequest(
        module=module,
        action=action,
        params=params or {},
        app_id=app_id,
        user_id=user_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        granted_permissions=granted_permissions,
    )


# ---------------------------------------------------------------------------
# 1. Module/action resolution
# ---------------------------------------------------------------------------

class TestModuleActionResolution:
    @pytest.mark.asyncio
    async def test_module_not_found_returns_error(self):
        ex = ModuleExecutor()
        result = await ex.execute(_request(module="nonexistent"))
        assert result.success is False
        assert result.error_code == "MODULE_NOT_FOUND"
        assert "nonexistent" in result.error

    @pytest.mark.asyncio
    async def test_action_not_found_returns_error(self):
        ex = ModuleExecutor()
        ex.register("contacts", _EchoHandler())
        result = await ex.execute(_request(action="no_such_action"))
        assert result.success is False
        assert result.error_code == "ACTION_NOT_FOUND"
        assert "no_such_action" in result.error

    @pytest.mark.asyncio
    async def test_sync_action_dispatched_and_result_returned(self):
        ex = ModuleExecutor()
        ex.register("contacts", _EchoHandler())
        result = await ex.execute(_request(params={"name": "Alice"}))
        assert result.success is True
        assert result.data == {"echo": {"name": "Alice"}}

    @pytest.mark.asyncio
    async def test_async_action_dispatched_and_result_returned(self):
        ex = ModuleExecutor()
        ex.register("contacts", _AsyncEchoHandler())
        result = await ex.execute(_request(action="echo_async", params={"x": 1}))
        assert result.success is True
        assert result.data == {"async_echo": {"x": 1}}

    @pytest.mark.asyncio
    async def test_action_method_map_remaps_action_id(self):
        """action_method_map: public action 'list' → handler method 'echo'"""
        ex = ModuleExecutor()
        ex.register(
            "contacts",
            _EchoHandler(),
            action_method_map={"list": "echo"},
        )
        result = await ex.execute(_request(action="list", params={"q": "test"}))
        assert result.success is True
        assert result.data == {"echo": {"q": "test"}}


# ---------------------------------------------------------------------------
# 2. Error handling
# ---------------------------------------------------------------------------

class TestActionErrorHandling:
    @pytest.mark.asyncio
    async def test_exception_in_action_returns_execution_error(self):
        ex = ModuleExecutor()
        ex.register("contacts", _ErrorHandler())
        result = await ex.execute(_request(action="blow_up"))
        assert result.success is False
        assert result.error_code == "EXECUTION_ERROR"
        # Raw exception text is suppressed; only a generic action-level message is returned.
        # The full exception is logged server-side (see MODULE_ACTION_ERROR log above).
        assert "blow_up" in result.error
        assert "something went wrong" not in result.error

    @pytest.mark.asyncio
    async def test_type_error_from_missing_required_param_returns_invalid_params(self):
        ex = ModuleExecutor()
        ex.register("contacts", _ErrorHandler())
        # bad_params requires `required_arg` but we pass nothing
        result = await ex.execute(_request(action="bad_params", params={}))
        assert result.success is False
        assert result.error_code == "INVALID_PARAMS"

    @pytest.mark.asyncio
    async def test_permission_error_from_service_layer_returns_permission_denied(self):
        """PermissionError raised inside a handler maps to PERMISSION_DENIED (403), not EXECUTION_ERROR (500)."""
        ex = ModuleExecutor()
        ex.register("contacts", _ErrorHandler())
        result = await ex.execute(_request(action="restricted", granted_permissions=["contacts.read"]))
        assert result.success is False
        assert result.error_code == "PERMISSION_DENIED"
        # Generic message returned — internal permission names are not leaked to callers.
        assert result.error == "Permission denied."


# ---------------------------------------------------------------------------
# 3. Permission enforcement
# ---------------------------------------------------------------------------

class TestPermissionEnforcement:
    @pytest.mark.asyncio
    async def test_trusted_call_none_permissions_bypasses_enforcement(self):
        ex = ModuleExecutor()
        ex.register(
            "contacts",
            _EchoHandler(),
            action_permissions={"echo": ["contacts.read"]},
        )
        # granted_permissions=None → trusted internal call, no enforcement
        result = await ex.execute(_request(granted_permissions=None))
        assert result.success is True

    @pytest.mark.asyncio
    async def test_required_permission_present_allows_action(self):
        ex = ModuleExecutor()
        ex.register(
            "contacts",
            _EchoHandler(),
            action_permissions={"echo": ["contacts.read"]},
        )
        result = await ex.execute(_request(granted_permissions=["contacts.read", "other.perm"]))
        assert result.success is True

    @pytest.mark.asyncio
    async def test_required_permission_missing_denies_action(self):
        ex = ModuleExecutor()
        ex.register(
            "contacts",
            _EchoHandler(),
            action_permissions={"echo": ["contacts.read"]},
        )
        result = await ex.execute(_request(granted_permissions=["other.perm"]))
        assert result.success is False
        assert result.error_code == "PERMISSION_DENIED"

    @pytest.mark.asyncio
    async def test_empty_granted_permissions_denies_gated_action(self):
        ex = ModuleExecutor()
        ex.register(
            "contacts",
            _EchoHandler(),
            action_permissions={"echo": ["contacts.read"]},
        )
        result = await ex.execute(_request(granted_permissions=[]))
        assert result.success is False
        assert result.error_code == "PERMISSION_DENIED"

    @pytest.mark.asyncio
    async def test_action_with_no_permissions_allows_any_caller(self):
        ex = ModuleExecutor()
        ex.register(
            "contacts",
            _EchoHandler(),
            action_permissions={},  # no permissions required
        )
        result = await ex.execute(_request(granted_permissions=[]))
        assert result.success is True


# ---------------------------------------------------------------------------
# 4. Entitlement gate
# ---------------------------------------------------------------------------

class TestEntitlementGate:
    @pytest.mark.asyncio
    async def test_entitlement_granted_allows_action(self):
        granted_checker = MagicMock()
        granted_checker.check = AsyncMock(return_value=EntitlementResult(granted=True))
        ex = ModuleExecutor(entitlement_checker=granted_checker)
        ex.register(
            "wallet",
            _EchoHandler(),
            action_entitlements={"echo": "wallet.payout"},
        )
        result = await ex.execute(_request(module="wallet", granted_permissions=["wallet.manage"]))
        assert result.success is True
        granted_checker.check.assert_awaited_once_with(
            "wallet.payout",
            app_id="app-1",
            user_id="user-1",
            tenant_id=None,
            workspace_id=None,
        )

    @pytest.mark.asyncio
    async def test_entitlement_check_receives_workspace_scope(self):
        granted_checker = MagicMock()
        granted_checker.check = AsyncMock(return_value=EntitlementResult(granted=True))
        ex = ModuleExecutor(entitlement_checker=granted_checker)
        ex.register(
            "wallet",
            _EchoHandler(),
            action_entitlements={"echo": "wallet.payout"},
        )
        result = await ex.execute(
            _request(
                module="wallet",
                tenant_id="tenant-1",
                workspace_id="workspace-1",
                granted_permissions=["wallet.manage"],
            )
        )
        assert result.success is True
        granted_checker.check.assert_awaited_once_with(
            "wallet.payout",
            app_id="app-1",
            user_id="user-1",
            tenant_id="tenant-1",
            workspace_id="workspace-1",
        )

    @pytest.mark.asyncio
    async def test_entitlement_denied_blocks_action(self):
        denied_checker = MagicMock()
        denied_checker.check = AsyncMock(return_value=EntitlementResult(
            granted=False, reason="no_subscription"
        ))
        ex = ModuleExecutor(entitlement_checker=denied_checker)
        ex.register(
            "wallet",
            _EchoHandler(),
            action_entitlements={"echo": "wallet.payout"},
        )
        result = await ex.execute(_request(module="wallet", granted_permissions=["wallet.manage"]))
        assert result.success is False
        assert result.error_code == "ENTITLEMENT_REQUIRED"

    @pytest.mark.asyncio
    async def test_trusted_call_skips_entitlement_check(self):
        """When granted_permissions is None, entitlement is never checked."""
        checked = MagicMock()
        checked.check = AsyncMock(return_value=EntitlementResult(granted=False))
        ex = ModuleExecutor(entitlement_checker=checked)
        ex.register(
            "wallet",
            _EchoHandler(),
            action_entitlements={"echo": "wallet.payout"},
        )
        result = await ex.execute(_request(module="wallet", granted_permissions=None))
        assert result.success is True
        checked.check.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_action_without_entitlement_gate_never_calls_checker(self):
        checked = MagicMock()
        checked.check = AsyncMock(return_value=EntitlementResult(granted=False))
        ex = ModuleExecutor(entitlement_checker=checked)
        ex.register(
            "contacts",
            _EchoHandler(),
            action_entitlements={},  # no gate on any action
        )
        result = await ex.execute(_request(granted_permissions=["any.perm"]))
        assert result.success is True
        checked.check.assert_not_awaited()


# ---------------------------------------------------------------------------
# 5. Schema validation
# ---------------------------------------------------------------------------

class TestSchemaValidation:
    @pytest.mark.asyncio
    async def test_input_schema_invalid_params_returns_error(self):
        ex = ModuleExecutor()
        ex.register(
            "contacts",
            _EchoHandler(),
            action_schemas={
                "echo": {
                    "input": {
                        "type": "object",
                        "required": ["name"],
                        "properties": {"name": {"type": "string"}},
                    }
                }
            },
        )
        result = await ex.execute(_request(params={}))  # missing required 'name'
        assert result.success is False
        assert result.error_code == "INVALID_PARAMS"

    @pytest.mark.asyncio
    async def test_input_schema_valid_params_dispatches_action(self):
        ex = ModuleExecutor()
        ex.register(
            "contacts",
            _EchoHandler(),
            action_schemas={
                "echo": {
                    "input": {
                        "type": "object",
                        "required": ["name"],
                        "properties": {"name": {"type": "string"}},
                    }
                }
            },
        )
        result = await ex.execute(_request(params={"name": "Alice"}))
        assert result.success is True

    @pytest.mark.asyncio
    async def test_output_schema_violation_is_warn_only_not_error(self):
        """Output schema validation must not fail the caller — warning only."""
        ex = ModuleExecutor()
        ex.register(
            "contacts",
            _EchoHandler(),
            action_schemas={
                "echo": {
                    "output": {
                        "type": "object",
                        "required": ["guaranteed_field"],
                        "properties": {"guaranteed_field": {"type": "string"}},
                    }
                }
            },
        )
        # The echo handler returns {"echo": ...} which lacks 'guaranteed_field'
        result = await ex.execute(_request(params={"x": 1}))
        # Must still succeed — output schema is warn-only
        assert result.success is True

    def test_validate_schema_none_schema_passes(self):
        assert _validate_schema({"any": "data"}, None) is None

    def test_validate_schema_empty_dict_passes(self):
        assert _validate_schema({"any": "data"}, {}) is None

    def test_validate_schema_valid_object_passes(self):
        schema = {"type": "object", "required": ["x"]}
        assert _validate_schema({"x": 1}, schema) is None

    def test_validate_schema_invalid_object_returns_error_string(self):
        schema = {"type": "object", "required": ["x"]}
        error = _validate_schema({}, schema)
        assert error is not None
        assert isinstance(error, str)


# ---------------------------------------------------------------------------
# 6. Event emitter envelope
# ---------------------------------------------------------------------------

class TestEventEmitterEnvelope:
    @pytest.mark.asyncio
    async def test_event_emitter_called_with_canonical_envelope(self):
        emitted: list = []

        async def fake_emitter(event_type: str, envelope: dict) -> None:
            emitted.append((event_type, envelope))

        ex = ModuleExecutor(event_emitter=fake_emitter)

        class _EmittingHandler:
            async def action_with_emit(self, ctx) -> dict:
                await ctx.emit("contacts.created", {"name": "Alice"})
                return {"success": True}

        ex.register("contacts", _EmittingHandler())
        result = await ex.execute(_request(action="action_with_emit"))
        assert result.success is True
        assert len(emitted) == 1
        evt_type, envelope = emitted[0]
        assert evt_type == "contacts.created"
        assert envelope["type"] == "contacts.created"
        assert envelope["payload"] == {"name": "Alice"}
        assert envelope["source"]["layer"] == "module"
        assert envelope["source"]["app_id"] == "app-1"
        assert envelope["source"]["module_id"] == "contacts"
        assert envelope["actor"]["id"] == "user-1"
        assert "id" in envelope
        assert "occurred_at" in envelope

    @pytest.mark.asyncio
    async def test_event_emitter_envelope_has_no_actor_when_no_user_id(self):
        emitted: list = []

        async def fake_emitter(event_type: str, envelope: dict) -> None:
            emitted.append(envelope)

        ex = ModuleExecutor(event_emitter=fake_emitter)

        class _EmittingHandler:
            async def do_action(self, ctx) -> dict:
                await ctx.emit("anon.event", {})
                return {}

        ex.register("contacts", _EmittingHandler())
        req = _request(action="do_action", user_id=None)
        await ex.execute(req)
        assert "actor" not in emitted[0]


# ---------------------------------------------------------------------------
# 7. Registry queries
# ---------------------------------------------------------------------------

class TestRegistryQueries:
    def test_registered_modules_returns_module_names(self):
        ex = ModuleExecutor()
        ex.register("contacts", _EchoHandler())
        ex.register("tasks", _AsyncEchoHandler())
        names = ex.registered_modules()
        assert set(names) == {"contacts", "tasks"}

    def test_can_handle_returns_true_for_registered(self):
        ex = ModuleExecutor()
        ex.register("contacts", _EchoHandler())
        assert ex.can_handle("contacts") is True

    def test_can_handle_returns_false_for_unknown(self):
        ex = ModuleExecutor()
        assert ex.can_handle("nonexistent") is False

    @pytest.mark.asyncio
    async def test_health_includes_module_list(self):
        ex = ModuleExecutor()
        ex.register("contacts", _EchoHandler())
        health = await ex.health()
        assert "contacts" in health["modules"]
        assert health["count"] == 1


# ---------------------------------------------------------------------------
# 8. Payload size limits
# ---------------------------------------------------------------------------


class TestPayloadSizeLimits:
    @pytest.mark.asyncio
    async def test_oversized_params_rejected_before_dispatch(self, monkeypatch):
        """Params exceeding MODULE_PARAMS_MAX_BYTES must be rejected without dispatching."""
        # Set a very tight limit so we can trigger it with a small payload.
        monkeypatch.setenv("MODULE_PARAMS_MAX_BYTES", "10")

        dispatched: list = []

        class _SpyHandler:
            def echo(self, ctx, **kwargs) -> dict:
                dispatched.append(kwargs)
                return {"echo": kwargs}

        ex = ModuleExecutor()
        ex.register("contacts", _SpyHandler())
        # Build a params dict that serializes to > 10 bytes.
        result = await ex.execute(_request(params={"x": "a" * 100}))

        assert result.success is False
        assert result.error_code == "PAYLOAD_TOO_LARGE"
        assert not dispatched  # handler must not have been called

    @pytest.mark.asyncio
    async def test_within_limit_params_dispatched_normally(self, monkeypatch):
        monkeypatch.setenv("MODULE_PARAMS_MAX_BYTES", "10000")

        ex = ModuleExecutor()
        ex.register("contacts", _EchoHandler())
        result = await ex.execute(_request(params={"name": "Alice"}))

        assert result.success is True
        assert result.data == {"echo": {"name": "Alice"}}

    @pytest.mark.asyncio
    async def test_oversized_response_blocked(self, monkeypatch):
        """A module returning more than MODULE_RESPONSE_MAX_BYTES must yield an error result."""
        monkeypatch.setenv("MODULE_RESPONSE_MAX_BYTES", "10")

        class _BigResponseHandler:
            def echo(self, ctx, **kwargs) -> dict:
                return {"data": "x" * 10000}

        ex = ModuleExecutor()
        ex.register("contacts", _BigResponseHandler())
        result = await ex.execute(_request())

        assert result.success is False
        assert result.error_code == "RESPONSE_TOO_LARGE"

    @pytest.mark.asyncio
    async def test_none_response_not_size_checked(self, monkeypatch):
        """Actions that return None should not trigger the size gate."""
        monkeypatch.setenv("MODULE_RESPONSE_MAX_BYTES", "1")

        class _NoneHandler:
            def echo(self, ctx, **kwargs):
                return None

        ex = ModuleExecutor()
        ex.register("contacts", _NoneHandler())
        result = await ex.execute(_request())

        assert result.success is True
        assert result.data is None


# ---------------------------------------------------------------------------
# 9. Action timeout
# ---------------------------------------------------------------------------


class TestActionTimeout:
    @pytest.mark.asyncio
    async def test_async_action_times_out(self, monkeypatch):
        """An async action that exceeds MODULE_ACTION_TIMEOUT_SECONDS is cancelled."""
        monkeypatch.setenv("MODULE_ACTION_TIMEOUT_SECONDS", "0.05")

        class _SlowHandler:
            async def echo(self, ctx, **kwargs) -> dict:
                await asyncio.sleep(10)
                return {"done": True}

        ex = ModuleExecutor()
        ex.register("contacts", _SlowHandler())
        result = await ex.execute(_request())

        assert result.success is False
        assert result.error_code == "ACTION_TIMEOUT"
        assert "timed out" in result.error

    @pytest.mark.asyncio
    async def test_async_action_completes_within_timeout(self, monkeypatch):
        """Fast actions complete normally even with a short timeout."""
        monkeypatch.setenv("MODULE_ACTION_TIMEOUT_SECONDS", "30")

        ex = ModuleExecutor()
        ex.register("contacts", _EchoHandler())
        result = await ex.execute(_request(params={"x": 1}))

        assert result.success is True

    @pytest.mark.asyncio
    async def test_timeout_disabled_when_zero(self, monkeypatch):
        """MODULE_ACTION_TIMEOUT_SECONDS=0 disables the timeout."""
        monkeypatch.setenv("MODULE_ACTION_TIMEOUT_SECONDS", "0")

        ex = ModuleExecutor()
        ex.register("contacts", _EchoHandler())
        result = await ex.execute(_request(params={"x": 1}))

        assert result.success is True
