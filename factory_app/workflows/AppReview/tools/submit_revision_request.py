from __future__ import annotations

from typing import Any

from factory_app.workflows.AppReview.tools.review_context import (
    build_refinement_request_payload,
    build_revision_event_payload,
    mark_review_promoted,
    mark_review_revision_submitted,
)


async def submit_revision_request(
    revision_request: str | None = None,
    action: str = "revise",
    context_variables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Called by ReviewAgent after the user types a revision request or
    after a successful promotion signal.

    Sets review_complete=True in context_variables so ReviewAgent can
    terminate. For revision requests, stores the user's text in
    refinement_request and emits a chat.revision_requested WebSocket event
    so the frontend can initiate a refinement trigger through the control plane.

    Args:
        revision_request: The user's typed revision request text.
            Required when action="revise". Ignored when action="promote".
        action: "revise" (user typed a change) or "promote" (build was
            promoted successfully, review is complete).
        context_variables: Live AG2 context_variables dict.
    """
    ctx = context_variables if context_variables is not None else {}
    normalized_action = str(action or "").strip().lower() or "revise"
    if normalized_action not in {"revise", "promote"}:
        raise ValueError("action must be 'revise' or 'promote'")
    is_promote = normalized_action == "promote"
    request_text = str(revision_request or "").strip()

    if is_promote:
        mark_review_promoted(ctx)
        return {
            "success": True,
            "action": "promote",
            "revision_request": None,
        }

    if not request_text:
        raise ValueError("revision_request is required when action='revise'")

    refinement_payload = build_refinement_request_payload(ctx, request_text)
    mark_review_revision_submitted(ctx, refinement_payload)

    # Emit a WebSocket event so the frontend can trigger the refinement
    # control plane. Context variables remain the canonical state record.
    chat_id = None
    if hasattr(ctx, "get"):
        try:
            chat_id = str(ctx.get("chat_id") or "").strip() or None
        except Exception:
            chat_id = None
    event_payload = build_revision_event_payload(ctx, request_text)
    try:
        from mozaiksai.core.transport.simple_transport import SimpleTransport

        transport = await SimpleTransport.get_instance()
        await transport.send_event_to_ui(event_payload, chat_id=chat_id)
        event_emitted = True
    except Exception:
        event_emitted = False

    return {
        "success": True,
        "action": "revise",
        "revision_request": request_text,
        "refinement_request": refinement_payload,
        "event_emitted": event_emitted,
    }
