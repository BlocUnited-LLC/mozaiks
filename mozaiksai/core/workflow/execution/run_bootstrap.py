"""Prepare launch input without reconstructing AG2 Network execution state.

The run stream is a UI/transport transcript. Durable Network state lives in
AG2's Hub KnowledgeStore and is hydrated by the Network runner.
"""
from __future__ import annotations

from typing import Any


def merge_persisted_extra_context(context: Any, extra_ctx: dict[str, Any]) -> None:
    """Hydrate validated persisted state over workflow-declared defaults."""
    if not isinstance(extra_ctx, dict) or not extra_ctx:
        return
    from ..context.adapter import _hydrate_persisted_context

    _hydrate_persisted_context(context, extra_ctx)


def _hidden_config_seed(config: dict[str, Any], *, suppress: bool) -> str | None:
    if suppress:
        return None
    seed = config.get("initial_message")
    if not isinstance(seed, str) or not seed.strip():
        return None
    return seed.strip()


def _latest_user_event(events: list[Any]) -> str | None:
    from ag2.events.input_events import TextInput

    for event in reversed(events):
        if not isinstance(event, TextInput):
            continue
        content = str(getattr(event, "content", "") or "").strip()
        if not content:
            continue
        return content
    return None


async def prepare_network_trigger(
    persistence_manager: Any,
    config: dict[str, Any],
    chat_id: str,
    app_id: str,
    workflow_name: str,
    user_id: str | None,
    initial_message: str | None,
    wf_logger: Any,
    suppress_config_seed: bool = False,
    resume_existing_only: bool = False,
) -> str:
    """Return the text that starts or continues the AG2 Network channel.

    Existing UI run events contribute only the newly queued user trigger. They
    are never replayed as Network history; Hub hydration owns that state.
    """
    run_events = await persistence_manager.load_run_events(chat_id=chat_id, app_id=app_id) or []
    has_persisted_events = bool(run_events)
    trigger_parts: list[str] = []

    if has_persisted_events:
        latest_user = None if resume_existing_only else _latest_user_event(run_events)
        trigger_parts = [latest_user] if latest_user else []
        wf_logger.debug(
            "[RUN_BOOTSTRAP] Existing chat %s uses AG2 Hub hydration: events=%d trigger_messages=%d",
            chat_id,
            len(run_events),
            len(trigger_parts),
        )

    if not has_persisted_events:
        hidden_seed = _hidden_config_seed(config, suppress=suppress_config_seed)
        if hidden_seed:
            trigger_parts.append(hidden_seed)
        if initial_message:
            trigger_parts.append(initial_message)

        current_user_id = user_id or "system_user"
        await persistence_manager.create_chat_session(
            chat_id=chat_id,
            app_id=app_id,
            workflow_name=workflow_name,
            user_id=current_user_id,
        )

    if not trigger_parts and config.get("workflow_startup_mode", "").strip().lower() == "userdriven":
        trigger_parts = ["."]

    return "\n\n".join(part.strip() for part in trigger_parts if part.strip()) or "."


__all__ = [
    "prepare_network_trigger",
    "merge_persisted_extra_context",
]
