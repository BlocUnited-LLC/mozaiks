# ==============================================================================
# FILE: mozaiksai/transport/websocket/input_handler.py
# DESCRIPTION: User input collection, UI tool response correlation
# ==============================================================================
import asyncio
import uuid
from typing import Any, Dict, Optional

from logs.logging_config import get_core_logger

logger = get_core_logger("transport.input_handler")


class InputHandler:
    """User input collection and UI tool response correlation.

    Extracted from ``SimpleTransport`` (Phase 5).

    Responsibilities:
    - Registering AG2 ``InputRequestEvent`` callbacks per chat.
    - Resolving user-submitted input to the correct callback.
    - Managing ``pending_ui_tool_responses`` Futures for interactive UI components.
    - Applying declarative ``ui_response`` triggers via derived context managers.

    Dependencies (injected via constructor):
    - ``event_sender`` — for emitting ack events via ``send_event_to_ui``.
    - ``ui_tool_metadata`` — shared dict (written by ``EventSender``, consumed here).
    - ``pending_ui_tool_responses`` — dict of ``asyncio.Future`` keyed by event_id.
    - ``derived_context_managers`` — per-chat trigger managers (shared with facade).
    """

    def __init__(
        self,
        *,
        event_sender: Any,
        ui_tool_metadata: Dict[str, Dict[str, Any]],
        pending_ui_tool_responses: Dict[str, asyncio.Future],
        derived_context_managers: Dict[str, Any],
    ) -> None:
        self._event_sender = event_sender
        self._ui_tool_metadata = ui_tool_metadata
        self.pending_ui_tool_responses = pending_ui_tool_responses
        self._derived_context_managers = derived_context_managers
        self._input_request_registries: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # AG2 Input Request management
    # ------------------------------------------------------------------

    def register_orchestration_input_registry(
        self, chat_id: str, registry: Dict[str, Any]
    ) -> None:
        self._input_request_registries[chat_id] = registry

    def register_input_request(
        self, chat_id: str, request_id: str, respond_cb: Any
    ) -> str:
        normalized_id = str(request_id) if request_id is not None else ""
        if not normalized_id or normalized_id.lower() == "none":
            normalized_id = uuid.uuid4().hex
            logger.debug(
                f"Generated fallback input request id {normalized_id} for chat {chat_id}"
            )
        if chat_id not in self._input_request_registries:
            self._input_request_registries[chat_id] = {}
        self._input_request_registries[chat_id][normalized_id] = respond_cb
        logger.debug(f"Registered input request {normalized_id} for chat {chat_id}")
        return normalized_id

    async def submit_user_input(self, input_request_id: str, user_input: str) -> bool:
        """Submit user input response for a pending input request.

        Called by the API endpoint or the WebSocket dispatch loop when
        the frontend submits user input.
        """
        logger.info(
            f"🔍 [INPUT_SUBMIT] Looking for request_id={input_request_id} "
            f"in {len(self._input_request_registries)} chat registries"
        )
        for cid, reg in self._input_request_registries.items():
            logger.info(
                f"  📋 [INPUT_SUBMIT] chat={cid} has {len(reg)} pending requests: "
                f"{list(reg.keys())}"
            )

        # First try orchestration registry respond callback(s)
        handled = False
        ack_chat_id = None
        for chat_id, reg in list(self._input_request_registries.items()):
            respond_cb = reg.get(input_request_id)
            if respond_cb:
                logger.info(
                    f"✅ [INPUT_SUBMIT] Found callback for {input_request_id} in chat {chat_id}"
                )
            if respond_cb:
                try:
                    logger.info(
                        f"🚀 [INPUT_SUBMIT] Invoking respond callback with "
                        f"user_input='{user_input[:50]}...'"
                    )
                    # Support both async and sync lambdas assigned by AG2
                    result = respond_cb(user_input)
                    if asyncio.iscoroutine(result):
                        await result
                    handled = True
                    ack_chat_id = chat_id
                    logger.info(
                        f"✅ [INPUT] Respond callback invoked for request "
                        f"{input_request_id} (chat {chat_id})"
                    )
                except Exception as e:
                    logger.error(
                        f"❌ [INPUT] Respond callback failed {input_request_id}: {e}",
                        exc_info=True,
                    )
                finally:
                    try:
                        del reg[input_request_id]
                    except Exception:
                        pass
                break

        if handled:
            # Emit chat.input_ack for B9/B10 protocol compliance
            if ack_chat_id:
                try:
                    await self._event_sender.send_event_to_ui(
                        {
                            'kind': 'input_ack',
                            'request_id': input_request_id,
                            'corr': input_request_id,
                        },
                        ack_chat_id,
                    )
                except Exception as e:
                    logger.warning(f"Failed to emit input_ack: {e}")
            return True

        logger.error(f"❌ [INPUT] No active request found for {input_request_id}")
        return False

    def build_resume_signal(self, chat_id: str, request_id: str) -> str:
        """Produce a non-empty fallback message when resuming pending input requests.

        Ensures downstream ChatCompletion payloads always contain valid user content
        even when lifecycle tools resume execution without explicit text input.

        Note: This is an internal coordination signal for AG2 continuation. It should
        never be persisted to the database or shown in the UI.
        """
        return "[SYSTEM_RESUME_SIGNAL] Continue workflow execution after UI tool response."

    # ------------------------------------------------------------------
    # UI tool response correlation
    # ------------------------------------------------------------------

    async def wait_for_ui_tool_response(
        self, event_id: str, timeout: Optional[float] = 300.0
    ) -> Dict[str, Any]:
        """Await a UI tool response with an optional timeout.

        Args:
            event_id: Correlation id originally sent in the ui_tool_event.
            timeout: Seconds to wait before raising TimeoutError (None = wait forever).
        """
        if event_id not in self.pending_ui_tool_responses:
            self.pending_ui_tool_responses[event_id] = asyncio.Future()

        fut = self.pending_ui_tool_responses[event_id]
        try:
            response_data = (
                await asyncio.wait_for(fut, timeout=timeout) if timeout else await fut
            )
            return response_data
        except asyncio.TimeoutError:
            if not fut.done():
                fut.set_exception(asyncio.TimeoutError("UI tool response timed out"))
            logger.error(f"⏰ UI tool response timed out for event {event_id}")
            raise
        finally:
            self.pending_ui_tool_responses.pop(event_id, None)

    async def submit_ui_tool_response(
        self, event_id: str, response_data: Dict[str, Any]
    ) -> bool:
        """Submit response data for a pending UI tool event.

        Called by an API endpoint when the frontend submits data from an
        interactive UI component.
        """
        if event_id in self.pending_ui_tool_responses:
            future = self.pending_ui_tool_responses[event_id]
            if not future.done():
                future.set_result(response_data)
                logger.info(f"✅ [UI_TOOL] Submitted response for event {event_id}")
                metadata = self._ui_tool_metadata.pop(event_id, None)
                if metadata:
                    display_mode = (metadata.get("display") or "").lower()
                    chat_ref = metadata.get("chat_id")
                    tool_name = metadata.get("tool_name")

                    # Apply declarative ui_response triggers into AG2 ContextVariables
                    if chat_ref and tool_name:
                        manager = self._derived_context_managers.get(chat_ref)
                        if manager and hasattr(manager, "apply_ui_tool_response"):
                            try:
                                updated = manager.apply_ui_tool_response(
                                    tool_name=str(tool_name),
                                    response_data=(
                                        response_data if isinstance(response_data, dict) else {}
                                    ),
                                )
                                if updated:
                                    logger.info(
                                        f"🧭 [UI_TOOL] Applied ui_response triggers: "
                                        f"chat={chat_ref} tool={tool_name} vars={updated}"
                                    )
                            except Exception as trigger_err:
                                logger.debug(
                                    f"[UI_TOOL] ui_response trigger apply failed: {trigger_err}"
                                )

                    if display_mode == "artifact":
                        try:
                            await self._event_sender.send_event_to_ui(
                                {
                                    "kind": "ui_tool_dismiss",
                                    "event_id": event_id,
                                    "ui_tool_id": metadata.get("tool_name"),
                                },
                                chat_ref,
                            )
                            logger.debug(
                                f"🧹 [UI_TOOL] Emitted dismiss event for artifact {event_id}"
                            )
                        except Exception as dismiss_err:
                            logger.debug(
                                f"⚠️ [UI_TOOL] Failed to emit dismiss event "
                                f"for {event_id}: {dismiss_err}"
                            )
                return True
            else:
                self._ui_tool_metadata.pop(event_id, None)
                logger.warning(f"⚠️ [UI_TOOL] Event {event_id} already completed")
                return False
        else:
            logger.warning(f"⚠️ [UI_TOOL] No pending event found for {event_id}")
            return False
