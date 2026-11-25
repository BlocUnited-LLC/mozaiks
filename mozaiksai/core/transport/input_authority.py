"""Whether the host still lets a person run the chat they are sending input to.

Orchestration asks the host when it starts or resumes a run. Input to a run that
is still live, or a reply it is waiting for, reaches no such check, and a refusal
during recovery would already have settled the chat as failed. Transport asks
here first, before anything changes.
"""

from __future__ import annotations

import logging
from typing import Any

from mozaiksai.core.multitenant import build_app_scope_filter

logger = logging.getLogger("simple_transport.input_authority")

WORKFLOW_ACCESS_DENIED = "WORKFLOW_ACCESS_DENIED"


async def input_refused(
    persistence_manager: Any, *, chat_id: str, app_id: str, user_id: str | None, require_session: bool = True,
) -> bool:
    """Ask the host about the stored workflow, not one a request names. Fails closed.

    Without a stored session there is nothing to continue: ``require_session=False``
    leaves a first start to orchestration, which asks the host itself.
    """
    from mozaiksai.core.data.persistence.persistence_manager import SERVER_OWNED_SESSION_FIELDS
    from mozaiksai.core.runtime.composition.platform_hooks import get_platform_hooks

    hooks = get_platform_hooks()
    if not hooks.has_session_fields:
        return False
    try:
        collection = await persistence_manager._coll()
        session = await collection.find_one(
            {"_id": chat_id, **build_app_scope_filter(app_id)},
            {"workflow_name": 1, **{key: 1 for key in SERVER_OWNED_SESSION_FIELDS}},
        )
        if not isinstance(session, dict):
            return require_session
        await hooks.call_chat_session_fields(
            app_id=app_id, user_id=user_id or "anonymous", workflow_name=str(session.get("workflow_name") or ""),
            chat_id=chat_id, phase="resume",
            session_fields={key: session[key] for key in SERVER_OWNED_SESSION_FIELDS if key in session},
        )
    except Exception as exc:
        logger.warning("WORKFLOW_INPUT_REFUSED chat=%s error=%s", chat_id, type(exc).__name__)
        return True
    return False


__all__ = ["WORKFLOW_ACCESS_DENIED", "input_refused"]
