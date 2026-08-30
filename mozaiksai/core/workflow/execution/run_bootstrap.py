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


def _hidden_config_seed(config: dict[str, Any], *, suppress: bool) -> dict[str, Any] | None:
    if suppress:
        return None
    seed = config.get("initial_message")
    if not isinstance(seed, str) or not seed.strip():
        return None
    return {
        "role": "user",
        "name": "user",
        "content": seed.strip(),
        "_mozaiks_seed_kind": "initial_message",
    }


def _latest_user_event(events: list[Any]) -> dict[str, Any] | None:
    from ag2.events.input_events import TextInput

    for event in reversed(events):
        if not isinstance(event, TextInput):
            continue
        content = str(getattr(event, "content", "") or "").strip()
        if not content:
            continue
        return {
            "role": "user",
            "name": "user",
            "content": content,
            "_mozaiks_seed_kind": "ag2_event_trigger",
        }
    return None


async def bootstrap_run_messages(
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
    resume_existing_only: bool = False,
) -> tuple[bool, list[dict[str, Any]]]:
    """Return ``(has_run_events, launch_messages)`` for AG2 Network.

    Existing run events contribute only the newly queued user trigger. They are
    never replayed as Network history; Hub hydration owns that state.
    """
    _ = initial_agent_name
    run_events = await persistence_manager.load_run_events(chat_id=chat_id, app_id=app_id) or []
    has_persisted_events = bool(run_events)
    seed_messages: list[dict[str, Any]] = []

    if has_persisted_events:
        latest_user = None if resume_existing_only else _latest_user_event(run_events)
        seed_messages = [latest_user] if latest_user else []
        wf_logger.debug(
            "[RUN_BOOTSTRAP] Existing chat %s uses AG2 Hub hydration: events=%d trigger_messages=%d",
            chat_id,
            len(run_events),
            len(seed_messages),
        )

    if not has_persisted_events:
        hidden_seed = _hidden_config_seed(config, suppress=suppress_config_seed)
        if hidden_seed:
            seed_messages.append(hidden_seed)
        if initial_message:
            seed_messages.append({
                "role": "user",
                "name": "user",
                "content": initial_message,
                "_mozaiks_seed_kind": "initial_message",
            })

        current_user_id = user_id or "system_user"
        await persistence_manager.create_chat_session(
            chat_id=chat_id,
            app_id=app_id,
            workflow_name=workflow_name,
            user_id=current_user_id,
        )

    if not seed_messages and config.get("workflow_startup_mode", "").strip().lower() == "userdriven":
        seed_messages = [{
            "role": "user",
            "name": "user",
            "content": ".",
            "_mozaiks_seed_kind": "userdriven_trigger",
        }]

    return has_persisted_events, seed_messages


__all__ = [
    "bootstrap_run_messages",
    "merge_persisted_extra_context",
]
