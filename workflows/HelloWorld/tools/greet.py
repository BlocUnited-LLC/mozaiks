"""
greet — UI tool that records the user's name, renders a GreetingCard in the
chat UI, then waits for the user's acknowledgement before returning.

This is your first tool.  To build your own:
  1. Rename the function and update tools.yaml (function: + file:).
  2. Replace the payload dict with whatever data your component needs.
  3. Change tool_id to match ui.component in tools.yaml.
  4. Read any user response fields out of `response` after use_ui_tool returns.
"""

import logging
from typing import Annotated, Any, Dict, Optional

from mozaiksai.core.workflow.outputs.ui_tools import UIToolError, use_ui_tool

_logger = logging.getLogger("tools.greet")


async def greet(
    *,
    name: Annotated[str, "The user's name to greet"],
    context_variables: Annotated[Optional[Any], "Context variables provided by AG2"] = None,
) -> Dict[str, Any]:
    """Render a GreetingCard artifact and wait for user acknowledgement."""

    # ── 1. Pull routing keys from context_variables ───────────────────────────
    chat_id: Optional[str] = None
    workflow_name: str = "HelloWorld"
    if context_variables is not None:
        try:
            chat_id = context_variables.get("chat_id")
            workflow_name = context_variables.get("workflow_name") or workflow_name
        except Exception as exc:
            _logger.debug("Could not read routing context: %s", exc)

    if not chat_id:
        _logger.warning("greet tool called without chat_id in context_variables")
        return {"status": "error", "message": "chat_id missing from context_variables"}

    # ── 2. Build the data payload ─────────────────────────────────────────────
    greeting = f"Hello, {name}! 👋 Welcome to Mozaiks."

    ui_payload: Dict[str, Any] = {
        # These keys are forwarded as props to the GreetingCard React component.
        "message": greeting,
        "name": name,
    }

    # ── 3. Persist to context_variables ──────────────────────────────────────
    if context_variables is not None:
        try:
            context_variables.set("user_name", name)
            context_variables.set("greeting", greeting)
        except Exception as exc:
            _logger.debug("Could not persist greeting to context_variables: %s", exc)

    # ── 4. Emit the UI component and wait for user response ───────────────────
    #   tool_id   → must match ui.component in tools.yaml ("GreetingCard")
    #   display   → must match ui.mode in tools.yaml ("inline")
    #   The runtime blocks here until the user interacts with the component.
    try:
        response = await use_ui_tool(
            tool_id="GreetingCard",
            payload=ui_payload,
            chat_id=chat_id,
            workflow_name=workflow_name,
            display="inline",
        )
    except UIToolError as exc:
        _logger.error("GreetingCard UI interaction failed: %s", exc)
        return {"status": "error", "message": str(exc)}

    # ── 5. Read the user's response and persist final state ───────────────────
    # `response` is whatever the GreetingCard component submits back.
    # Add any fields your component returns (e.g. response.get("acknowledged")).
    _logger.info("Greeted user '%s'; UI response: %s", name, response.get("status"))

    if context_variables is not None:
        try:
            context_variables.set("greeting_complete", True)
        except Exception as exc:
            _logger.debug("Could not persist greeting_complete: %s", exc)

    return {
        "status": response.get("status", "success"),
        "greeting": greeting,
        "name": name,
        "ui_response": response,
    }
