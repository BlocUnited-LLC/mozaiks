"""Chat session resume and initialization logic.

Owns the boundary between a new workflow run and a resumed one: loads
persisted message history, filters scaffolding-only sessions, seeds the
initial message list, and creates the chat-session record on first run.
"""
from __future__ import annotations

from typing import Any


def merge_persisted_extra_context(context: Any, extra_ctx: dict[str, Any]) -> None:
    """Apply persisted run context over workflow-declared defaults.

    Workflow ``context_variables.yaml`` supplies defaults. Persisted chat-session
    extra fields are the explicit launch/resume context for this run and must
    therefore override those defaults. ``fetch_chat_session_extra_context`` strips
    canonical chat identity fields before this function is called.
    """
    if not isinstance(extra_ctx, dict) or not extra_ctx:
        return
    for k, v in extra_ctx.items():
        if not isinstance(k, str) or not k.strip():
            continue
        try:
            if hasattr(context, "set"):
                context.set(k, v)
            elif hasattr(context, "__setitem__"):
                context[k] = v
        except Exception:
            continue
    try:
        if extra_ctx.get("parent_chat_id"):
            if hasattr(context, "set"):
                context.set("automated_workflow_run", True)
    except Exception:
        pass


async def resume_or_initialize_chat(
    persistence_manager: Any,
    config: dict[str, Any],
    chat_id: str,
    app_id: str,
    workflow_name: str,
    user_id: str | None,
    initial_message: str | None,
    initial_agent_name: str | None,
    wf_logger: Any,
    suppress_config_seed: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (resumed_messages, initial_messages) for AG2 Network execution.

    - ``resumed_messages``: full persisted history (empty list on new runs)
    - ``initial_messages``: seed messages for the AG2 workflow channel
    """
    def _build_hidden_config_seed() -> dict[str, Any] | None:
        if suppress_config_seed:
            return None
        seed = config.get("initial_message")
        if not isinstance(seed, str) or not seed.strip():
            return None
        return {"role": "user", "name": "user", "content": seed.strip(), "_mozaiks_seed_kind": "initial_message"}

    resumed_messages = await persistence_manager.load_run_history(chat_id=chat_id, app_id=app_id) or []
    initial_messages: list[dict[str, Any]] = []
    hidden_config_seed = _build_hidden_config_seed()

    # Strip general-mode chatter from resumed history
    filtered: list[dict[str, Any]] = []
    for msg in resumed_messages:
        meta = msg.get("metadata") if isinstance(msg, dict) else None
        if isinstance(meta, dict) and meta.get("source") == "general_agent":
            continue
        filtered.append(msg)
    resumed_messages = filtered

    meaningful_roles = {"user", "assistant", "agent", "tool"}
    meaningful_messages = [m for m in resumed_messages if isinstance(m, dict) and m.get("role") in meaningful_roles]
    resume_valid = bool(resumed_messages) and bool(meaningful_messages)

    if resume_valid:
        wf_logger.debug(
            "[RESUME] Resuming chat %s: messages=%d meaningful=%d",
            chat_id, len(resumed_messages), len(meaningful_messages),
        )
        initial_messages = list(resumed_messages)
        if hidden_config_seed:
            initial_messages = [dict(hidden_config_seed)] + initial_messages
        if initial_message:
            initial_messages.append({"role": "user", "name": "user", "content": initial_message, "_mozaiks_seed_kind": "initial_message"})
    else:
        if resumed_messages:
            wf_logger.debug("[RESUME] Discarding scaffolding-only resume for %s; treating as NEW", chat_id)
        resumed_messages = []

        if hidden_config_seed:
            initial_messages.append(dict(hidden_config_seed))
        if initial_message:
            initial_messages.append({"role": "user", "name": "user", "content": initial_message, "_mozaiks_seed_kind": "initial_message"})

        current_user_id = user_id or "system_user"
        try:
            await persistence_manager.create_chat_session(
                chat_id=chat_id, app_id=app_id, workflow_name=workflow_name, user_id=current_user_id,
            )
        except Exception as cs_err:
            wf_logger.error("Failed to create chat session for %s: %s", chat_id, cs_err)

    # UserDriven trigger — workflow waits for user but needs a non-empty message to start
    if not initial_messages and config.get("workflow_startup_mode", "").strip().lower() == "userdriven":
        initial_messages = [{"role": "user", "name": "user", "content": ".", "_mozaiks_seed_kind": "userdriven_trigger"}]

    return resumed_messages, initial_messages


__all__ = [
    "merge_persisted_extra_context",
    "resume_or_initialize_chat",
]
