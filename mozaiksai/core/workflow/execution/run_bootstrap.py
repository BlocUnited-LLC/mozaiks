"""Workflow run bootstrap from canonical AG2 event history.

This module owns only the deterministic boundary between a new workflow run
and re-entering an existing run. AG2 typed events are the execution source of
truth. Message dictionaries produced here are a temporary prompt projection for
the current AG2 Network runner, which does not expose the beta ``Agent.resume``
API in the installed AG2 version.
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


def _latest_user_message(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        if message.get("role") != "user":
            continue
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        return {
            "role": "user",
            "name": str(message.get("name") or "user"),
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
) -> tuple[bool, list[dict[str, Any]]]:
    """Return ``(has_persisted_events, seed_messages)`` for AG2 Network.

    Existing runs are bootstrapped from AG2 typed event history. New runs are
    seeded only from launch-time input/config and get a ``ChatSessions`` record.
    """
    _ = initial_agent_name
    run_events = await persistence_manager.load_run_events(chat_id=chat_id, app_id=app_id) or []
    has_persisted_events = bool(run_events)
    seed_messages: list[dict[str, Any]] = []

    if has_persisted_events:
        event_projector = getattr(persistence_manager, "project_run_events_to_messages", None)
        if not callable(event_projector):
            raise RuntimeError("persistence_manager must provide project_run_events_to_messages")
        projected_messages = event_projector(run_events)
        meaningful_roles = {"user", "assistant", "agent", "tool"}
        meaningful_count = sum(
            1 for message in projected_messages
            if isinstance(message, dict) and message.get("role") in meaningful_roles
        )
        if meaningful_count:
            latest_user = _latest_user_message(projected_messages)
            seed_messages = [latest_user] if latest_user else []
            wf_logger.debug(
                "[RUN_BOOTSTRAP] Re-entering chat %s from AG2 events: events=%d projected_messages=%d trigger_messages=%d",
                chat_id, len(run_events), len(projected_messages), len(seed_messages),
            )
        else:
            wf_logger.debug(
                "[RUN_BOOTSTRAP] Existing AG2 events for %s have no prompt projection; continuing as fresh",
                chat_id,
            )
            has_persisted_events = False
            seed_messages = []

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
