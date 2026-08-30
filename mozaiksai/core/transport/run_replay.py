# ==============================================================================
# FILE: mozaiksai/core/transport/run_replay.py
# DESCRIPTION: Replays persisted workflow messages and pending UI state to websocket clients.
# ==============================================================================



from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from logs.logging_config import get_core_logger
from mozaiksai.core.workflow.startup_messages import resolve_hidden_initial_message

SendEventFunc = Callable[[dict[str, Any], str | None], Awaitable[None]]


class WorkflowRunReplayer:
    """Replays persisted workflow events for websocket reconnects and client handshakes."""

    def __init__(self) -> None:
        self.logger = get_core_logger("run_replay")
        self._persistence_manager = None

    async def replay_on_connect_if_needed(
        self,
        *,
        chat_id: str,
        app_id: str | None,
        send_event: SendEventFunc,
        workflow_startup_mode: str | None = None,
    ) -> int | None:
        """Replay persisted messages for in-progress chats when a socket connects."""
        if not app_id:
            self.logger.debug("[RUN_REPLAY] Missing app_id for %s; skipping", chat_id)
            return None

        doc = await self._fetch_chat_doc(
            chat_id,
            app_id,
            projection={"status": 1, "workflow_name": 1, "user_id": 1},
        )
        if not doc:
            self.logger.debug("[RUN_REPLAY] No persisted chat found for %s", chat_id)
            return None

        workflow_name = doc.get("workflow_name")
        workflow_startup_mode = self._resolve_startup_mode(workflow_startup_mode, workflow_name)
        hidden_initial_message = self._resolve_hidden_initial_message(
            workflow_name=workflow_name,
            workflow_startup_mode=workflow_startup_mode,
        )
        resume_state = await self._load_resume_state(app_id=app_id, user_id=doc.get("user_id"))

        try:
            from mozaiksai.core.data.models import WorkflowStatus

            status = doc.get("status", -1)
            if int(status) != int(WorkflowStatus.IN_PROGRESS):
                self.logger.debug(
                    "[RUN_REPLAY] Chat %s not IN_PROGRESS (status=%s); skipping", chat_id, status
                )
                return None
        except Exception as exc:  # pragma: no cover - defensive guard
            self.logger.warning("[RUN_REPLAY] Status guard failed for %s: %s", chat_id, exc)
            return None

        pm = self._get_persistence_manager()
        messages: list[dict[str, Any]] = await pm.load_run_history(chat_id=chat_id, app_id=app_id)
        if not messages:
            self.logger.debug("[RUN_REPLAY] No messages to replay for %s", chat_id)
            return None

        replay_result = await self._replay_messages(
            chat_id=chat_id,
            app_id=app_id,
            messages=messages,
            send_event=send_event,
            mode="auto",
            chat_status="in_progress",
            start_index=0,
            context={"reason": "on_connect"},
            workflow_startup_mode=workflow_startup_mode,
            hidden_initial_message=hidden_initial_message,
            resume_state=resume_state,
        )
        return replay_result.get("last_index")

    async def handle_resume_request(
        self,
        *,
        chat_id: str,
        app_id: str | None,
        last_client_index: int,
        send_event: SendEventFunc,
        workflow_startup_mode: str | None = None,
    ) -> dict[str, Any]:
        """Process an explicit client.resume handshake request as a replay operation."""
        if not app_id:
            raise RuntimeError("Missing app_id for resume flow")

        doc = await self._fetch_chat_doc(
            chat_id,
            app_id,
            projection={"status": 1, "workflow_name": 1, "user_id": 1},
        )
        pm = self._get_persistence_manager()
        messages: list[dict[str, Any]] = await pm.load_run_history(chat_id=chat_id, app_id=app_id)
        status = doc.get("status", "unknown")
        workflow_name = doc.get("workflow_name")
        workflow_startup_mode = self._resolve_startup_mode(workflow_startup_mode, workflow_name)
        hidden_initial_message = self._resolve_hidden_initial_message(
            workflow_name=workflow_name,
            workflow_startup_mode=workflow_startup_mode,
        )
        resume_state = await self._load_resume_state(app_id=app_id, user_id=doc.get("user_id"))

        if last_client_index < -1:
            last_client_index = -1
        start_index = last_client_index + 1

        if start_index >= len(messages):
            summary = {
                "replayed_messages": 0,
                "last_message_index": last_client_index,
                "total_messages": len(messages),
            }
            await send_event(
                self._build_boundary_event(
                    chat_id=chat_id,
                    total_messages=len(messages),
                    replayed=0,
                    last_index=last_client_index,
                    mode="client",
                    chat_status=status,
                    start_index=start_index,
                    events_slice=[],
                    context={"reason": "client_resume", "last_client_index": last_client_index},
                    resume_state=resume_state,
                ),
                chat_id,
            )
            return summary

        replay_result = await self._replay_messages(
            chat_id=chat_id,
            app_id=app_id,
            messages=messages,
            send_event=send_event,
            mode="client",
            chat_status=status,
            start_index=start_index,
            context={"reason": "client_resume", "last_client_index": last_client_index},
            workflow_startup_mode=workflow_startup_mode,
            hidden_initial_message=hidden_initial_message,
            resume_state=resume_state,
        )
        return {
            "replayed_messages": replay_result.get("replayed_messages", 0),
            "last_message_index": replay_result.get("last_index", last_client_index),
            "total_messages": len(messages),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    async def _replay_messages(
        self,
        *,
        chat_id: str,
        app_id: str | None,
        messages: list[dict[str, Any]],
        send_event: SendEventFunc,
        mode: str,
        chat_status: str,
        start_index: int,
        context: dict[str, Any] | None,
        workflow_startup_mode: str | None = None,
        hidden_initial_message: str | None = None,
        resume_state: dict[str, Any] | None = None,
    ) -> dict[str, int]:
        slice_messages = messages[start_index:]
        if not slice_messages:
            await send_event(
                self._build_boundary_event(
                    chat_id=chat_id,
                    total_messages=len(messages),
                    replayed=0,
                    last_index=start_index - 1,
                    mode=mode,
                    chat_status=chat_status,
                    start_index=start_index,
                    events_slice=[],
                    context=context,
                    resume_state=resume_state,
                ),
                chat_id,
            )
            return {"last_index": start_index - 1, "replayed_messages": 0}

        normalized_startup_mode = str(workflow_startup_mode or "").strip().lower()
        tool_call_state_by_index = await self._load_tool_call_state_by_message_index(
            chat_id=chat_id,
            app_id=app_id,
        )
        replayed_messages: list[dict[str, Any]] = []
        last_index = start_index - 1
        for offset, message in enumerate(slice_messages):
            absolute_index = start_index + offset
            
            should_skip = False
            seed_kind = message.get("_mozaiks_seed_kind")
            metadata = message.get("metadata", {})
            metadata_seed_kind = metadata.get("_mozaiks_seed_kind") if isinstance(metadata, dict) else None
            role = str(message.get("role") or "").strip().lower()
            content = str(message.get("content") or "")
            agent_name = str(message.get("agent_name") or message.get("name") or "").strip().lower()
            message_visibility = ""
            if isinstance(metadata, dict):
                message_visibility = str(metadata.get("ui_visibility") or "").strip().lower()
            if not message_visibility:
                message_visibility = str(message.get("ui_visibility") or "").strip().lower()
            if message_visibility == "hidden":
                self.logger.debug(
                    "[RUN_REPLAY] Skipping ui-hidden message (index=%d, chat_id=%s)",
                    absolute_index,
                    chat_id,
                )
                should_skip = True

            if not should_skip and normalized_startup_mode == "agentdriven":
                is_marked_initial_message = (
                    seed_kind == "initial_message" or metadata_seed_kind == "initial_message"
                )
                is_unmarked_initial_message = (
                    absolute_index == 0
                    and role == "user"
                    and bool(hidden_initial_message)
                    and content.strip() == str(hidden_initial_message).strip()
                    and agent_name in {"", "user"}
                )
                if is_marked_initial_message or is_unmarked_initial_message:
                    self.logger.debug(
                        "[RUN_REPLAY] Skipping initial_message for AgentDriven workflow (index=%d, chat_id=%s)",
                        absolute_index, chat_id
                    )
                    should_skip = True
            elif not should_skip and normalized_startup_mode == "userdriven":
                is_marked_userdriven_trigger = (
                    seed_kind == "userdriven_trigger" or metadata_seed_kind == "userdriven_trigger"
                )
                is_dot_userdriven_trigger = absolute_index == 0 and role == "user" and content.strip() == "."
                if is_marked_userdriven_trigger or is_dot_userdriven_trigger:
                    self.logger.debug(
                        "[RUN_REPLAY] Skipping synthetic UserDriven trigger (index=%d, chat_id=%s)",
                        absolute_index, chat_id
                    )
                    should_skip = True
            
            if not should_skip:
                replay_message = self._decorate_message_with_tool_call_state(
                    message=message,
                    index=absolute_index,
                    tool_call_state_by_index=tool_call_state_by_index,
                )
                replayed_messages.append(replay_message)
                await send_event(
                    self._build_text_event(message=replay_message, index=absolute_index, chat_id=chat_id),
                    chat_id,
                )
            last_index = absolute_index

        await send_event(
            self._build_boundary_event(
                chat_id=chat_id,
                total_messages=len(messages),
                replayed=len(replayed_messages),
                last_index=last_index,
                mode=mode,
                chat_status=chat_status,
                start_index=start_index,
                events_slice=replayed_messages,
                context=context,
                resume_state=resume_state,
            ),
            chat_id,
        )

        # Re-emit pending input request if one exists
        # This ensures the UI knows input is still needed after resume
        try:
            pm = self._get_persistence_manager()
            if pm:
                pending = await pm.get_pending_input_request(
                    chat_id=chat_id,
                    app_id=app_id,
                )
                if pending:
                    self.logger.debug(
                        "[RUN_REPLAY] Re-emitting pending input request: %s (chat=%s)",
                        pending.get("request_id"),
                        chat_id,
                    )
                    await send_event(
                        self._build_input_request_event(
                            request_id=pending.get("request_id", ""),
                            agent=pending.get("agent", "Agent"),
                            prompt=pending.get("prompt", ""),
                            chat_id=chat_id,
                            component_type=pending.get("component_type"),
                            workflow_name=pending.get("workflow_name"),
                            tool_name=pending.get("tool_name"),
                            display=pending.get("display") or "inline",
                            interaction_type=pending.get("interaction_type") or "input_request",
                            password=bool(pending.get("password", False)),
                            raw_payload=pending.get("raw_payload") if isinstance(pending.get("raw_payload"), dict) else None,
                        ),
                        chat_id,
                    )
                    raw_payload = pending.get("raw_payload") if isinstance(pending.get("raw_payload"), dict) else {}
                    if raw_payload.get("resume_ui_kind") == "awaiting_reply":
                        await send_event(
                            self._build_awaiting_reply_event(
                                agent=pending.get("agent", "Agent"),
                                prompt=pending.get("prompt", ""),
                                chat_id=chat_id,
                                workflow_name=pending.get("workflow_name"),
                            ),
                            chat_id,
                        )
        except Exception as e:
            self.logger.debug("[RUN_REPLAY] Failed to check pending input request: %s", e)

        return {"last_index": last_index, "replayed_messages": len(replayed_messages)}

    def _resolve_hidden_initial_message(
        self,
        *,
        workflow_name: str | None,
        workflow_startup_mode: str | None,
    ) -> str | None:
        try:
            return resolve_hidden_initial_message(
                workflow_name,
                workflow_startup_mode=workflow_startup_mode,
            )
        except Exception as exc:
            self.logger.debug(
                "[RUN_REPLAY] Failed to resolve hidden initial_message for workflow %s: %s",
                workflow_name,
                exc,
            )
            return None

    def _resolve_startup_mode(self, workflow_startup_mode: str | None, workflow_name: str | None) -> str | None:
        normalized = str(workflow_startup_mode or "").strip().lower()
        if normalized:
            return normalized
        if not workflow_name:
            return None
        try:
            from mozaiksai.core.workflow.workflow_manager import workflow_manager

            cfg = workflow_manager.get_config(str(workflow_name)) or {}
            resolved = str(cfg.get("workflow_startup_mode") or "").strip().lower()
            return resolved or None
        except Exception:
            return None

    def _build_text_event(self, *, message: dict[str, Any], index: int, chat_id: str) -> dict[str, Any]:
        timestamp = message.get("timestamp")
        if isinstance(timestamp, datetime):
            timestamp = timestamp.isoformat()

        role = message.get("role")
        if role == "assistant":
            agent_name = message.get("agent_name") or message.get("name") or "assistant"
        else:
            agent_name = message.get("name") or "user"
        normalized = {
            "kind": "text",
            "agent": agent_name,
            "role": role or "user",
            "content": message.get("content", ""),
            "index": index,
            "chat_id": chat_id,
            "replay": True,
            "timestamp": timestamp or datetime.now(UTC).isoformat(),
        }
        metadata = message.get("metadata")
        if metadata:
            normalized["metadata"] = self._json_safe(metadata)
            
            tool_call_meta = metadata.get("tool_call")
            if tool_call_meta and isinstance(tool_call_meta, dict):
                normalized["toolCall"] = {
                    "tool_name": tool_call_meta.get("tool_name"),
                    "tool_call_id": tool_call_meta.get("tool_call_id"),
                    "payload": tool_call_meta.get("payload", {}),
                    "display": tool_call_meta.get("display", "inline"),
                    "workflow_name": message.get("workflow_name") or tool_call_meta.get("workflow_name"),
                    "component_type": tool_call_meta.get("component_type") or tool_call_meta.get("tool_name"),
                }
                normalized["tool_call_completed"] = tool_call_meta.get("tool_call_completed", False)
                normalized["tool_call_status"] = tool_call_meta.get("tool_call_status", "pending")
                
                self.logger.debug(
                    "[RESUME] Restored tool call state: tool=%s event=%s completed=%s display=%s",
                    tool_call_meta.get("tool_name"),
                    tool_call_meta.get("tool_call_id"),
                    tool_call_meta.get("tool_call_completed", False),
                    tool_call_meta.get("display", "inline")
                )
        seed_kind = message.get("_mozaiks_seed_kind")
        if isinstance(seed_kind, str) and seed_kind.strip():
            normalized["_mozaiks_seed_kind"] = seed_kind.strip()
            metadata_payload = normalized.get("metadata")
            if not isinstance(metadata_payload, dict):
                metadata_payload = {}
            metadata_payload.setdefault("_mozaiks_seed_kind", seed_kind.strip())
            normalized["metadata"] = metadata_payload
        return normalized

    def _build_boundary_event(
        self,
        *,
        chat_id: str,
        total_messages: int,
        replayed: int,
        last_index: int,
        mode: str,
        chat_status: str,
        start_index: int,
        events_slice: list[dict[str, Any]],
        context: dict[str, Any] | None,
        resume_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ag2_resume = {
            "mode": mode,
            "start_index": start_index,
            "last_index": last_index,
            "events": [self._sanitize_message(msg) for msg in events_slice],
        }
        if events_slice:
            ag2_resume["last_speaker_name"] = events_slice[-1].get("name") or events_slice[-1].get("agent_name")

        boundary = {
            "kind": "resume_boundary",
            "chat_id": chat_id,
            "message_count": total_messages,
            "chat_status": chat_status,
            "replayed_messages": replayed,
            "last_message_index": last_index,
            "resume_mode": mode,
            "timestamp": datetime.now(UTC).isoformat(),
            "ag2_resume": ag2_resume,
        }
        if context:
            boundary["resume_context"] = context
        if resume_state is not None:
            boundary["resume_state"] = resume_state
        return boundary

    async def _load_tool_call_state_by_message_index(
        self,
        *,
        chat_id: str,
        app_id: str | None,
    ) -> dict[int, dict[str, Any]]:
        if not app_id:
            return {}
        try:
            pm = self._get_persistence_manager()
            loader = getattr(pm, "get_workflow_tool_call_states", None)
            if loader is None:
                return {}
            states = await loader(chat_id=chat_id, app_id=app_id)
            indexed: dict[int, dict[str, Any]] = {}
            for state in states or []:
                if not isinstance(state, dict):
                    continue
                message_index = state.get("message_index")
                if not isinstance(message_index, int) or message_index < 0:
                    continue
                normalized_state = self._normalize_tool_call_state(state)
                existing = indexed.get(message_index)
                if existing is None:
                    indexed[message_index] = normalized_state
                    continue
                existing_stamp = str(existing.get("completed_at") or existing.get("responded_at") or existing.get("timestamp") or "")
                normalized_stamp = str(
                    normalized_state.get("completed_at")
                    or normalized_state.get("responded_at")
                    or normalized_state.get("timestamp")
                    or ""
                )
                if normalized_stamp >= existing_stamp:
                    indexed[message_index] = normalized_state
            return indexed
        except Exception as exc:
            self.logger.debug("[RUN_REPLAY] Failed to load workflow UI state for %s: %s", chat_id, exc)
            return {}

    def _normalize_tool_call_state(self, state: dict[str, Any]) -> dict[str, Any]:
        normalized = self._json_safe(dict(state))
        normalized.pop("message_index", None)
        normalized.pop("updated_at", None)
        return normalized  # type: ignore[no-any-return]

    def _decorate_message_with_tool_call_state(
        self,
        *,
        message: dict[str, Any],
        index: int,
        tool_call_state_by_index: dict[int, dict[str, Any]],
    ) -> dict[str, Any]:
        tool_call_state = tool_call_state_by_index.get(index)
        if not tool_call_state:
            return message

        decorated = dict(message)
        metadata = decorated.get("metadata")
        next_metadata = dict(metadata) if isinstance(metadata, dict) else {}
        next_metadata["tool_call"] = dict(tool_call_state)
        decorated["metadata"] = next_metadata
        return decorated

    def _sanitize_message(self, message: dict[str, Any]) -> dict[str, Any]:
        return {
            "role": message.get("role"),
            "name": message.get("name"),
            "content": message.get("content"),
            "metadata": self._json_safe(message.get("metadata")),
        }

    def _json_safe(self, value: Any) -> Any:
        """Best-effort conversion of persistence payloads to JSON-safe values."""
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, dict):
            out: dict[str, Any] = {}
            for k, v in value.items():
                if isinstance(k, str):
                    key = k
                elif isinstance(k, datetime):
                    key = k.isoformat()
                else:
                    key = str(k)
                out[key] = self._json_safe(v)
            return out
        if isinstance(value, (list, tuple, set)):
            return [self._json_safe(v) for v in value]
        return str(value)

    async def _fetch_chat_doc(
        self,
        chat_id: str,
        app_id: str,
        projection: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        try:
            pm = await self._ensure_persistence_manager()
            coll = await pm._coll()
            return await coll.find_one({"_id": chat_id, "app_id": app_id}, projection) or {}
        except Exception as exc:
            self.logger.warning("Failed to fetch chat doc for %s: %s", chat_id, exc)
            return {}

    async def _ensure_persistence_manager(self):
        if self._persistence_manager is None:
            from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager

            self._persistence_manager = AG2PersistenceManager()
        return self._persistence_manager

    def _get_persistence_manager(self):
        """Get the cached persistence manager if available (sync access)."""
        if self._persistence_manager is None:
            from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
            self._persistence_manager = AG2PersistenceManager()
        return self._persistence_manager

    async def _load_resume_state(self, *, app_id: str | None, user_id: Any) -> dict[str, Any] | None:
        resolved_app_id = str(app_id or "").strip()
        resolved_user_id = str(user_id or "").strip()
        if not resolved_app_id or not resolved_user_id:
            return None
        try:
            from mozaiksai.core.session import get_session_router

            return await get_session_router().get_session_snapshot(  # type: ignore[no-any-return]
                app_id=resolved_app_id,
                user_id=resolved_user_id,
            )
        except Exception as exc:
            self.logger.debug(
                "[RUN_REPLAY] Failed to load session snapshot for %s/%s: %s",
                resolved_app_id,
                resolved_user_id,
                exc,
            )
            return None

    def _build_input_request_event(
        self,
        *,
        request_id: str,
        agent: str,
        prompt: str,
        chat_id: str,
        component_type: str | None = None,
        workflow_name: str | None = None,
        tool_name: str | None = None,
        display: str = "composer",
        interaction_type: str = "input_request",
        password: bool = False,
        raw_payload: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
        """Build a response-required tool_call payload for UI resume."""
        resolved_component = component_type or "UserInputRequest"
        resolved_tool_name = tool_name or component_type or resolved_component
        resolved_display = display or "composer"
        payload = {
            **(raw_payload if isinstance(raw_payload, dict) else {}),
            "input_request_id": request_id,
            "request_id": request_id,
            "prompt": prompt,
            "password": bool(password),
            "workflow_name": workflow_name,
            "component_type": resolved_component,
            "display": resolved_display,
            "mode": resolved_display,
            "interaction_type": interaction_type or "input_request",
        }
        return {
            "kind": "tool_call",
            "tool_call_id": request_id,
            "corr": request_id,
            "tool_name": resolved_tool_name,
            "component_type": resolved_component,
            "workflow_name": workflow_name,
            "interaction_type": interaction_type or "input_request",
            "awaiting_response": True,
            "display": resolved_display,
            "display_type": resolved_display,
            "agent": agent,
            "chat_id": chat_id,
            "payload": payload,
            "replay": True,
            "timestamp": datetime.now(UTC).isoformat(),
            "metadata": {
                "source": "resume_pending_input",
            },
        }

    def _build_awaiting_reply_event(
        self,
        *,
        agent: str,
        prompt: str,
        chat_id: str,
        workflow_name: str | None,
    ) -> dict[str, Any]:
        return {
            "kind": "awaiting_reply",
            "agent": agent or "Agent",
            "chat_id": chat_id,
            "workflow_name": workflow_name,
            "display": "composer",
            "interaction_type": "input_request",
            "reason": "awaiting_user_reply",
            "prompt": prompt or "",
            "source_agent": agent or "Agent",
            "replay": True,
            "timestamp": datetime.now(UTC).isoformat(),
            "metadata": {
                "source": "resume_pending_input",
            },
        }


__all__ = ["WorkflowRunReplayer"]

