# ==============================================================================
# FILE: mozaiksai/core/transport/workflow_bridge.py
# DESCRIPTION: Workflow integration layer - orchestration execution bridge
# ==============================================================================
"""
Workflow bridge mixin for SimpleTransport.

This module handles workflow orchestration integration:
- API-driven workflow execution
- Background workflow running
- Workflow pausing/resuming
- Lifecycle event emission

Usage:
    class SimpleTransport(WorkflowBridgeMixin):
        ...
"""
from __future__ import annotations

import asyncio
import logging
import traceback
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, Optional

from mozaiksai.core.runtime.extensions import get_workflow_lifecycle_hooks
from mozaiksai.core.transport.session_registry import session_registry

if TYPE_CHECKING:
    pass

logger = logging.getLogger("simple_transport.workflow")


class WorkflowBridgeMixin:
    """Mixin providing workflow integration functionality.

    Expects the following attributes on the class:
        - _input_request_registries: Dict[str, Dict[str, Any]]
        - _workflow_spawn_semaphore: asyncio.Semaphore
        - _background_tasks: Dict[str, asyncio.Task]
        - connections: Dict[str, Dict[str, Any]]
        - submit_user_input(request_id, input): method
        - process_incoming_user_message(...): method
        - send_error(message, code, chat_id): method
        - _build_resume_signal(chat_id, request_id): method
    """

    # ==================================================================================
    # API-DRIVEN WORKFLOW EXECUTION
    # ==================================================================================

    async def handle_user_input_from_api(
        self,
        chat_id: str,
        user_id: Optional[str],
        workflow_name: str,
        message: Optional[str],
        app_id: str,
        initial_agent_name_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Handle user input from the POST API endpoint with smart routing.

        Checks if there's an active AG2 GroupChat session waiting for input.
        If yes, passes message to existing session. If no, starts new workflow.
        """
        try:
            starting_new_workflow = False

            # Load workflow-declared lifecycle hooks (modular, per-workflow)
            lifecycle = get_workflow_lifecycle_hooks(workflow_name)
            _emit_execution_started = lifecycle.get("on_start")
            _emit_execution_completed = lifecycle.get("on_complete")
            _emit_execution_failed = lifecycle.get("on_fail")

            # Check if there's an active AG2 session waiting for user input
            has_active_session = bool(self._input_request_registries.get(chat_id))

            # Also check if there are pending input callbacks for this chat
            active_callbacks = False
            if chat_id in self._input_request_registries:
                active_callbacks = bool(self._input_request_registries[chat_id])

            logger.info(f"[SMART_ROUTING] chat={chat_id} has_registry={has_active_session} has_callbacks={active_callbacks}")

            if has_active_session and active_callbacks:
                # Route to existing AG2 session via WebSocket callback mechanism
                logger.info(f"[SMART_ROUTING] Continuing existing AG2 session for chat {chat_id}")

                # Get any available request_id from the registry
                registry = self._input_request_registries.get(chat_id, {})
                if registry:
                    # Get the first available request_id
                    request_id = next(iter(registry.keys()))

                    normalized_message = message
                    resume_signal = False
                    if not normalized_message or (isinstance(normalized_message, str) and not normalized_message.strip()):
                        normalized_message = self._build_resume_signal(chat_id, request_id)
                        resume_signal = True

                    success = await self.submit_user_input(request_id, str(normalized_message))

                    if success:
                        route = "existing_session_resume" if resume_signal else "existing_session"
                        # Don't persist/echo resume signal messages - they're internal coordination only
                        if not resume_signal:
                            # Only persist actual user messages to database
                            try:
                                await self.process_incoming_user_message(
                                    chat_id=chat_id,
                                    user_id=user_id,
                                    content=message,
                                    source='http'
                                )
                            except Exception as persist_err:
                                logger.debug(f"User message persistence failed (non-fatal): {persist_err}")
                        return {"status": "success", "chat_id": chat_id, "message": "Input passed to existing AG2 session.", "route": route}
                    else:
                        logger.warning(f"[SMART_ROUTING] Failed to submit input to existing session, falling back to new workflow")

            # No active session or callback failed - start new workflow
            logger.info(f"[SMART_ROUTING] Starting new workflow for chat {chat_id}")
            starting_new_workflow = True

            from mozaiksai.core.adapters.ag2_orchestration import get_ag2_adapter
            from mozaiksai.core.ports.orchestration import RunRequest

            # Only persist and echo user message when starting NEW workflows
            # For existing sessions, the message goes directly to AG2 via callback
            if message:
                try:
                    await self.process_incoming_user_message(
                        chat_id=chat_id,
                        user_id=user_id,
                        content=message,
                        source='http'
                    )
                except Exception as persist_err:
                    logger.debug(f"Early persistence of user message failed (non-fatal): {persist_err}")

            # Build lifecycle reporting (best-effort; non-blocking).
            if _emit_execution_started is not None:
                try:
                    asyncio.create_task(
                        _emit_execution_started(
                            app_id=app_id,
                            execution_id=chat_id,
                            chat_id=chat_id,
                            user_id=user_id,
                            workflow_name=workflow_name,
                        )
                    )
                except Exception:
                    pass

            # Launch orchestration via OrchestrationPort (engine-agnostic)
            adapter = get_ag2_adapter()
            await adapter.run(RunRequest(
                workflow_name=workflow_name,
                app_id=app_id,
                chat_id=chat_id,
                user_id=user_id,
                initial_message=None,  # already persisted & sent upstream
                initial_agent_name_override=initial_agent_name_override,
            ))

            if _emit_execution_completed is not None:
                try:
                    asyncio.create_task(
                        _emit_execution_completed(
                            app_id=app_id,
                            execution_id=chat_id,
                            chat_id=chat_id,
                            user_id=user_id,
                            workflow_name=workflow_name,
                        )
                    )
                except Exception:
                    pass

            return {"status": "success", "chat_id": chat_id, "message": "Workflow started successfully.", "route": "new_workflow"}

        except Exception as e:
            logger.error(f"User input handling failed for chat {chat_id}: {e}\n{traceback.format_exc()}")
            if starting_new_workflow and _emit_execution_failed is not None:
                try:
                    err_details = traceback.format_exc()
                    asyncio.create_task(
                        _emit_execution_failed(
                            app_id=app_id,
                            execution_id=chat_id,
                            chat_id=chat_id,
                            user_id=user_id,
                            workflow_name=workflow_name,
                            message=str(e),
                            details=str(err_details) if isinstance(err_details, str) else None,
                        )
                    )
                except Exception:
                    pass
            await self.send_error(
                error_message=f"An internal error occurred: {e}",
                error_code="WORKFLOW_EXECUTION_FAILED",
                chat_id=chat_id
            )
            return {"status": "error", "chat_id": chat_id, "message": str(e)}

    # ==================================================================================
    # BACKGROUND WORKFLOW EXECUTION
    # ==================================================================================

    async def _run_workflow_background(
        self,
        *,
        chat_id: str,
        workflow_name: str,
        app_id: str,
        user_id: str,
        ws_id: Optional[int],
        initial_message: Optional[str] = None,
        initial_agent_name_override: Optional[str] = None,
    ) -> None:
        """Run a workflow orchestration in the background.

        This enables parallel execution of multiple independent chats (each with its
        own chat_id) while preserving AG2-native semantics within each chat.
        """
        try:
            async with self._workflow_spawn_semaphore:
                try:
                    result = await self.handle_user_input_from_api(
                        chat_id=chat_id,
                        user_id=user_id,
                        workflow_name=workflow_name,
                        message=initial_message,
                        app_id=app_id,
                        initial_agent_name_override=initial_agent_name_override,
                    )
                    # Emit run_complete success asynchronously to dispatcher
                    try:
                        from mozaiksai.core.events.unified_event_dispatcher import get_event_dispatcher

                        dispatcher = get_event_dispatcher()
                        asyncio.create_task(
                            dispatcher.emit(
                                "chat.run_complete",
                                {
                                    "chat_id": chat_id,
                                    "workflow_name": workflow_name,
                                    "app_id": app_id,
                                    "user_id": user_id,
                                    "status": "completed",
                                },
                            )
                        )
                    except Exception:
                        pass
                except Exception:
                    # Emit failed run_complete before re-raising so listeners can react
                    try:
                        from mozaiksai.core.events.unified_event_dispatcher import get_event_dispatcher

                        dispatcher = get_event_dispatcher()
                        asyncio.create_task(
                            dispatcher.emit(
                                "chat.run_complete",
                                {
                                    "chat_id": chat_id,
                                    "workflow_name": workflow_name,
                                    "app_id": app_id,
                                    "user_id": user_id,
                                    "status": "failed",
                                },
                            )
                        )
                    except Exception:
                        pass
                    raise
        except asyncio.CancelledError:
            # Treat cancellation as an explicit pause request (adapter-driven).
            logger.info(
                "Background workflow cancelled (paused) workflow=%s chat=%s",
                workflow_name,
                chat_id,
            )
            raise
        except Exception as e:
            logger.error(
                f"Background workflow run failed (workflow={workflow_name} chat={chat_id}): {e}",
                exc_info=True,
            )
            try:
                await self.send_error(
                    error_message=f"Background workflow failed: {e}",
                    error_code="WORKFLOW_BACKGROUND_FAILED",
                    chat_id=chat_id,
                )
            except Exception:
                pass
        finally:
            # Drop task handle
            try:
                self._background_tasks.pop(chat_id, None)
            except Exception:
                pass

            # Mark completed ONLY if we weren't cancelled.
            try:
                if ws_id:
                    task = asyncio.current_task()
                    was_cancelled = bool(task and task.cancelled())
                    if not was_cancelled:
                        session_registry.complete_workflow(ws_id, chat_id)
            except Exception:
                pass

    # ==================================================================================
    # WORKFLOW PAUSE/RESUME
    # ==================================================================================

    async def pause_background_workflow(self, *, chat_id: str, reason: str = "paused") -> bool:
        """Cancel a running background workflow task so it can be resumed later.

        This is runtime-level orchestration only: AG2 state is persisted to Mongo,
        and resuming replays messages + continues from history.
        """
        task = self._background_tasks.get(chat_id)
        if not task:
            return False
        if task.done():
            return False

        # Best-effort: mark session as paused in the runtime registry.
        try:
            conn = self.connections.get(chat_id) or {}
            ws_id = conn.get("ws_id")
            if ws_id:
                # switch_workflow will mark the previous active chat paused; we also
                # want this chat paused if it was active.
                ctx = session_registry.get_workflow_by_chat_id(ws_id, chat_id)
                if ctx and getattr(ctx, "status", None) != "completed":
                    ctx.status = "paused"
        except Exception:
            pass

        # Emit a lightweight runtime event for observability.
        try:
            from mozaiksai.core.events.unified_event_dispatcher import get_event_dispatcher

            dispatcher = get_event_dispatcher()
            if dispatcher:
                await dispatcher.emit(
                    "runtime.workflow_paused",
                    {"chat_id": chat_id, "reason": str(reason)},
                )
        except Exception:
            pass

        task.cancel()
        return True

    # ==================================================================================
    # SIMPLIFIED CHAT MESSAGE API
    # ==================================================================================

    async def send_chat_message(
        self,
        message: str,
        agent_name: Optional[str] = None,
        chat_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """Send chat message to user interface."""
        # Create properly formatted event data with 'kind' field for envelope builder
        event_data = {
            "kind": "text",
            "agent": agent_name or "Agent",
            "content": str(message),
            "chat_id": chat_id,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        if metadata:
            event_data["metadata"] = metadata

        # Enhanced logging for debugging UI rendering
        logger.info(f"Sending chat message: kind={event_data['kind']} agent='{agent_name}' content_len={len(message)}")

        await self.send_event_to_ui(event_data, chat_id)
