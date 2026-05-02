"""Attachment tools for AgentGenerator (hybrid model).

These tools are intentionally small and workflow-scoped:
- They read/update ChatSessions.attachments metadata.
- They are gated by the workflow-level flag `attachments_allow_bundling`.

Uploads remain context-only by default. Promoting an attachment to `bundle`
requires an explicit agent call (and you can place a human confirmation step
in the agent prompt or UI if desired).
"""

import logging
from typing import Annotated, Any, Dict, List, Optional

from autogen.tools.dependency_injection import Field
from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager

_logger = logging.getLogger("tools.attachments")


async def list_attachments(
    *,
    context_variables: Annotated[
        Optional[Any],
        Field(description="AG2-injected workflow context variables."),
    ] = None,
) -> Dict[str, Any]:
    """List attachment metadata for the current chat.

    Returns attachment records as stored on the ChatSessions document.
    """

    chat_id = None
    app_id = None
    workflow_name = None
    if context_variables and hasattr(context_variables, "get"):
        chat_id = context_variables.get("chat_id")
        app_id = context_variables.get("app_id")
        workflow_name = context_variables.get("workflow_name")

    if not chat_id or not app_id:
        return {"status": "error", "message": "chat_id and app_id are required"}

    pm = AG2PersistenceManager()
    coll = await pm._coll()
    doc = await coll.find_one(
        {"_id": chat_id, "app_id": app_id},
        {"attachments": 1},
    )
    attachments = (doc or {}).get("attachments")
    if not isinstance(attachments, list):
        attachments = []

    # Only return metadata; never return file bytes.
    sanitized: List[Dict[str, Any]] = []
    for att in attachments:
        if not isinstance(att, dict):
            continue
        sanitized.append(
            {
                "attachment_id": att.get("attachment_id"),
                "filename": att.get("filename"),
                "size_bytes": att.get("size_bytes"),
                "content_type": att.get("content_type"),
                "intent": att.get("intent"),
                "bundle_path": att.get("bundle_path"),
                "uploaded_at_utc": att.get("uploaded_at_utc"),
                "user_id": att.get("user_id"),
                "stored_path": att.get("stored_path"),
            }
        )

    _logger.info("Listed %s attachments (workflow=%s chat=%s)", len(sanitized), workflow_name, chat_id)
    return {"status": "success", "attachments": sanitized, "count": len(sanitized)}


async def set_attachment_intent(
    *,
    AttachmentIntentUpdate: Annotated[
        Dict[str, Any],
        Field(description="Attachment update payload with attachment_id, intent, and optional bundle_path."),
    ],
    agent_message: Annotated[str, Field(description="Short audit message.")],
    context_variables: Annotated[
        Optional[Any],
        Field(description="AG2-injected workflow context variables."),
    ] = None,
) -> Dict[str, Any]:
    """Update an attachment's intent/bundle_path in ChatSessions.attachments.

    Hybrid gating:
    - Only allows setting intent to bundle/deliverable when
      context_variables.attachments_allow_bundling == True.
    """

    chat_id = None
    app_id = None
    workflow_name = None
    allow_bundling = False

    if context_variables and hasattr(context_variables, "get"):
        chat_id = context_variables.get("chat_id")
        app_id = context_variables.get("app_id")
        workflow_name = context_variables.get("workflow_name")
        allow_bundling = bool(context_variables.get("attachments_allow_bundling", False))

    if not chat_id or not app_id:
        return {"status": "error", "message": "chat_id and app_id are required"}

    if not isinstance(AttachmentIntentUpdate, dict):
        return {"status": "error", "message": "AttachmentIntentUpdate must be an object"}

    attachment_id = (AttachmentIntentUpdate.get("attachment_id") or "").strip()
    intent = (AttachmentIntentUpdate.get("intent") or "").strip().lower()
    bundle_path = AttachmentIntentUpdate.get("bundle_path")

    if not attachment_id:
        return {"status": "error", "message": "attachment_id is required"}

    if intent not in {"context", "bundle", "deliverable"}:
        return {"status": "error", "message": "intent must be one of: context, bundle, deliverable"}

    # Enforce hybrid policy: bundling must be enabled to promote.
    if intent in {"bundle", "deliverable"} and not allow_bundling:
        return {
            "status": "error",
            "message": "attachments_allow_bundling is false; cannot set intent to bundle/deliverable",
        }

    # Basic path sanity (relative paths only)
    if bundle_path is not None:
        if not isinstance(bundle_path, str):
            return {"status": "error", "message": "bundle_path must be a string"}
        bundle_path = bundle_path.strip()
        if bundle_path.startswith("/") or bundle_path.startswith("\\"):
            return {"status": "error", "message": "bundle_path must be a relative path"}

    pm = AG2PersistenceManager()
    coll = await pm._coll()

    update_doc: Dict[str, Any] = {
        "attachments.$.intent": intent,
    }
    if bundle_path is not None:
        update_doc["attachments.$.bundle_path"] = (bundle_path or None)

    res = await coll.update_one(
        {"_id": chat_id, "app_id": app_id, "attachments.attachment_id": attachment_id},
        {"$set": update_doc},
    )

    if res.matched_count <= 0:
        return {"status": "error", "message": "attachment not found"}

    _logger.info(
        "Updated attachment intent (workflow=%s chat=%s attachment_id=%s intent=%s)",
        workflow_name,
        chat_id,
        attachment_id,
        intent,
    )

    return {
        "status": "success",
        "message": agent_message or "Attachment updated",
        "attachment_id": attachment_id,
        "intent": intent,
        "bundle_path": bundle_path,
    }
