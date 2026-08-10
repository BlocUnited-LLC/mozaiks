from __future__ import annotations

from types import SimpleNamespace

import pytest

from mozaiksai.core.runtime.composition.module_executor import ModuleResult
from mozaiksai.hosts.routers import modules as module_router


class _FakeModuleExecutor:
    def __init__(self, action_schemas: dict[str, dict[str, dict]] | None = None) -> None:
        self.requests = []
        self._action_schemas = action_schemas or {}

    async def execute(self, request, context=None):
        self.requests.append(request)
        return ModuleResult(success=True, data={"ok": True, "user_id": request.user_id})


@pytest.mark.asyncio
async def test_module_context_user_id_override_reaches_executor(monkeypatch):
    executor = _FakeModuleExecutor()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                failed_module_names=[],
                executor_registry=SimpleNamespace(module_executor=executor),
                module_action_surfaces={},
            )
        ),
        query_params={},
        headers={},
    )

    async def _scope(**kwargs):
        requested = kwargs["requested_scope"]
        return {
            "app_id": requested["app_id"],
            "tenant_id": requested["tenant_id"],
            "workspace_id": requested["workspace_id"],
            "user_id": requested["user_id"],
            "permissions": [],
        }

    monkeypatch.setattr(module_router, "is_auth_enabled", lambda: False)
    monkeypatch.setattr(
        module_router,
        "get_platform_hooks",
        lambda: SimpleNamespace(call_module_scope=_scope),
    )

    result = await module_router._execute_module_action(
        module_name="workspace_support",
        action_name="create_support_request",
        request=request,
        principal=None,
        params={"message": "help"},
        context_overrides={"app_id": "mozaiks-factory", "user_id": "demo-user"},
    )

    assert result == {"ok": True, "user_id": "demo-user"}
    assert executor.requests[0].app_id == "mozaiks-factory"
    assert executor.requests[0].user_id == "demo-user"


def test_reconcile_reserved_params_keeps_declared_user_id_and_skips_context_promotion():
    """An action's own "user_id" input (e.g. add_member's target user) must
    stay in params and never be promoted into the trusted execution context.
    Only an explicit context.user_id should be able to set the execution
    identity."""
    params = {"community_id": "c1", "user_id": "target-user", "role": "contributor"}
    context_overrides = {"app_id": "demo-app"}

    module_router._reconcile_reserved_params(
        params,
        context_overrides,
        action_input_properties={"community_id": {}, "user_id": {}, "role": {}},
    )

    assert params["user_id"] == "target-user"
    assert "user_id" not in context_overrides
    assert context_overrides["app_id"] == "demo-app"


def test_reconcile_reserved_params_keeps_and_promotes_declared_app_id():
    """app_id stays available in params (many actions declare it as a
    resource-scoping business input) and is also promoted into context when
    the caller omits it there."""
    params = {"name": "My Community", "app_id": "demo-app"}
    context_overrides: dict[str, object] = {}

    module_router._reconcile_reserved_params(
        params,
        context_overrides,
        action_input_properties={"name": {}, "app_id": {}},
    )

    assert params["app_id"] == "demo-app"
    assert context_overrides["app_id"] == "demo-app"


def test_reconcile_reserved_params_strips_undeclared_reserved_key():
    """A reserved word the action does NOT declare as a business input is
    promoted into context and removed from params, since the handler method
    is called as handler.method(ctx, **params) and would otherwise raise a
    TypeError for an unexpected keyword argument."""
    params = {"proposal_id": "p1", "app_id": "demo-app"}
    context_overrides: dict[str, object] = {}

    module_router._reconcile_reserved_params(
        params,
        context_overrides,
        action_input_properties={"proposal_id": {}},
    )

    assert "app_id" not in params
    assert context_overrides["app_id"] == "demo-app"


def test_split_post_body_raw_shape_preserves_reserved_keys_for_later_reconciliation():
    """The legacy flat (non-enveloped) body shape used by the app's own
    frontend module API client must not strip reserved words up front, since
    whether a word like app_id belongs in params depends on the target
    action's schema, which _split_post_body does not have access to."""
    params, context_overrides = module_router._split_post_body(
        {"app_id": "demo-app", "community_id": "c1"}
    )

    assert params == {"app_id": "demo-app", "community_id": "c1"}
    assert context_overrides == {}


