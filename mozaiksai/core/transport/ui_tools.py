# ==============================================================================
# FILE: mozaiksai/core/transport/ui_tools.py
# DESCRIPTION: UI Tool event handling - interactive component communication
# ==============================================================================
"""
UI Tools mixin for SimpleTransport.

This module handles interactive UI component events:
- Sending UI tool events to frontend
- Waiting for UI tool responses
- Persisting UI state for restoration
- Derived context management

Usage:
    class SimpleTransport(UIToolsMixin):
        ...
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    pass

logger = logging.getLogger("simple_transport.ui_tools")


class UIToolsMixin:
    """Mixin providing UI tool interaction functionality.

    Expects the following attributes on the class:
        - pending_tool_call_responses: Dict[str, asyncio.Future]
        - _ui_tool_metadata: Dict[str, Dict[str, Any]]
        - _derived_context_managers: Dict[str, Any]
        - send_event_to_ui(event, chat_id): method
        - _get_or_create_persistence_manager(): method
        - _resolve_chat_context(chat_id, ...): method
    """

    # ==================================================================================
    # UI TOOL STATE PERSISTENCE
    # ==================================================================================

    async def _persist_ui_tool_state(
        self,
        *,
        chat_id: Optional[str],
        tool_name: str,
        event_id: str,
        display_type: str,
        payload: Dict[str, Any],
    ) -> None:
        """Persist latest artifact/inline UI payload for chat restoration."""
        if not chat_id or not isinstance(payload, dict):
            return

        mode_candidates = [
            display_type,
            payload.get("display"),
            payload.get("mode"),
        ]
        display_mode = next(
            (m.strip() for m in mode_candidates if isinstance(m, str) and m.strip()),
            None,
        )
        normalized_mode = display_mode.lower() if display_mode else None
        persist_flag = bool(payload.get("persist_ui_state")) if isinstance(payload, dict) else False

        if not normalized_mode and not persist_flag:
            return
        if normalized_mode not in ("artifact", "inline") and not persist_flag:
            return

        if not normalized_mode:
            normalized_mode = "artifact"

        try:
            pm = self._get_or_create_persistence_manager()
        except Exception as pm_err:
            logger.debug(f"[UI_TOOL] Persistence manager unavailable: {pm_err}")
            return

        try:
            app_id, workflow_name = await self._resolve_chat_context(
                chat_id,
                pm=pm,
                payload_workflow=payload.get("workflow_name"),
            )
            if not app_id:
                logger.debug(f"[UI_TOOL] Missing app_id for chat {chat_id}; skipping last_artifact persist")
                return

            try:
                sanitized_payload = json.loads(json.dumps(payload))
            except Exception:
                sanitized_payload = payload

            artifact_doc = {
                "tool_name": tool_name,
                "tool_call_id": event_id,
                "component_type": payload.get("component_type") or tool_name,
                "display": normalized_mode,
                "workflow_name": payload.get("workflow_name") or workflow_name,
                "payload": sanitized_payload,
            }
            await pm.update_last_artifact(
                chat_id=chat_id,
                app_id=app_id,
                artifact=artifact_doc,
            )
        except Exception as persist_err:
            logger.debug(f"[UI_TOOL] Failed to persist last_artifact for chat {chat_id}: {persist_err}")

    # ==================================================================================
    # UI TOOL EVENT EMISSION
    # ==================================================================================

    async def send_tool_call_event(
        self,
        event_id: str,
        chat_id: Optional[str],
        tool_name: str,
        component_name: str,
        display_type: str,
        payload: Dict[str, Any],
        awaiting_response: bool = True,
        agent_name: Optional[str] = None
    ) -> None:
        """
        Emit a tool_call event to the frontend using the strict chat.tool_call protocol.
        """
        # Extract agent_name from payload if not explicitly provided
        if not agent_name and isinstance(payload, dict):
            agent_name = payload.get("agent_name")
        workflow_name = payload.get("workflow_name") if isinstance(payload, dict) else None
        interaction_type = payload.get("interaction_type") if isinstance(payload, dict) else None
        if not isinstance(interaction_type, str) or not interaction_type.strip():
            interaction_type = "ui_tool" if awaiting_response else "ui_surface"

        # Build a standardized AG2 tool_call payload
        event = {
            "kind": "tool_call",
            "tool_call_id": event_id,
            "tool_name": tool_name,
            "component_type": component_name,
            "awaiting_response": bool(awaiting_response),
            "payload": payload,
            "corr": event_id,
            "workflow_name": workflow_name,
            "interaction_type": interaction_type,
            "display": display_type,
            "display_type": display_type,
        }

        # Set agent field if available
        if agent_name:
            event["agent"] = agent_name

        payload_keys = list(payload.keys()) if isinstance(payload, dict) else []
        logger.info(
            f"[UI_TOOL] Emitting tool_call event: tool={tool_name}, component={component_name}, display={display_type}, event_id={event_id}, chat_id={chat_id}, payload_keys={payload_keys[:12]}"
        )

        try:
            await self._persist_ui_tool_state(
                chat_id=chat_id,
                tool_name=tool_name,
                event_id=event_id,
                display_type=display_type,
                payload=payload,
            )
        except Exception as persist_exc:
            logger.debug(f"[UI_TOOL] Persist hook raised for chat {chat_id}: {persist_exc}")

        if event_id and bool(awaiting_response):
            self._ui_tool_metadata[event_id] = {
                "chat_id": chat_id,
                "tool_name": tool_name,
                "display": display_type,
            }

        # Delegate to core event sender for namespacing and sequence handling
        await self.send_event_to_ui(event, chat_id)

    # ==================================================================================
    # UI TOOL RESPONSE HANDLING
    # ==================================================================================

    @classmethod
    async def wait_for_tool_call_response(cls, event_id: str, timeout: Optional[float] = 300.0) -> Dict[str, Any]:
        """Await a UI tool response with an optional timeout.

        Args:
            event_id: Correlation id originally sent in the tool_call event.
            timeout: Seconds to wait before raising TimeoutError (None = wait forever).
        """
        instance = await cls.get_instance()
        if not instance:
            raise RuntimeError("SimpleTransport instance not available")

        if event_id not in instance.pending_tool_call_responses:
            instance.pending_tool_call_responses[event_id] = asyncio.get_running_loop().create_future()

        fut = instance.pending_tool_call_responses[event_id]
        buffered_response = instance._buffered_tool_call_responses.pop(event_id, None)
        if buffered_response is not None and not fut.done():
            instance._complete_tool_call_future(fut, buffered_response)
        try:
            response_data = await asyncio.wait_for(fut, timeout=timeout) if timeout else await fut
            return response_data
        except asyncio.TimeoutError:
            if not fut.done():
                fut.set_exception(asyncio.TimeoutError("UI tool response timed out"))
            logger.error(f"Tool call response timed out for event {event_id}")
            raise
        finally:
            instance.pending_tool_call_responses.pop(event_id, None)

    def _resolve_tool_call_future(self, event_id: str) -> Optional[asyncio.Future]:
        future = self.pending_tool_call_responses.get(event_id)
        if future is None:
            return None
        if not isinstance(future, asyncio.Future):
            return None
        return future

    def _complete_tool_call_future(self, future: asyncio.Future, response_data: Dict[str, Any]) -> None:
        if future.done():
            return
        future_loop = future.get_loop() if hasattr(future, "get_loop") else None
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if future_loop is not None and current_loop is not None and future_loop is not current_loop:
            future_loop.call_soon_threadsafe(future.set_result, response_data)
            return

        future.set_result(response_data)

    async def _finalize_tool_call_response(
        self,
        *,
        event_id: str,
        response_data: Dict[str, Any],
        metadata: Optional[Dict[str, Any]],
    ) -> None:
        if not metadata:
            return

        display_mode = (metadata.get("display") or "").lower()
        chat_ref = metadata.get("chat_id")
        tool_name = metadata.get("tool_name")
        response_status = (
            str(response_data.get("status") or "").strip().lower()
            if isinstance(response_data, dict)
            else ""
        )

        if chat_ref and tool_name:
            manager = self._derived_context_managers.get(chat_ref)
            if manager and hasattr(manager, "apply_tool_call_response"):
                try:
                    updated = manager.apply_tool_call_response(
                        tool_name=str(tool_name),
                        response_data=response_data if isinstance(response_data, dict) else {},
                    )
                    if updated:
                        logger.info(
                            f"[UI_TOOL] Applied ui_response triggers: chat={chat_ref} tool={tool_name} vars={updated}"
                        )
                except Exception as trigger_err:
                    logger.debug(f"[UI_TOOL] ui_response trigger apply failed: {trigger_err}")

        if chat_ref and hasattr(self, "_get_or_create_persistence_manager"):
            try:
                pm = self._get_or_create_persistence_manager()
                conn = self.connections.get(chat_ref) or {}
                app_id = conn.get("app_id")
                if pm and app_id:
                    status = "responded"
                    completed = False
                    if display_mode == "artifact":
                        status = (
                            response_status
                            if response_status in {"cancelled", "skipped", "dismissed"}
                            else "dismissed"
                        )
                        completed = True
                    await pm.update_tool_call_state(
                        chat_id=chat_ref,
                        app_id=str(app_id),
                        event_id=event_id,
                        status=status,
                        completed=completed,
                    )
            except Exception as persist_err:
                logger.debug(f"[UI_TOOL] Failed to persist tool_call response state for {event_id}: {persist_err}")

        if display_mode == "artifact":
            try:
                await self.send_event_to_ui(
                    {
                        "kind": "tool_call_dismiss",
                        "tool_call_id": event_id,
                        "tool_name": metadata.get("tool_name"),
                    },
                    chat_ref,
                )
                logger.debug(f"[UI_TOOL] Emitted dismiss event for artifact {event_id}")
            except Exception as dismiss_err:
                logger.debug(f"[UI_TOOL] Failed to emit dismiss event for {event_id}: {dismiss_err}")

    async def submit_tool_call_response(self, event_id: str, response_data: Dict[str, Any]) -> bool:
        """
        Submit response data for a pending UI tool event.

        This method is called by an API endpoint when the frontend submits data
        from an interactive UI component.
        """
        future = self._resolve_tool_call_future(event_id)
        if future is not None:
            if not future.done():
                self._complete_tool_call_future(future, response_data)
                logger.info(f"[UI_TOOL] Submitted response for event {event_id}")
                metadata = self._ui_tool_metadata.pop(event_id, None)
                await self._finalize_tool_call_response(
                    event_id=event_id,
                    response_data=response_data,
                    metadata=metadata,
                )
                return True
            else:
                self._ui_tool_metadata.pop(event_id, None)
                logger.warning(f"[UI_TOOL] Event {event_id} already completed")
                return False
        metadata = self._ui_tool_metadata.get(event_id)
        if metadata is not None:
            self._buffered_tool_call_responses[event_id] = response_data
            self._ui_tool_metadata.pop(event_id, None)
            logger.info(f"[UI_TOOL] Buffered early response for event {event_id}")
            await self._finalize_tool_call_response(
                event_id=event_id,
                response_data=response_data,
                metadata=metadata,
            )
            return True
        input_request_text = self._coerce_input_request_response_text(response_data)
        if self._has_pending_input_request(event_id):
            logger.info(f"[UI_TOOL] Routing response-required interaction through submit_user_input for {event_id}")
            return await self.submit_user_input(event_id, input_request_text)

        logger.warning(f"[UI_TOOL] No pending event found for {event_id}")
        return False

    def _has_pending_input_request(self, event_id: str) -> bool:
        for registry in self._input_request_registries.values():
            if event_id in registry:
                return True
        return False

    def _coerce_input_request_response_text(self, response_data: Any) -> str:
        if isinstance(response_data, str):
            return response_data
        if isinstance(response_data, dict):
            for key in ("user_input", "user_response", "text", "value"):
                value = response_data.get(key)
                if value is not None:
                    return str(value)
            nested = response_data.get("data")
            if isinstance(nested, dict):
                for key in ("user_input", "user_response", "text", "value"):
                    value = nested.get(key)
                    if value is not None:
                        return str(value)
            status = str(response_data.get("status") or "").strip().lower()
            if status in {"cancelled", "skipped", "dismissed"}:
                return ""
            try:
                return json.dumps(response_data)
            except Exception:
                return str(response_data)
        if response_data is None:
            return ""
        try:
            return json.dumps(response_data)
        except Exception:
            return str(response_data)

    # ==================================================================================
    # DERIVED CONTEXT MANAGER REGISTRY
    # ==================================================================================

    def register_derived_context_manager(self, chat_id: str, manager: Any) -> None:
        """Register a derived context manager for a chat session."""
        if not chat_id:
            return
        self._derived_context_managers[chat_id] = manager

    def unregister_derived_context_manager(self, chat_id: str) -> None:
        """Unregister a derived context manager for a chat session."""
        if not chat_id:
            return
        self._derived_context_managers.pop(chat_id, None)
