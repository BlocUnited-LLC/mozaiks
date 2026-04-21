# ==============================================================================
# FILE: mozaiksai/core/transport/resume_groupchat.py
# DESCRIPTION: Rebuilds AG2-compatible message history and state for paused workflow resume flows.
# ==============================================================================



from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from logs.logging_config import get_core_logger

SendEventFunc = Callable[[Dict[str, Any], Optional[str]], Awaitable[None]]


class GroupChatResumer:
    """Encapsulates AG2-aligned resume flows for websocket transports."""

    def __init__(self) -> None:
        self.logger = get_core_logger("resume_groupchat")
        self._persistence_manager = None

    async def auto_resume_if_needed(
        self,
        *,
        chat_id: str,
        app_id: Optional[str],
        send_event: SendEventFunc,
        workflow_startup_mode: Optional[str] = None,
    ) -> Optional[int]:
        """Replay persisted messages for in-progress chats when a socket connects."""
        if not app_id:
            self.logger.debug("[AUTO_RESUME] Missing app_id for %s; skipping", chat_id)
            return None

        doc = await self._fetch_chat_doc(
            chat_id,
            app_id,
            projection={"status": 1, "messages": 1, "workflow_name": 1, "user_id": 1},
        )
        if not doc:
            self.logger.debug("[AUTO_RESUME] No persisted chat found for %s", chat_id)
            return None

        workflow_startup_mode = self._resolve_startup_mode(workflow_startup_mode, doc.get("workflow_name"))
        resume_state = await self._load_resume_state(app_id=app_id, user_id=doc.get("user_id"))

        try:
            from mozaiksai.core.data.models import WorkflowStatus

            status = doc.get("status", -1)
            if int(status) != int(WorkflowStatus.IN_PROGRESS):
                self.logger.debug(
                    "[AUTO_RESUME] Chat %s not IN_PROGRESS (status=%s); skipping", chat_id, status
                )
                return None
        except Exception as exc:  # pragma: no cover - defensive guard
            self.logger.warning("[AUTO_RESUME] Status guard failed for %s: %s", chat_id, exc)
            return None

        messages: List[Dict[str, Any]] = doc.get("messages", []) or []
        if not messages:
            self.logger.debug("[AUTO_RESUME] No messages to replay for %s", chat_id)
            return None

        replay_result = await self._replay_messages(
            chat_id=chat_id,
            messages=messages,
            send_event=send_event,
            mode="auto",
            chat_status="in_progress",
            start_index=0,
            context={"reason": "on_connect"},
            workflow_startup_mode=workflow_startup_mode,
            resume_state=resume_state,
        )
        return replay_result.get("last_index")

    async def handle_resume_request(
        self,
        *,
        chat_id: str,
        app_id: Optional[str],
        last_client_index: int,
        send_event: SendEventFunc,
        workflow_startup_mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Process an explicit client.resume handshake request."""
        if not app_id:
            raise RuntimeError("Missing app_id for resume flow")

        doc = await self._fetch_chat_doc(
            chat_id,
            app_id,
            projection={"status": 1, "messages": 1, "workflow_name": 1, "user_id": 1},
        )
        messages: List[Dict[str, Any]] = doc.get("messages", []) or []
        status = doc.get("status", "unknown")
        workflow_startup_mode = self._resolve_startup_mode(workflow_startup_mode, doc.get("workflow_name"))
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
            messages=messages,
            send_event=send_event,
            mode="client",
            chat_status=status,
            start_index=start_index,
            context={"reason": "client_resume", "last_client_index": last_client_index},
            workflow_startup_mode=workflow_startup_mode,
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
        messages: List[Dict[str, Any]],
        send_event: SendEventFunc,
        mode: str,
        chat_status: str,
        start_index: int,
        context: Optional[Dict[str, Any]],
        workflow_startup_mode: Optional[str] = None,
        resume_state: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, int]:
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
        replayed_messages: List[Dict[str, Any]] = []
        last_index = start_index - 1
        for offset, message in enumerate(slice_messages):
            absolute_index = start_index + offset
            
            should_skip = False
            seed_kind = message.get("_mozaiks_seed_kind")
            metadata = message.get("metadata", {})
            metadata_seed_kind = metadata.get("_mozaiks_seed_kind") if isinstance(metadata, dict) else None
            role = str(message.get("role") or "").strip().lower()
            content = str(message.get("content") or "")

            if normalized_startup_mode == "agentdriven":
                if seed_kind == "initial_message" or metadata_seed_kind == "initial_message":
                    self.logger.debug(
                        "[AUTO_RESUME] Skipping initial_message for AgentDriven workflow (index=%d, chat_id=%s)",
                        absolute_index, chat_id
                    )
                    should_skip = True
            elif normalized_startup_mode == "userdriven":
                is_marked_userdriven_trigger = (
                    seed_kind == "userdriven_trigger" or metadata_seed_kind == "userdriven_trigger"
                )
                is_dot_userdriven_trigger = absolute_index == 0 and role == "user" and content.strip() == "."
                if is_marked_userdriven_trigger or is_dot_userdriven_trigger:
                    self.logger.debug(
                        "[AUTO_RESUME] Skipping synthetic UserDriven trigger (index=%d, chat_id=%s)",
                        absolute_index, chat_id
                    )
                    should_skip = True
            
            if not should_skip:
                replayed_messages.append(message)
                await send_event(
                    self._build_text_event(message=message, index=absolute_index, chat_id=chat_id),
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
                pending = await pm.get_pending_input_request(chat_id=chat_id)
                if pending:
                    self.logger.info(
                        "[AUTO_RESUME] Re-emitting pending input request: %s (chat=%s)",
                        pending.get("request_id"),
                        chat_id,
                    )
                    await send_event(
                        self._build_input_request_event(
                            request_id=pending.get("request_id", ""),
                            agent=pending.get("agent", "Agent"),
                            prompt=pending.get("prompt", ""),
                            chat_id=chat_id,
                        ),
                        chat_id,
                    )
        except Exception as e:
            self.logger.debug("[AUTO_RESUME] Failed to check pending input request: %s", e)

        return {"last_index": last_index, "replayed_messages": len(replayed_messages)}

    def _resolve_startup_mode(self, workflow_startup_mode: Optional[str], workflow_name: Optional[str]) -> Optional[str]:
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

    def _build_text_event(self, *, message: Dict[str, Any], index: int, chat_id: str) -> Dict[str, Any]:
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
            "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        }
        metadata = message.get("metadata")
        if metadata:
            normalized["metadata"] = self._json_safe(metadata)
            
            # Restore UI tool state if message has ui_tool metadata
            ui_tool_meta = metadata.get("ui_tool")
            if ui_tool_meta and isinstance(ui_tool_meta, dict):
                # Reconstruct uiToolEvent object for frontend UIToolEventRenderer
                normalized["uiToolEvent"] = {
                    "ui_tool_id": ui_tool_meta.get("ui_tool_id"),
                    "eventId": ui_tool_meta.get("event_id"),
                    "payload": ui_tool_meta.get("payload", {}),
                    "display": ui_tool_meta.get("display", "inline"),
                    "workflow_name": message.get("workflow_name"),  # May be in top-level or metadata
                }
                # CRITICAL: Surface completion state to frontend
                # Frontend UIToolEventRenderer checks these flags to show "Completed" chip vs interactive component
                normalized["ui_tool_completed"] = ui_tool_meta.get("ui_tool_completed", False)
                normalized["ui_tool_status"] = ui_tool_meta.get("ui_tool_status", "pending")
                
                self.logger.debug(
                    "[RESUME] Restored UI tool state: tool=%s event=%s completed=%s display=%s",
                    ui_tool_meta.get("ui_tool_id"),
                    ui_tool_meta.get("event_id"),
                    ui_tool_meta.get("ui_tool_completed", False),
                    ui_tool_meta.get("display", "inline")
                )
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
        events_slice: List[Dict[str, Any]],
        context: Optional[Dict[str, Any]],
        resume_state: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
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
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "ag2_resume": ag2_resume,
        }
        if context:
            boundary["resume_context"] = context
        if resume_state is not None:
            boundary["resume_state"] = resume_state
        return boundary

    def _sanitize_message(self, message: Dict[str, Any]) -> Dict[str, Any]:
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
            out: Dict[str, Any] = {}
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
        projection: Optional[Dict[str, int]] = None,
    ) -> Dict[str, Any]:
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

    async def _load_resume_state(self, *, app_id: Optional[str], user_id: Any) -> Optional[Dict[str, Any]]:
        resolved_app_id = str(app_id or "").strip()
        resolved_user_id = str(user_id or "").strip()
        if not resolved_app_id or not resolved_user_id:
            return None
        try:
            from mozaiksai.core.session import get_session_router

            return await get_session_router().get_session_snapshot(
                app_id=resolved_app_id,
                user_id=resolved_user_id,
            )
        except Exception as exc:
            self.logger.debug(
                "[AUTO_RESUME] Failed to load session snapshot for %s/%s: %s",
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
    ) -> Dict[str, Any]:
        """Build an input_request event payload for UI."""
        return {
            "kind": "input_request",
            "request_id": request_id,
            "agent": agent,
            "prompt": prompt,
            "chat_id": chat_id,
            "replay": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metadata": {
                "source": "resume_pending_input",
            },
        }


__all__ = ["GroupChatResumer"]