@pytest.mark.asyncio
async def test_module_action_own_user_id_param_does_not_hijack_actor_identity(monkeypatch):
    """Regression test for a real authz bug: dispatching an action whose input
    schema declares its own "user_id" param (a target subject, e.g.
    community_membership.add_member's user being added) must not overwrite the
    authenticated/dev-resolved execution context's user_id with that target's
    user_id. Exercised through the enveloped {params, context} shape."""
    executor = _FakeModuleExecutor(
        action_schemas={
            "community_membership": {
                "add_member": {
                    "input": {
                        "properties": {"community_id": {}, "user_id": {}, "role": {}},
                    },
                },
            },
        }
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                failed_module_names=[],
                executor_registry=SimpleNamespace(module_executor=executor),
                module_action_surfaces={},
            )
        ),
        query_params={},
        headers={},
    )

    async def _scope(**kwargs):
        requested = kwargs["requested_scope"]
        return {
            "app_id": requested["app_id"],
            "tenant_id": requested["tenant_id"],
            "workspace_id": requested["workspace_id"],
            "user_id": requested["user_id"],
            "permissions": [],
        }

    monkeypatch.setattr(module_router, "is_auth_enabled", lambda: False)
    monkeypatch.setattr(
        module_router,
        "get_platform_hooks",
        lambda: SimpleNamespace(call_module_scope=_scope),
    )

    params, context_overrides = module_router._split_post_body(
        {
            "params": {"community_id": "c1", "user_id": "target-user", "role": "contributor"},
            "context": {"app_id": "demo-app"},
        }
    )

    result = await module_router._execute_module_action(
        module_name="community_membership",
        action_name="add_member",
        request=request,
        principal=SimpleNamespace(user_id="actor-owner", scopes=[], tenant_id=None, workspace_id=None, app_id=None),
        params=params,
        context_overrides=context_overrides,
    )

    assert result == {"ok": True, "user_id": "actor-owner"}
    assert executor.requests[0].user_id == "actor-owner"
    assert executor.requests[0].params["user_id"] == "target-user"


@pytest.mark.asyncio
async def test_module_action_own_user_id_param_does_not_hijack_actor_identity_raw_body(monkeypatch):
    """Same regression as above, but through the legacy flat/raw body shape —
    this is the exact shape the app's own frontend moduleApi.js client sends,
    so it must be protected the same way as the enveloped shape."""
    executor = _FakeModuleExecutor(
        action_schemas={
            "community_membership": {
                "add_member": {
                    "input": {
                        "properties": {"community_id": {}, "user_id": {}, "role": {}},
                    },
                },
            },
        }
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                failed_module_names=[],
                executor_registry=SimpleNamespace(module_executor=executor),
                module_action_surfaces={},
            )
        ),
        query_params={},
        headers={},
    )

    async def _scope(**kwargs):
        requested = kwargs["requested_scope"]
        return {
            "app_id": requested["app_id"],
            "tenant_id": requested["tenant_id"],
            "workspace_id": requested["workspace_id"],
            "user_id": requested["user_id"],
            "permissions": [],
        }

    monkeypatch.setattr(module_router, "is_auth_enabled", lambda: False)
    monkeypatch.setattr(
        module_router,
        "get_platform_hooks",
        lambda: SimpleNamespace(call_module_scope=_scope),
    )

    params, context_overrides = module_router._split_post_body(
        {"community_id": "c1", "user_id": "target-user", "role": "contributor"}
    )

    result = await module_router._execute_module_action(
        module_name="community_membership",
        action_name="add_member",
        request=request,
        principal=SimpleNamespace(user_id="actor-owner", scopes=[], tenant_id=None, workspace_id=None, app_id=None),
        params=params,
        context_overrides=context_overrides,
    )

    assert result == {"ok": True, "user_id": "actor-owner"}
    assert executor.requests[0].user_id == "actor-owner"
    assert executor.requests[0].params["user_id"] == "target-user"


@pytest.mark.asyncio
async def test_module_action_raw_body_keeps_declared_app_id_param(monkeypatch):
    """Regression test for the app_id counterpart of the same bug class: an
    action such as community_membership.get_app_community_summary declares
    app_id as a required business input. Dispatched through the legacy flat
    body shape, app_id must survive into params (not just context) or schema
    validation on the real executor would fail with
    "'app_id' is a required property"."""
    executor = _FakeModuleExecutor(
        action_schemas={
            "community_membership": {
                "get_app_community_summary": {
                    "input": {
                        "properties": {"app_id": {}, "community_id": {}},
                    },
                },
            },
        }
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                failed_module_names=[],
                executor_registry=SimpleNamespace(module_executor=executor),
                module_action_surfaces={},
            )
        ),
        query_params={},
        headers={},
    )

    async def _scope(**kwargs):
        requested = kwargs["requested_scope"]
        return {
            "app_id": requested["app_id"],
            "tenant_id": requested["tenant_id"],
            "workspace_id": requested["workspace_id"],
            "user_id": requested["user_id"],
            "permissions": [],
        }

    monkeypatch.setattr(module_router, "is_auth_enabled", lambda: False)
    monkeypatch.setattr(
        module_router,
        "get_platform_hooks",
        lambda: SimpleNamespace(call_module_scope=_scope),
    )

    params, context_overrides = module_router._split_post_body(
        {"app_id": "demo-app", "community_id": "c1"}
    )

    await module_router._execute_module_action(
        module_name="community_membership",
        action_name="get_app_community_summary",
        request=request,
        principal=SimpleNamespace(user_id="actor-owner", scopes=[], tenant_id=None, workspace_id=None, app_id=None),
        params=params,
        context_overrides=context_overrides,
    )

    assert executor.requests[0].params["app_id"] == "demo-app"
    assert executor.requests[0].app_id == "demo-app"
