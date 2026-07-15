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
