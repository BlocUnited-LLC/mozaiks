from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mozaiksai.core.auth import UserPrincipal
from mozaiksai.hosts import runtime


@pytest.mark.parametrize(
    ("result", "expected_status"),
    [
        ({"status": "success", "route": "persisted_reply"}, 200),
        ({"status": "busy", "route": "chat_lock_busy"}, 409),
        (
            {
                "status": "error",
                "route": "terminal_session",
                "error_code": "WORKFLOW_SESSION_TERMINAL",
            },
            409,
        ),
    ],
)
def test_http_chat_input_reports_bridge_refusal(
    monkeypatch: pytest.MonkeyPatch, result: dict[str, str], expected_status: int
) -> None:
    class Chats:
        async def find_one(self, query, projection):
            assert query["_id"] == "chat-1"
            assert query["user_id"] == "user-1"
            assert query["app_id"] == "app-1"
            return {"_id": "chat-1"}

    class Transport:
        async def handle_user_input_from_api(self, **kwargs):
            assert kwargs == {
                "chat_id": "chat-1",
                "user_id": "user-1",
                "workflow_name": "AskAgent",
                "message": "Continue",
                "app_id": "app-1",
            }
            return result

    async def chat_coll():
        return Chats()

    principal = UserPrincipal(
        user_id="user-1",
        email=None,
        name=None,
        roles=[],
        scopes=[],
        raw_claims={},
        auth_provenance="token_validated",
    )
    monkeypatch.setattr(runtime, "_chat_coll", chat_coll)
    monkeypatch.setattr(runtime, "simple_transport", Transport())
    monkeypatch.setitem(runtime.app.dependency_overrides, runtime.require_user_scope, lambda: principal)

    client = TestClient(runtime.app, raise_server_exceptions=False, base_url="http://localhost:8000")
    response = client.post(
        "/chat/app-1/chat-1/user-1/input",
        json={"message": "Continue", "workflow_name": "AskAgent"},
    )

    assert response.status_code == expected_status, response.text
    assert response.json()["result"] == result
    if expected_status != 200:
        assert response.json()["status"] == "Message was not accepted."
