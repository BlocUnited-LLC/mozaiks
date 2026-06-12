from __future__ import annotations

from typing import Any

from mozaiksai.core.workflow.ui_tools import emit_ui_surface


async def present_review_summary(context_variables: dict[str, Any] | None = None) -> dict[str, Any]:
    """Called by ReviewAgent on its first turn.

    Reads build validation context from context_variables and emits an
    AppReviewSummary UI surface artifact in the chat.
    """
    ctx = context_variables or {}

    chat_id = str(ctx.get("chat_id") or "").strip() or None
    workflow_name = str(ctx.get("workflow_name") or "AppReview").strip()

    payload: dict[str, Any] = {
        "build_registry_id": ctx.get("build_registry_id"),
        "bundle_path": ctx.get("bundle_path"),
        "lifecycle_state": ctx.get("lifecycle_state", "review"),
        "app_validation_status": ctx.get("app_validation_status"),
        "app_validation_strategy_used": ctx.get("app_validation_strategy_used"),
        "app_validation_preview_url": ctx.get("app_validation_preview_url"),
        "integration_tests_passed": ctx.get("integration_tests_passed"),
    }

    await emit_ui_surface(
        "present_review_summary",
        payload,
        chat_id=chat_id,
        workflow_name=workflow_name,
        display="artifact",
        agent_name="ReviewAgent",
    )

    return {"presented": True}
