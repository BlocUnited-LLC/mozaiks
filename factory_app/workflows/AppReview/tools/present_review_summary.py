from __future__ import annotations

from typing import Any

from factory_app.workflows.AppReview.tools.review_context import build_review_summary_payload
from mozaiksai.core.workflow.ui_tools import emit_ui_surface


async def present_review_summary(context_variables: Any | None = None) -> dict[str, Any]:
    """Called by ReviewAgent on its first turn.

    Reads build validation context from context_variables and emits an
    AppReviewSummary UI surface artifact in the chat.
    """
    ctx = context_variables or {}

    chat_id = str(ctx.get("chat_id") or "").strip() or None
    workflow_name = str(ctx.get("workflow_name") or "AppReview").strip()

    payload = build_review_summary_payload(ctx)

    await emit_ui_surface(
        "present_review_summary",
        payload,
        chat_id=chat_id,
        workflow_name=workflow_name,
        display="artifact",
        agent_name="ReviewAgent",
    )

    return {"presented": True}
