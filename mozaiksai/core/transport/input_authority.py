"""Whether the host still lets a person continue the chat they are sending input to.

Starting a run and resuming one ask the host. Input to a run that is still live,
or a reply it is waiting for, reaches no such check. A host that limits who may
run a workflow declares ``chat_input_authority``, and transport asks it here
before anything changes.
"""

from __future__ import annotations

import logging
from typing import Any

from mozaiksai.core.multitenant import build_app_scope_filter

logger = logging.getLogger("simple_transport.input_authority")

WORKFLOW_ACCESS_DENIED = "WORKFLOW_ACCESS_DENIED"
DEFAULT_REASON = "You can no longer continue this workflow."


async def input_refusal(
    persistence_manager: Any, *, chat_id: str, app_id: str, user_id: str | None, require_session: bool = True,
) -> str | None:
    """Why this input may not continue the chat, or None. Fails closed.

    The stored workflow decides, not one a request names. Without a stored session
    there is nothing to continue: ``require_session=False`` leaves a first start to
    the checks a start makes.
    """
    from mozaiksai.core.runtime.composition.platform_hooks import get_platform_hooks

    hooks = get_platform_hooks()
    if not hooks.has_chat_input_authority:
        return None
    try:
        collection = await persistence_manager._coll()
        session = await collection.find_one(
            {"_id": chat_id, **build_app_scope_filter(app_id)}, {"workflow_name": 1},
        )
        if not isinstance(session, dict):
            return DEFAULT_REASON if require_session else None
        allowed, reason = await hooks.call_chat_input_authority(
            app_id=app_id, user_id=user_id or "anonymous",
            workflow_name=str(session.get("workflow_name") or ""), chat_id=chat_id,
        )
    except Exception as exc:
        logger.warning("WORKFLOW_INPUT_AUTHORITY_UNAVAILABLE chat=%s error=%s", chat_id, type(exc).__name__)
        return DEFAULT_REASON
    return None if allowed else reason or DEFAULT_REASON


__all__ = ["WORKFLOW_ACCESS_DENIED", "input_refusal"]
