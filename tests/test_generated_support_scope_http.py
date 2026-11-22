"""App-local support HTTP authority stays separate from hosted support roles."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from mozaiksai.core.runtime.app.module_loader import ModuleLoader
from tests.test_generated_messaging_scope_http import runtime as runtime  # noqa: F401

SUPPORT = Path(__file__).resolve().parents[1] / "factory_app/build_context/support/templates"


def test_generated_app_admin_reply_readback_and_wrong_app_denial(runtime, tmp_path):
    app = runtime.client.app
    executor = app.state.executor_registry.module_executor
    root = tmp_path / "app"
    shutil.copytree(SUPPORT / "modules/support", root / "modules/support")
    loaded = ModuleLoader(str(root)).load("support")
    executor.register_loaded_module(loaded)
    app.state.module_action_surfaces["support"] = loaded.action_api_surface_map
    contract = json.loads((SUPPORT / "data/contract.json").read_text(encoding="utf-8"))
    executor._data_contract["surfaces"].extend(contract["surfaces"])

    # Existing app-local permissions are fixture credentials, with no hosted role.
    customer = runtime.token(user="customer", scopes="support.submit")
    admin = runtime.token(user="app-admin", scopes="support.submit support.read support.manage")

    def action(headers, name, params, context=None):
        body = {"params": params}
        if context is not None:
            body["context"] = context
        return runtime.client.post(f"/api/modules/support/{name}", json=body, headers=headers)

    created = action(
        customer, "create_support_request", {"message": "Please help", "subject": "My app"}
    )
    assert created.status_code == 200, created.text
    request_id = created.json()["request"]["request_id"]
    inbox = action(admin, "list_support_requests", {"scope": "app", "status": "all"})
    assert inbox.status_code == 200, inbox.text
    assert [row["request_id"] for row in inbox.json()["requests"]] == [request_id]
    replied = action(
        admin, "reply_support_request", {"request_id": request_id, "body": "App admin response"}
    )
    assert replied.status_code == 200 and replied.json()["success"], replied.text
    readback = action(customer, "get_support_conversation", {"request_id": request_id})
    assert readback.status_code == 200, readback.text
    assert [row["body"] for row in readback.json()["messages"]] == [
        "Please help",
        "App admin response",
    ]

    wrong_app = {"app_id": "foreign-app"}
    for name, params in (
        ("list_support_requests", {"scope": "app", "status": "all"}),
        ("get_support_conversation", {"request_id": request_id}),
        ("reply_support_request", {"request_id": request_id, "body": "Cross-app attempt"}),
    ):
        response = action(admin, name, params, context=wrong_app)
        assert response.status_code == 403, response.text
    ordinary = action(customer, "list_support_requests", {"scope": "app", "status": "all"})
    assert ordinary.status_code == 403, ordinary.text
    stranger = runtime.token(user="stranger", scopes="support.submit")
    assert (
        action(stranger, "get_support_conversation", {"request_id": request_id}).status_code == 403
    )
    final = action(customer, "get_support_conversation", {"request_id": request_id})
    assert [row["body"] for row in final.json()["messages"]] == [
        "Please help",
        "App admin response",
    ]
