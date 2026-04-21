from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

import shared_app
from mozaiksai.core.auth.dependencies import UserPrincipal


WORKSPACE = Path(__file__).resolve().parents[1]


def _read_text(relative_path: str) -> str:
    return (WORKSPACE / relative_path).read_text(encoding="utf-8")


def test_resolve_scope_from_principal_accepts_dev_body_scope() -> None:
    principal = UserPrincipal(
        user_id="anonymous",
        email=None,
        name="Anonymous User",
        roles=[],
        scopes=["access_as_user"],
        raw_claims={},
        provider="none",
        app_id=None,
    )

    app_id, user_id = shared_app._resolve_scope_from_principal(
        principal,
        app_id="demo-app",
        user_id="demo-user",
    )

    assert app_id == "demo-app"
    assert user_id == "demo-user"


def test_resolve_scope_from_principal_rejects_mismatched_bound_app_scope() -> None:
    principal = UserPrincipal(
        user_id="user-1",
        email="user@example.com",
        name="User",
        roles=[],
        scopes=["access_as_user"],
        raw_claims={},
        provider="test",
        app_id="app-1",
    )

    with pytest.raises(HTTPException) as exc_info:
        shared_app._resolve_scope_from_principal(
            principal,
            app_id="different-app",
            user_id="user-1",
        )

    assert exc_info.value.status_code == 403


def test_route_renderer_posts_app_and_user_scope_for_transition_resolution() -> None:
    source = _read_text("chat-ui/src/components/RouteRenderer.jsx")

    assert "app_id: resolvedAppId" in source
    assert "user_id: resolvedUserId" in source
    assert "option_id" in source
    assert "route_to" not in source
    assert "setAccumulatedContext(data.context_variables ?? mergedContext)" in source
    assert "const { user, config } = useChatUI();" in source


def test_workflow_start_posts_app_and_user_scope_for_triggered_workflows() -> None:
    source = _read_text("chat-ui/src/hooks/useWorkflowStart.js")

    assert "app_id: resolvedAppId" in source
    assert "user_id: resolvedUserId" in source
    assert "const { user, config } = useChatUI();" in source
