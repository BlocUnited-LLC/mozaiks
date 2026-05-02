from typing import Any, Dict

from logs.logging_config import get_workflow_logger
from mozaiksai.core.workflow.ui_tools import UIToolError, use_ui_tool


def _extract_response_text(response: Dict[str, Any]) -> str:
    data = response.get("data")
    if isinstance(data, dict):
        for key in ("text", "user_input", "user_response", "value"):
            value = data.get(key)
            if value is not None:
                return str(value)
    for key in ("text", "user_input", "user_response", "value"):
        value = response.get(key)
        if value is not None:
            return str(value)
    return ""


async def collect_runtime_confirmation(
    prompt_text: str = "Type approved to confirm the runtime tool-call smoke path.",
    expected_text: str = "approved",
    context_variables: Any = None,
) -> Dict[str, Any]:
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
        return {"status": "error", "message": "workflow_name is required"}

    wf_logger = get_workflow_logger(workflow_name=workflow_name, chat_id=chat_id)
    try:
        response = await use_ui_tool(
            "UserInputRequest",
            {
                "component_type": "UserInputRequest",
                "prompt": prompt_text,
                "input_request_id": "runtime_tool_call_smoke",
                "interaction_type": "input_request",
            },
            chat_id=chat_id,
            workflow_name=str(workflow_name),
        )
    except UIToolError as exc:
        return {"status": "error", "message": f"UI interaction failed: {exc}"}
    except Exception as exc:  # pragma: no cover - defensive guard
        wf_logger.error("Runtime tool-call smoke interaction failed: %s", exc, exc_info=True)
        return {"status": "error", "message": "UI interaction failure"}

    response_text = _extract_response_text(response).strip()
    normalized_expected = str(expected_text or "").strip().lower()
    confirmed = bool(response_text) and response_text.lower() == normalized_expected

    return {
        "status": "success",
        "response_text": response_text,
        "confirmed": confirmed,
        "expected_text": expected_text,
        "tool_call_id": response.get("ui_event_id") or response.get("event_id"),
    }
