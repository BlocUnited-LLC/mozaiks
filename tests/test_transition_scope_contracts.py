from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from mozaiksai.core.auth.dependencies import UserPrincipal
from mozaiksai.hosts import platform as platform_app
from mozaiksai.hosts.routers import transitions as transitions_router

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

    app_id, user_id = platform_app.resolve_scope_from_principal(
        principal,
        app_id="demo-app",
        user_id="demo-user",
    )

    assert app_id == "demo-app"
    assert user_id == "demo-user"


def test_resolve_scope_from_principal_uses_default_user_for_anonymous_scope() -> None:
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

    app_id, user_id = platform_app.resolve_scope_from_principal(
        principal,
        app_id="demo-app",
        default_user_id="demo-user",
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
        platform_app.resolve_scope_from_principal(
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


@pytest.mark.asyncio
async def test_transition_resolve_workflow_response_includes_context_variables(monkeypatch) -> None:
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
    selected_context = {
        "app_type": "brownfield_app",
        "brownfield_build_path": "light_integration",
    }

    async def fake_launch_transition(**kwargs):  # noqa: ANN003
        assert kwargs["context_variables"] == {"app_type": "brownfield_app"}
        return SimpleNamespace(
            resolution_type="transition",
            option_id="light_integration",
            context_variables=selected_context,
            next_transition_id="brownfield_repo_input",
            journey_id="brownfield_app_adoption",
            transition=SimpleNamespace(
                model_dump=lambda exclude_none=True: {
                    "id": "brownfield_repo_input",
                    "transition_type": "user_choice_context",
                    "ui": {"component": "BrownfieldRepoInput", "mode": "screen"},
                }
            ),
        )

    monkeypatch.setattr(transitions_router, "launch_transition", fake_launch_transition)

    response = await transitions_router.resolve_transition_route(
        transitions_router.TransitionResolveRequest(
            transition_id="brownfield_path_selector",
            option_id="light_integration",
            app_id="app_1",
            user_id="user_1",
            context_variables={"app_type": "brownfield_app"},
        ),
        principal=principal,
    )

    assert response["resolution_type"] == "transition"
    assert response["transition"]["id"] == "brownfield_repo_input"
    assert response["next_transition_id"] == "brownfield_repo_input"
    assert response["context_variables"] == selected_context


def test_session_state_route_accepts_explicit_local_scope() -> None:
    source = _read_text("mozaiksai/hosts/routers/transitions.py")

    assert '@router.get("/api/session/state")' in source
    assert "app_id: str | None = Query(default=None)" in source
    assert "user_id: str | None = Query(default=None)" in source
    assert "resolve_scope_from_principal(" in source
    assert "app_id=scoped_app_id" in source
    assert "user_id=scoped_user_id" in source


def test_workflow_start_posts_app_and_user_scope_for_triggered_workflows() -> None:
    source = _read_text("chat-ui/src/hooks/useWorkflowStart.js")

    assert "app_id: resolvedAppId" in source
    assert "user_id: resolvedUserId" in source
    assert "const { user, config } = useChatUI();" in source


def test_chat_page_transition_handoff_persists_workflow_before_reconnect() -> None:
    source = _read_text("chat-ui/src/pages/ChatPage.js")
    startup_source = _read_text("chat-ui/src/hooks/useChatStartupEffects.js")
    controller_source = _read_text("chat-ui/src/hooks/useConversationModeController.js")

    assert "setStoredActiveWorkflowName(resolvedWorkflowName)" in source
    assert "currentChatId\n        ? activeResolvedWorkflow || urlResolvedWorkflow" in source
    assert "workflowConfig.resolveKnownWorkflowName(getStoredActiveWorkflowName())" in source
    assert "buildWorkflowResolutionCandidates({" in source
    assert "includeAvailable: Boolean(queryChatId)" in source
    assert "includeAvailable: candidateChatId === queryChatId" in source
    assert "resolvedCandidateWorkflow" in source
    assert "const expectedConnectionWorkflow = routeWorkflowName || preferredConnectionWorkflow" in source
    assert "const existingConnectionWorkflow = workflowConfig.resolveKnownWorkflowName(wsRef.current?.workflowName)" in source
    assert "existingConnectionWorkflow === expectedConnectionWorkflow" in source
    assert (
        "const workflowForUrl =\n"
        "      workflowConfig.resolveKnownWorkflowName(currentWorkflowName)\n"
        "      || workflowConfig.resolveKnownWorkflowName(activeWorkflowName)\n"
        "      || workflowConfig.resolveKnownWorkflowName(getStoredActiveWorkflowName())"
    ) in source
    assert "includeAvailable: true" in startup_source
    assert "resolveWorkflowForChat({" in startup_source
    assert "resolvedWorkflowForChat || preferredWorkflow || workflowFromQuery" in startup_source
    assert source.count("navigate(`/chat?${chatParams.toString()}`)") >= 2
    assert "const targetWorkflow = workflowName" in controller_source
    assert "resolveKnownWorkflowName(workflowName) || workflowName" in controller_source
    assert "|| resolveWorkflow()" in controller_source

