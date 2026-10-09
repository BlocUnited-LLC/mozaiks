"""Token wallet denials retain their recovery contract through module HTTP dispatch."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor
from mozaiksai.core.tokens.guard import TokenUsageDecision, TokenUsageDenied
from mozaiksai.hosts.routers.modules import router as modules_router


@pytest.mark.parametrize(
    ("decision", "expected_status", "expected_error"),
    [
        (
            TokenUsageDecision(
                allowed=False,
                reason="insufficient_balance",
                error_code="INSUFFICIENT_TOKENS",
                wallet_id="ai_tokens",
                balance=0,
                required_tokens=1,
                recovery_action="contact_admin",
                contact_route="/help",
            ),
            402,
            "Insufficient token balance to run this AI action.",
        ),
        (
            TokenUsageDecision(
                allowed=False,
                reason="missing_user_id",
                error_code="TOKEN_USAGE_SCOPE_MISSING",
                wallet_id="ai_tokens",
            ),
            403,
            "Token usage denied: missing_user_id",
        ),
    ],
)
def test_module_http_preserves_typed_token_denial(
    monkeypatch: pytest.MonkeyPatch,
    decision: TokenUsageDecision,
    expected_status: int,
    expected_error: str,
) -> None:
    class Handler:
        async def generate(self, ctx):  # noqa: ANN001
            raise TokenUsageDenied(decision)

    class AuditLogger:
        async def log_module_action(self, **kwargs):  # noqa: ANN003
            return None

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setattr(
        "mozaiksai.core.runtime.composition.module_executor.get_audit_logger",
        lambda: AuditLogger(),
    )
    executor = ModuleExecutor()
    executor.register("reports", Handler(), action_method_map={"generate": "generate"})
    app = FastAPI()
    app.state.module_action_surfaces = {"reports": {"generate": "public"}}
    app.state.loaded_app_id = "fixture-app"
    app.state.failed_module_names = []
    app.state.executor_registry = SimpleNamespace(module_executor=executor)
    app.include_router(modules_router)

    response = TestClient(app, raise_server_exceptions=False).post(
        "/api/modules/reports/generate", json={}
    )

    assert response.status_code == expected_status, response.json()
    assert response.json() == {
        "detail": {
            "error": expected_error,
            "error_code": decision.error_code,
            "module": "reports",
            "action": "generate",
            "extra_data": decision.to_error_metadata(),
        }
    }
