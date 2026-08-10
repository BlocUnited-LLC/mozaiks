from __future__ import annotations

from types import SimpleNamespace

import pytest

from mozaiksai.core.runtime.composition.module_executor import ModuleResult
from mozaiksai.hosts.routers import modules as module_router


class _FakeModuleExecutor:
    def __init__(self) -> None:
        self.requests = []

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


def test_split_post_body_does_not_promote_action_user_id_param():
    """An action's own "user_id" input (e.g. add_member's target user) must
    never be promoted into the trusted execution context. Only an explicit
    context.user_id should be able to set the execution identity."""
    params, context_overrides = module_router._split_post_body(
        {
            "params": {"community_id": "c1", "user_id": "target-user", "role": "contributor"},
            "context": {"app_id": "demo-app"},
        }
    )

    assert params["user_id"] == "target-user"
    assert "user_id" not in context_overrides
    assert context_overrides["app_id"] == "demo-app"


def test_split_post_body_still_promotes_app_id_for_resource_scoped_actions():
    """app_id remains intentionally promoted from params into context when the
    caller declares it as a business input and omits it from context."""
    params, context_overrides = module_router._split_post_body(
        {
            "params": {"name": "My Community", "app_id": "demo-app"},
        }
    )

    assert params["app_id"] == "demo-app"
    assert context_overrides["app_id"] == "demo-app"


@pytest.mark.asyncio
async def test_module_action_own_user_id_param_does_not_hijack_actor_identity(monkeypatch):
    """Regression test for a real authz bug: dispatching an action whose input
    schema declares its own "user_id" param (a target subject, e.g.
    community_membership.add_member's user being added) must not overwrite the
    authenticated/dev-resolved execution context's user_id with that target's
    user_id."""
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
        principal=SimpleNamespace(user_id="actor-owner", scopes=[], tenant_id=None, workspace_id=None),
        params=params,
        context_overrides=context_overrides,
    )

    assert result == {"ok": True, "user_id": "actor-owner"}
    assert executor.requests[0].user_id == "actor-owner"
    assert executor.requests[0].params["user_id"] == "target-user"
