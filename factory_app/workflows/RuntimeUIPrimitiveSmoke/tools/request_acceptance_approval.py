from __future__ import annotations

from typing import Any

from mozaiksai.core.workflow.ui_tools import UIToolError, use_ui_tool


def _response_value(response: dict[str, Any], key: str) -> Any:
    data = response.get("data")
    if isinstance(data, dict) and key in data:
        return data.get(key)
    return response.get(key)


async def request_acceptance_approval(context_variables: Any = None) -> dict[str, Any]:
    chat_id = None
    workflow_name = None

    if context_variables is not None and hasattr(context_variables, "get"):
        try:
            chat_id = context_variables.get("chat_id")
            workflow_name = context_variables.get("workflow_name")
        except Exception:
            chat_id = None
            workflow_name = None

    if not workflow_name:
        return {"status": "error", "approved": False, "action": "error", "rationale": "workflow_name is required"}

    payload = {
        "component_type": "ApprovalCard",
        "interaction_type": "ui_tool",
        "title": "Approve the runtime UI primitive smoke checkpoint",
        "summary": (
            "The composer reply was captured. Confirm the structured approval lane "
            "before the artifact viewer surface is emitted."
        ),
        "checkpoints": [
            "Composer reply routed through the standard chat input",
            "Structured inline approval returned through tool_call_response",
            "Artifact surface opens after structured approval completes",
        ],
    }

    try:
        response = await use_ui_tool(
            "ApprovalCard",
            payload,
            chat_id=chat_id,
            workflow_name=str(workflow_name),
        )
    except UIToolError as exc:
        return {
            "status": "error",
            "approved": False,
            "action": "error",
            "rationale": f"UI interaction failed: {exc}",
        }

    approved = bool(_response_value(response, "approved"))
    action = str(_response_value(response, "action") or ("approve" if approved else "request_changes")).strip()
    rationale = str(_response_value(response, "rationale") or "").strip()

    return {
        "status": str(_response_value(response, "status") or "submitted"),
        "approved": approved,
        "action": action,
        "rationale": rationale,
        "tool_call_id": response.get("ui_event_id") or response.get("event_id"),
    }
