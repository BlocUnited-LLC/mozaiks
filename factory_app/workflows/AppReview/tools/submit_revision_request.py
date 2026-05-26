from __future__ import annotations

from typing import Any


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
    ctx = context_variables if isinstance(context_variables, dict) else {}

    is_promote = str(action or "").strip().lower() == "promote"
    request_text = str(revision_request or "").strip()

    if not is_promote:
        ctx["revision_submitted"] = True
        if request_text:
            ctx["refinement_request"] = request_text

    ctx["review_complete"] = True

    # Emit a WebSocket event so the frontend can trigger the refinement
    # control plane. This is a best-effort fire-and-forget — the context
    # variables above are the canonical state record.
    if not is_promote and request_text:
        chat_id = str(ctx.get("chat_id") or "").strip() or None
        try:
            from mozaiksai.core.transport.simple_transport import SimpleTransport

            transport = await SimpleTransport.get_instance()
            await transport.send_event_to_ui(
                {
                    "kind": "chat.revision_requested",
                    "refinement_request": request_text,
                    "artifact_kind": "app_bundle",
                    "source_workflow": "AppReview",
                },
                chat_id=chat_id,
            )
        except Exception:
            # Transport may be unavailable in test environments.
            pass

    return {
        "success": True,
        "action": "promote" if is_promote else "revise",
        "revision_request": request_text if not is_promote else None,
    }
