# ==============================================================================
# FILE: mozaiksai/transport/websocket/handler.py
# DESCRIPTION: SimpleTransport facade — singleton composing focused sub-modules
# ==============================================================================
"""Slim facade for the ``SimpleTransport`` singleton.

Phase 5 of the pipeline refactor decomposed the original 2785-line class into
four focused modules:

- ``ConnectionManager``  — WS protocol, buffering, heartbeat, serialization
- ``EventSender``        — send_event_to_ui, visibility gating, trace downgrading
- ``InputHandler``       — user input collection, UI tool response correlation
- ``MessageRouter``      — inbound validation, routing, persistence, artifact actions

``SimpleTransport`` remains the public entry point.  External callers still use
``SimpleTransport.get_instance()`` and the same method names; internally every
call delegates to the appropriate sub-module.
"""
import asyncio
import importlib
import json
import os
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

from fastapi import WebSocket

from mozaiksai.transport.websocket.connection_manager import ConnectionManager
from mozaiksai.transport.websocket.connection_state import ConnectionState
from mozaiksai.transport.websocket.event_sender import (
    EventSender,
    _extract_clean_content,
)
from mozaiksai.transport.websocket.input_handler import InputHandler
from mozaiksai.transport.websocket.message_router import MessageRouter

# AG2 adapter: provides serialization functions injected into transport components
# so that transport itself contains zero AG2 knowledge.
from mozaiksai.adapters.ag2.serializer import (
    serialize_ag2_object as _ag2_serialize,
    extract_sender_name as _ag2_sender_name,
)

# Import workflow configuration for agent visibility filtering
from mozaiksai.kernel.workflow_manager import workflow_manager

# Enhanced logging setup
from logs.logging_config import get_core_logger

# Session manager for multi-workflow navigation
from mozaiksai.runtime.sessions import session_manager
from mozaiksai.transport.websocket.registry import session_registry

# Runtime extensions (workflow-declared lifecycle hooks)
from mozaiksai.runtime.extensions.extensions import get_workflow_lifecycle_hooks

# Get our enhanced loggers
logger = get_core_logger("simple_transport")


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def _load_general_agent_service():
    """Load the non-AG2 capability executor used for "general" mode."""
    module_path = os.getenv("MOZAIKS_GENERAL_AGENT_MODULE", "core.capabilities.simple_llm")
    factory_name = os.getenv("MOZAIKS_GENERAL_AGENT_FACTORY", "get_general_capability_service")
    try:
        module = importlib.import_module(module_path)
        factory = getattr(module, factory_name, None)
        if callable(factory):
            return factory()
        logger.debug(
            "General agent factory not callable",
            extra={"module": module_path, "factory": factory_name},
        )
    except Exception as exc:
        logger.debug(
            "General agent service unavailable",
            extra={"module": module_path, "factory": factory_name, "error": str(exc)},
        )
    return None


def _load_platform_build_lifecycle() -> dict:
    """Load platform-level build lifecycle hooks from an optional external module."""
    empty: dict = {
        "is_build_workflow": None,
        "emit_build_started": None,
        "emit_build_completed": None,
        "emit_build_failed": None,
    }
    module_path = os.getenv("MOZAIKS_PLATFORM_BUILD_LIFECYCLE_MODULE", "")
    if not module_path:
        return empty
    try:
        mod = importlib.import_module(module_path)
        result = dict(empty)
        for key in empty:
            val = getattr(mod, key, None)
            if callable(val):
                result[key] = val
        return result
    except Exception as exc:
        logger.debug(f"Platform build lifecycle unavailable: {exc}")
        return empty


async def handle_user_input_api(
    chat_id: str,
    input_request_id: str,
    user_input: str,
) -> bool:
    """Module-level convenience wrapper for SimpleTransport.submit_user_input."""
    transport = await SimpleTransport.get_instance()
    return await transport.submit_user_input(input_request_id, user_input)


# ==============================================================================
# MAIN TRANSPORT CLASS (FACADE)
# ==============================================================================


class SimpleTransport:
    """Lean transport facade composing four focused sub-modules.

    Features:
    - Message filtering (removes AutoGen noise)
    - WebSocket connection management
    - Event forwarding to the UI
    - Thread-safe singleton pattern
    """

    _instance = None
    _lock = asyncio.Lock()

    @classmethod
    async def get_instance(cls, *args, **kwargs):
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance.__init__(*args, **kwargs)
                    cls._instance = instance
        return cls._instance

    def __init__(self):
        """Singleton initializer (idempotent)."""
        if getattr(self, '_initialized', False):
            return

        # Shared mutable state (passed by reference to sub-modules)
        _ui_tool_metadata: Dict[str, Dict[str, Any]] = {}
        _pending_ui_tool_responses: Dict[str, asyncio.Future] = {}
        self._derived_context_managers: Dict[str, Any] = {}

        # 1. ConnectionManager — owns connections, heartbeat, buffering
        # Phase 3: AG2 serialize_fn injected so CM uses adapter serialization
        self._connection_manager = ConnectionManager(
            serialize_fn=_ag2_serialize,
        )

        # 2. EventSender — send_event_to_ui, visibility, trace
        # Phase 3: AG2 sender_name_fn injected so EventSender uses adapter extraction
        self._event_sender = EventSender(
            connection_manager=self._connection_manager,
            ui_tool_metadata=_ui_tool_metadata,
            persist_ui_tool_cb=self._persist_ui_tool_state,
            sender_name_fn=_ag2_sender_name,
        )

        # 3. InputHandler — user input, UI tool response correlation
        self._input_handler = InputHandler(
            event_sender=self._event_sender,
            ui_tool_metadata=_ui_tool_metadata,
            pending_ui_tool_responses=_pending_ui_tool_responses,
            derived_context_managers=self._derived_context_managers,
        )

        # 4. MessageRouter — validation, routing, persistence, artifact actions
        self._message_router = MessageRouter(
            connection_manager=self._connection_manager,
            event_sender=self._event_sender,
        )

        # Background workflow execution (for parallel child chats)
        self._background_tasks: Dict[str, asyncio.Task] = {}
        try:
            max_parallel = int(os.environ.get("MOZAIKS_MAX_PARALLEL_WORKFLOWS", "4"))
        except Exception:
            max_parallel = 4
        self._workflow_spawn_semaphore = asyncio.Semaphore(max(1, max_parallel))

        # Expose sub-module state on self for attribute access
        self.connections = self._connection_manager.connections
        self.pending_ui_tool_responses = _pending_ui_tool_responses
        self._ui_tool_metadata = _ui_tool_metadata
        self._input_request_registries = self._input_handler._input_request_registries

        # Usage emission fan-out (measurement only; no billing enforcement).
        try:
            from mozaiksai.kernel.dispatcher import get_event_dispatcher

            dispatcher = get_event_dispatcher()
            dispatcher.register_handler(
                "chat.usage_delta", self._event_sender.handle_usage_delta_event
            )
            dispatcher.register_handler(
                "chat.usage_summary", self._event_sender.handle_usage_summary_event
            )
        except Exception:
            logger.debug("Usage event handler registration skipped", exc_info=True)

        self._initialized = True
        logger.info("🚀 SimpleTransport singleton initialized")

    # ==================================================================
    # DELEGATED PUBLIC API — EventSender
    # ==================================================================

    async def send_event_to_ui(self, event: Any, chat_id: Optional[str] = None) -> None:
        await self._event_sender.send_event_to_ui(event, chat_id)

    async def send_error(
        self, error_message: str, error_code: str = "GENERAL_ERROR", chat_id: Optional[str] = None
    ) -> None:
        await self._event_sender.send_error(error_message, error_code, chat_id)

    async def send_chat_message(
        self,
        message: str,
        agent_name: Optional[str] = None,
        chat_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        await self._event_sender.send_chat_message(message, agent_name, chat_id, metadata)

    async def send_ui_tool_event(
        self,
        event_id: str,
        chat_id: Optional[str],
        tool_name: str,
        component_name: str,
        display_type: str,
        payload: Dict[str, Any],
        awaiting_response: bool = True,
        agent_name: Optional[str] = None,
    ) -> None:
        await self._event_sender.send_ui_tool_event(
            event_id, chat_id, tool_name, component_name, display_type,
            payload, awaiting_response, agent_name,
        )

    def should_show_to_user(self, agent_name: Optional[str], chat_id: Optional[str] = None) -> bool:
        return self._event_sender.should_show_to_user(agent_name, chat_id)

    def _extract_clean_content(self, message: Union[str, Dict[str, Any], Any]) -> str:
        return _extract_clean_content(message)

    def _extract_event_sender_name(self, event: Any) -> Optional[str]:
        return self._event_sender.extract_event_sender_name(event)

    def _sanitize_trace_content(self, content: str, *, limit: int = 800):
        return self._event_sender.sanitize_trace_content(content, limit=limit)

    # ==================================================================
    # DELEGATED PUBLIC API — InputHandler
    # ==================================================================

    async def submit_user_input(self, input_request_id: str, user_input: str) -> bool:
        return await self._input_handler.submit_user_input(input_request_id, user_input)

    def register_orchestration_input_registry(self, chat_id: str, registry: Dict[str, Any]) -> None:
        self._input_handler.register_orchestration_input_registry(chat_id, registry)

    def register_input_request(self, chat_id: str, request_id: str, respond_cb: Any) -> str:
        return self._input_handler.register_input_request(chat_id, request_id, respond_cb)

    def _build_resume_signal(self, chat_id: str, request_id: str) -> str:
        return self._input_handler.build_resume_signal(chat_id, request_id)

    @classmethod
    async def wait_for_ui_tool_response(cls, event_id: str, timeout: Optional[float] = 300.0) -> Dict[str, Any]:
        instance = await cls.get_instance()
        if not instance:
            raise RuntimeError("SimpleTransport instance not available")
        return await instance._input_handler.wait_for_ui_tool_response(event_id, timeout)

    async def submit_ui_tool_response(self, event_id: str, response_data: Dict[str, Any]) -> bool:
        return await self._input_handler.submit_ui_tool_response(event_id, response_data)

    # ==================================================================
    # DELEGATED PUBLIC API — MessageRouter
    # ==================================================================

    async def process_incoming_user_message(
        self, *, chat_id: str, user_id: Optional[str], content: str, source: str = 'ws'
    ) -> None:
        await self._message_router.process_incoming_user_message(
            chat_id=chat_id, user_id=user_id, content=content, source=source,
        )

    async def process_component_action(
        self, *, chat_id: str, app_id: str, component_id: str, action_type: str, action_data: dict
    ) -> Dict[str, Any]:
        return await self._message_router.process_component_action(
            chat_id=chat_id, app_id=app_id, component_id=component_id,
            action_type=action_type, action_data=action_data,
        )

    # ==================================================================
    # DELEGATED PUBLIC API — ConnectionManager
    # ==================================================================

    def _get_next_sequence(self, chat_id: str) -> int:
        return self._connection_manager.get_next_sequence(chat_id)

    async def _broadcast_to_websockets(
        self, event_data: Dict[str, Any], target_chat_id: Optional[str] = None
    ) -> None:
        await self._connection_manager.broadcast_to_websockets(event_data, target_chat_id)

    # ==================================================================
    # Context trigger manager registry (shared state)
    # ==================================================================

    def register_derived_context_manager(self, chat_id: str, manager: Any) -> None:
        if not chat_id:
            return
        self._derived_context_managers[chat_id] = manager

    def unregister_derived_context_manager(self, chat_id: str) -> None:
        if not chat_id:
            return
        self._derived_context_managers.pop(chat_id, None)

    # ==================================================================
    # Persistence helpers (used by facade methods & EventSender callback)
    # ==================================================================

    def _get_or_create_persistence_manager(self):
        """Return cached AG2PersistenceManager instance (lazy import)."""
        pm = getattr(self, "_persistence_manager", None)
        if pm is None:
            from mozaiksai.runtime.data.persistence.persistence_manager import AG2PersistenceManager

            pm = AG2PersistenceManager()
            self._persistence_manager = pm
        return pm

    async def _resolve_chat_context(
        self,
        chat_id: Optional[str],
        *,
        pm,
        payload_workflow: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        """Resolve app/workflow for a chat regardless of live connection."""
        if not chat_id:
            return None, payload_workflow

        app_id: Optional[str] = None
        workflow_name: Optional[str] = payload_workflow

        conn = self.connections.get(chat_id)
        if conn:
            raw_ent = conn.app_id
            if raw_ent:
                app_id = str(raw_ent)
            if not workflow_name:
                workflow_name = conn.workflow_name

        if app_id and workflow_name:
            return app_id, workflow_name

        try:
            coll = await pm._coll()
            doc = await coll.find_one({"_id": chat_id}, {"app_id": 1, "workflow_name": 1})
            if doc:
                if not app_id and doc.get("app_id") is not None:
                    app_id = str(doc.get("app_id"))
                if not workflow_name and doc.get("workflow_name"):
                    workflow_name = doc.get("workflow_name")
        except Exception as ctx_err:
            logger.debug(f"[UI_TOOL] Context lookup failed for chat {chat_id}: {ctx_err}")

        if chat_id in self.connections:
            conn = self.connections[chat_id]
            if app_id and not conn.app_id:
                conn.app_id = app_id
            if workflow_name and not conn.workflow_name:
                conn.workflow_name = workflow_name

        return app_id, workflow_name

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
        except Exception as pm_err:  # pragma: no cover
            logger.debug(f"[UI_TOOL] Persistence manager unavailable: {pm_err}")
            return

        try:
            app_id, w_name = await self._resolve_chat_context(
                chat_id,
                pm=pm,
                payload_workflow=payload.get("workflow_name"),
            )
            if not app_id:
                logger.debug(
                    f"[UI_TOOL] Missing app_id for chat {chat_id}; skipping last_artifact persist"
                )
                return

            try:
                sanitized_payload = json.loads(json.dumps(payload))
            except Exception:
                sanitized_payload = payload

            artifact_doc = {
                "ui_tool_id": tool_name,
                "event_id": event_id,
                "display": normalized_mode,
                "workflow_name": payload.get("workflow_name") or w_name,
                "payload": sanitized_payload,
            }
            await pm.update_last_artifact(
                chat_id=chat_id,
                app_id=app_id,
                artifact=artifact_doc,
            )
        except Exception as persist_err:
            logger.debug(
                f"[UI_TOOL] Failed to persist last_artifact for chat {chat_id}: {persist_err}"
            )

    # ==================================================================
    # General mode helpers
    # ==================================================================

    async def _ensure_general_chat_context(
        self,
        *,
        chat_id: str,
        force_new: bool = False,
    ) -> Dict[str, Any]:
        """Return (or create) the general chat context associated with this connection."""
        conn = self.connections.get(chat_id)
        if not conn:
            raise RuntimeError(f"No active connection metadata for chat {chat_id}")

        if not force_new:
            existing_ctx = conn.general_session
            if isinstance(existing_ctx, dict) and existing_ctx.get("chat_id"):
                return existing_ctx

        app_id = conn.app_id
        user_id = conn.user_id or "anonymous"
        if not app_id:
            raise RuntimeError("Cannot create general chat without app context")

        pm = self._get_or_create_persistence_manager()
        session_info = await pm.create_general_chat_session(
            app_id=str(app_id),
            user_id=str(user_id),
        )

        general_ctx = {
            "chat_id": session_info.get("chat_id"),
            "label": session_info.get("label"),
            "sequence": session_info.get("sequence"),
            "app_id": str(app_id),
            "user_id": str(user_id),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        conn.general_session = general_ctx
        return general_ctx

    async def _handle_general_agent_exchange(
        self,
        *,
        chat_id: str,
        ws_id: Optional[int],
        user_message: str,
        ui_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Route a general-mode utterance to the configured non-AG2 capability executor."""
        conn = self.connections.get(chat_id)
        app_id = conn.app_id if conn else None
        user_id = conn.user_id if conn else None
        if not app_id:
            raise RuntimeError("Cannot route general-mode message without app context")

        general_ctx = await self._ensure_general_chat_context(chat_id=chat_id)
        general_chat_id = general_ctx.get("chat_id")
        general_label = general_ctx.get("label")
        if not general_chat_id:
            raise RuntimeError("Failed to resolve general chat identifier")

        workflows_payload: List[Dict[str, Any]] = []
        if ws_id:
            contexts = session_registry.get_all_workflows(ws_id)
            for ctx in contexts:
                if hasattr(ctx, "to_dict"):
                    workflows_payload.append(ctx.to_dict())
                else:
                    workflows_payload.append({
                        "chat_id": getattr(ctx, "chat_id", None),
                        "workflow_name": getattr(ctx, "workflow_name", None),
                        "status": getattr(ctx, "status", None),
                        "artifact_id": getattr(ctx, "artifact_id", None),
                        "app_id": getattr(ctx, "app_id", None),
                        "user_id": getattr(ctx, "user_id", None),
                    })

        metadata_base = {
            "source": "general_agent",
            "ui_context": ui_context or {},
            "workflows": workflows_payload,
            "general_chat_id": general_chat_id,
            "general_chat_label": general_label,
        }

        await self._persist_general_message(
            general_chat_id=str(general_chat_id),
            app_id=str(app_id),
            role="user",
            content=user_message,
            user_id=str(user_id) if user_id else None,
            metadata=metadata_base,
        )

        await self.send_event_to_ui(
            {
                "kind": "text",
                "agent": "user",
                "content": user_message,
                "chat_id": chat_id,
                "metadata": metadata_base,
            },
            chat_id,
        )

        service = _load_general_agent_service()
        if service is None:
            await self.send_chat_message(
                "General mode is not configured for this runtime.",
                agent_name="System",
                chat_id=chat_id,
                metadata=metadata_base,
            )
            return

        response = await service.generate_response(
            prompt=user_message,
            workflows=workflows_payload,
            app_id=str(app_id),
            user_id=str(user_id) if user_id else None,
            ui_context=ui_context,
        )

        assistant_metadata = {
            "source": "general_agent",
            "workflows": workflows_payload,
            "ui_context": ui_context or {},
            "general_chat_id": general_chat_id,
            "general_chat_label": general_label,
        }

        await self._persist_general_message(
            general_chat_id=str(general_chat_id),
            app_id=str(app_id),
            role="assistant",
            content=response.get("content", ""),
            user_id=str(user_id) if user_id else None,
            metadata=assistant_metadata,
        )

        await self.send_chat_message(
            response.get("content", ""),
            agent_name="Assistant",
            chat_id=chat_id,
            metadata=assistant_metadata,
        )

        usage = response.get("usage") or {}
        try:
            pm = self._get_or_create_persistence_manager()
            await pm.update_session_metrics(
                chat_id=str(general_chat_id),
                app_id=str(app_id),
                user_id=str(user_id) if user_id else "anonymous",
                workflow_name="GeneralCapability",
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                completion_tokens=int(usage.get("completion_tokens") or 0),
                cost_usd=0.0,
                agent_name="assistant",
                session_type="general",
            )
            try:
                from mozaiksai.runtime.tokens.manager import TokenManager

                await TokenManager.emit_usage_delta(
                    chat_id=str(general_chat_id),
                    app_id=str(app_id),
                    user_id=str(user_id) if user_id else "anonymous",
                    workflow_name="GeneralCapability",
                    agent_name="assistant",
                    prompt_tokens=int(usage.get("prompt_tokens") or 0),
                    completion_tokens=int(usage.get("completion_tokens") or 0),
                    total_tokens=int(
                        usage.get("total_tokens")
                        or (
                            int(usage.get("prompt_tokens") or 0)
                            + int(usage.get("completion_tokens") or 0)
                        )
                    ),
                    cached=False,
                    duration_sec=0.0,
                )

                try:
                    coll = await pm._general_coll()  # type: ignore[attr-defined]
                    totals = await coll.find_one(
                        {"_id": str(general_chat_id), "app_id": str(app_id)},
                        {
                            "usage_prompt_tokens_final": 1,
                            "usage_completion_tokens_final": 1,
                            "usage_total_tokens_final": 1,
                        },
                    )
                    if isinstance(totals, dict):
                        await TokenManager.emit_usage_summary(
                            chat_id=str(general_chat_id),
                            app_id=str(app_id),
                            user_id=str(user_id) if user_id else "anonymous",
                            workflow_name="GeneralCapability",
                            prompt_tokens=int(totals.get("usage_prompt_tokens_final") or 0),
                            completion_tokens=int(totals.get("usage_completion_tokens_final") or 0),
                            total_tokens=int(totals.get("usage_total_tokens_final") or 0),
                        )
                except Exception:
                    logger.debug("Failed to emit general-mode usage summary", exc_info=True)
            except Exception:
                logger.debug("Failed to emit general-mode usage delta", exc_info=True)
        except Exception as metrics_err:
            logger.debug(f"Failed to record general-mode usage metrics: {metrics_err}")

    async def _persist_general_message(
        self,
        *,
        general_chat_id: str,
        app_id: str,
        role: str,
        content: str,
        user_id: Optional[str],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        pm = self._get_or_create_persistence_manager()
        try:
            await pm.append_general_message(
                general_chat_id=general_chat_id,
                app_id=app_id,
                role=role,
                content=content,
                user_id=user_id,
                metadata=metadata,
            )
        except Exception as persist_err:
            logger.debug(
                f"Failed to persist general agent message for {general_chat_id}: {persist_err}"
            )

    # ==================================================================
    # Background workflow execution
    # ==================================================================

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

        Enables parallel execution of multiple independent chats (each with its
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
                    try:
                        from mozaiksai.kernel.dispatcher import get_event_dispatcher

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
                    try:
                        from mozaiksai.kernel.dispatcher import get_event_dispatcher

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
            logger.info(
                "⏸️ Background workflow cancelled (paused) workflow=%s chat=%s",
                workflow_name,
                chat_id,
            )
            raise
        except Exception as e:
            logger.error(
                f"❌ Background workflow run failed (workflow={workflow_name} "
                f"chat={chat_id}): {e}",
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
            try:
                self._background_tasks.pop(chat_id, None)
            except Exception:
                pass

            try:
                if ws_id:
                    task = asyncio.current_task()
                    was_cancelled = bool(task and task.cancelled())
                    if not was_cancelled:
                        session_registry.complete_workflow(ws_id, chat_id)
            except Exception:
                pass

    async def pause_background_workflow(self, *, chat_id: str, reason: str = "paused") -> bool:
        """Cancel a running background workflow task so it can be resumed later."""
        task = self._background_tasks.get(chat_id)
        if not task:
            return False
        if task.done():
            return False

        try:
            conn = self.connections.get(chat_id)
            ws_id = conn.ws_id if conn else None
            if ws_id:
                ctx = session_registry.get_workflow_by_chat_id(ws_id, chat_id)
                if ctx and getattr(ctx, "status", None) != "completed":
                    ctx.status = "paused"
        except Exception:
            pass

        try:
            from mozaiksai.kernel.dispatcher import get_event_dispatcher

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

    # ==================================================================
    # PackTransportPort implementation
    # ==================================================================

    async def spawn_run(self, request: "RunRequest") -> asyncio.Task:
        """Spawn a workflow run as a background task via RunSupervisor.

        Implements ``PackTransportPort.spawn_run``.  All workflow execution
        flows through ``RunSupervisor.start_run()`` so capability-based
        dispatch applies.
        """
        from mozaiksai.runtime.execution.run_supervisor import get_run_supervisor

        async def _run() -> None:
            async with self._workflow_spawn_semaphore:
                try:
                    async for _ev in get_run_supervisor().start_run(request):
                        pass  # lifecycle events; UI events stream via side effects
                except Exception:
                    raise
            # Emit run_complete event for downstream listeners (pack coordinator, etc.)
            try:
                from mozaiksai.kernel.dispatcher import get_event_dispatcher

                dispatcher = get_event_dispatcher()
                asyncio.create_task(
                    dispatcher.emit(
                        "chat.run_complete",
                        {
                            "chat_id": request.chat_id,
                            "workflow_name": request.workflow_name,
                            "app_id": request.app_id,
                            "user_id": request.user_id,
                            "status": "completed",
                        },
                    )
                )
            except Exception:
                pass

        task = asyncio.create_task(_run())
        self._background_tasks[request.chat_id] = task
        return task

    def is_task_running(self, chat_id: str) -> bool:
        """Return True if a background task for *chat_id* is still running."""
        task = self._background_tasks.get(chat_id)
        return bool(task and not task.done())

    def get_task_error(self, chat_id: str) -> Optional[str]:
        """Return the error message if the task for *chat_id* failed."""
        task = self._background_tasks.get(chat_id)
        if not task or not task.done():
            return None
        try:
            task.result()  # raises if task failed
            return None
        except asyncio.CancelledError:
            return "cancelled (timeout or parent abort)"
        except Exception as exc:
            return str(exc)

    def get_persistence(self) -> Any:
        """Return the persistence manager (``PackTransportPort``)."""
        return self._get_or_create_persistence_manager()

    def get_connection_meta(self, chat_id: str) -> Optional[Dict[str, Any]]:
        """Return connection metadata for *chat_id* (``PackTransportPort``)."""
        conn = self.connections.get(chat_id)
        if conn is None:
            return None
        return {
            "app_id": conn.app_id,
            "user_id": conn.user_id,
            "ws_id": conn.ws_id,
            "websocket": conn.websocket,
            "frontend_context": getattr(conn, "frontend_context", None),
        }

    async def setup_child_connection(
        self,
        *,
        source_chat_id: str,
        target_chat_id: str,
        workflow_name: str,
        app_id: str,
        user_id: str,
    ) -> None:
        """Clone WebSocket routing from source to target chat (``PackTransportPort``)."""
        from mozaiksai.transport.websocket.connection_state import ConnectionState

        source_conn = self.connections.get(source_chat_id)
        if source_conn is None:
            return
        websocket = source_conn.websocket
        ws_id = source_conn.ws_id
        if websocket is None or ws_id is None:
            return

        existing = self.connections.get(target_chat_id)
        frontend_context = (
            (existing.frontend_context if existing else None)
            or source_conn.frontend_context
        )

        if existing:
            existing.websocket = websocket
            existing.user_id = user_id
            existing.workflow_name = workflow_name
            existing.app_id = app_id
            existing.active = True
            existing.ws_id = ws_id
            if frontend_context and isinstance(frontend_context, dict):
                existing.frontend_context = frontend_context
        else:
            self.connections[target_chat_id] = ConnectionState(
                websocket=websocket,
                user_id=user_id,
                workflow_name=workflow_name,
                app_id=app_id,
                active=True,
                ws_id=ws_id,
                frontend_context=frontend_context if isinstance(frontend_context, dict) else None,
            )

    async def flush_pre_connection_buffers(self, chat_id: str) -> None:
        """Flush events buffered before the connection alias was set up."""
        try:
            buffers = getattr(self, "_pre_connection_buffers", None)
            if not isinstance(buffers, dict):
                return
            buffered = buffers.pop(chat_id, None)
            if not buffered or not isinstance(buffered, list):
                return
            for msg in buffered:
                try:
                    await self._queue_message_with_backpressure(chat_id, msg)
                except Exception:
                    continue
            try:
                await self._flush_message_queue(chat_id)
            except Exception:
                return
        except Exception:
            return

    # ==================================================================
    # HTTP POST entry point
    # ==================================================================

    async def handle_user_input_from_api(
        self,
        chat_id: str,
        user_id: Optional[str],
        workflow_name: str,
        message: Optional[str],
        app_id: str,
        initial_agent_name_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Handle user input from the POST API endpoint with smart routing.

        Checks if there's an active AG2 GroupChat session waiting for input.
        If yes, passes message to existing session. If no, starts new workflow.
        """
        try:
            starting_new_workflow = False
            is_build = False

            # Load workflow-declared lifecycle hooks (modular, per-workflow)
            lifecycle = get_workflow_lifecycle_hooks(workflow_name)
            _is_build_workflow = lifecycle.get("is_build_workflow")
            _emit_build_started = lifecycle.get("on_start")
            _emit_build_completed = lifecycle.get("on_complete")
            _emit_build_failed = lifecycle.get("on_fail")

            try:
                if callable(_is_build_workflow):
                    is_build = bool(_is_build_workflow(workflow_name))
            except Exception:
                is_build = False

            # Check if there's an active AG2 session waiting for user input
            has_active_session = bool(self._input_request_registries.get(chat_id))
            active_callbacks = False
            if chat_id in self._input_request_registries:
                active_callbacks = bool(self._input_request_registries[chat_id])

            logger.info(
                f"🔀 [SMART_ROUTING] chat={chat_id} has_registry={has_active_session} "
                f"has_callbacks={active_callbacks}"
            )

            if has_active_session and active_callbacks:
                logger.info(
                    f"🔄 [SMART_ROUTING] Continuing existing AG2 session for chat {chat_id}"
                )

                registry = self._input_request_registries.get(chat_id, {})
                if registry:
                    request_id = next(iter(registry.keys()))

                    normalized_message = message
                    resume_signal = False
                    if not normalized_message or (
                        isinstance(normalized_message, str) and not normalized_message.strip()
                    ):
                        normalized_message = self._build_resume_signal(chat_id, request_id)
                        resume_signal = True

                    success = await self.submit_user_input(request_id, str(normalized_message))

                    if success:
                        route = "existing_session_resume" if resume_signal else "existing_session"
                        if not resume_signal:
                            try:
                                await self.process_incoming_user_message(
                                    chat_id=chat_id,
                                    user_id=user_id,
                                    content=message,
                                    source='http',
                                )
                            except Exception as persist_err:
                                logger.debug(
                                    f"User message persistence failed (non-fatal): {persist_err}"
                                )
                        return {
                            "status": "success",
                            "chat_id": chat_id,
                            "message": "Input passed to existing AG2 session.",
                            "route": route,
                        }
                    else:
                        logger.warning(
                            "⚠️ [SMART_ROUTING] Failed to submit input to existing session, "
                            "falling back to new workflow"
                        )

            # No active session or callback failed — start new workflow
            logger.info(f"🚀 [SMART_ROUTING] Starting new workflow for chat {chat_id}")
            starting_new_workflow = True

            from mozaiksai.contracts import RunRequest as _RunRequest
            from mozaiksai.runtime.execution.run_supervisor import get_run_supervisor as _get_run_supervisor

            if message:
                try:
                    await self.process_incoming_user_message(
                        chat_id=chat_id,
                        user_id=user_id,
                        content=message,
                        source='http',
                    )
                except Exception as persist_err:
                    logger.debug(
                        f"Early persistence of user message failed (non-fatal): {persist_err}"
                    )

            if is_build and _emit_build_started is not None:
                try:
                    asyncio.create_task(
                        _emit_build_started(
                            app_id=app_id,
                            build_id=chat_id,
                            user_id=user_id,
                            workflow_name=workflow_name,
                        )
                    )
                except Exception:
                    pass

            _run_req = _RunRequest(
                run_id=str(uuid.uuid4()),
                capability="agent",
                workflow_name=workflow_name,
                app_id=app_id,
                chat_id=chat_id,
                user_id=user_id,
                context={},
                metadata={"initial_agent_name_override": initial_agent_name_override},
            )
            async for _ev in _get_run_supervisor().start_run(_run_req):
                pass  # lifecycle events only; actual UI events stream via SimpleTransport

            if is_build and _emit_build_completed is not None:
                try:
                    asyncio.create_task(
                        _emit_build_completed(
                            app_id=app_id,
                            build_id=chat_id,
                            user_id=user_id,
                            workflow_name=workflow_name,
                        )
                    )
                except Exception:
                    pass

            return {
                "status": "success",
                "chat_id": chat_id,
                "message": "Workflow started successfully.",
                "route": "new_workflow",
            }

        except Exception as e:
            logger.error(
                f"❌ User input handling failed for chat {chat_id}: {e}\n{traceback.format_exc()}"
            )
            if starting_new_workflow and is_build and _emit_build_failed is not None:
                try:
                    err_details = traceback.format_exc()
                    asyncio.create_task(
                        _emit_build_failed(
                            app_id=app_id,
                            build_id=chat_id,
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
                chat_id=chat_id,
            )
            return {"status": "error", "chat_id": chat_id, "message": str(e)}

    # ==================================================================
    # WebSocket handler (main entry point)
    # ==================================================================

    async def handle_websocket(
        self,
        websocket: WebSocket,
        chat_id: str,
        user_id: str,
        workflow_name: str,
        app_id: Optional[str] = None,
        ws_id: Optional[int] = None,
    ) -> None:
        """Handle WebSocket connection for real-time communication with multi-workflow session support."""
        # --- Connection setup (ConnectionManager) ---
        await self._connection_manager.accept_connection(
            websocket, chat_id, user_id, workflow_name, app_id, ws_id,
        )

        # H5: Auto-resume for IN_PROGRESS chats
        await self._connection_manager.auto_resume_if_needed(chat_id, websocket, app_id)

        pm = self._get_or_create_persistence_manager()

        try:
            # --- Inbound dispatch loop ---
            while True:
                try:
                    msg = await websocket.receive_text()
                except Exception as recv_err:
                    raise recv_err
                if not msg:
                    await asyncio.sleep(0.05)
                    continue
                try:
                    data = json.loads(msg)
                except Exception:
                    logger.debug(
                        f"⚠️ Received non-JSON message on WS chat {chat_id}: {msg[:80]}"
                    )
                    continue

                # H3: Validate message schema
                if not self._message_router.validate_inbound_message(data):
                    await websocket.send_json({
                        "type": "chat.error",
                        "data": {
                            "message": "Invalid message schema",
                            "error_code": "SCHEMA_VALIDATION_FAILED",
                        },
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                    continue

                mtype = data.get('type') or data.get('kind')

                # ----------------------------------------------------------
                # user.input.submit
                # ----------------------------------------------------------
                if mtype in ("user.input.submit", "user_input_submit"):
                    req_id = data.get('input_request_id') or data.get('request_id')
                    text = (data.get('text') or data.get('user_input') or "").strip()
                    _conn = self.connections.get(chat_id)
                    ws_id = _conn.ws_id if _conn else None
                    ui_context_payload = data.get("context") or data.get("ui_context") or {}
                    if not isinstance(ui_context_payload, dict):
                        ui_context_payload = {}

                    logger.info(
                        f"📥 [INPUT] Received user.input.submit: chat={chat_id}, "
                        f"req_id={req_id}, text_len={len(text)}, ws_id={ws_id}"
                    )

                    is_general_mode = bool(ws_id and session_registry.is_in_general_mode(ws_id))
                    logger.info(
                        f"🔍 [INPUT] Mode check: is_general={is_general_mode}, "
                        f"has_req_id={bool(req_id)}"
                    )

                    if not req_id and is_general_mode:
                        if not text:
                            await websocket.send_json({
                                "type": "chat.error",
                                "data": {
                                    "message": "Message cannot be empty in general mode",
                                    "error_code": "GENERAL_MODE_EMPTY_MESSAGE",
                                },
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            })
                            continue
                        try:
                            await self._handle_general_agent_exchange(
                                chat_id=chat_id,
                                ws_id=ws_id,
                                user_message=text,
                                ui_context=ui_context_payload,
                            )
                            await websocket.send_json({
                                "type": "chat.input_ack",
                                "data": {"chat_id": chat_id, "status": "accepted"},
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            })
                        except Exception as general_err:
                            logger.error(
                                f"Failed to process general-mode message for {chat_id}: "
                                f"{general_err}"
                            )
                            await websocket.send_json({
                                "type": "chat.error",
                                "data": {
                                    "message": "General mode is unavailable right now. Please try again.",
                                    "error_code": "GENERAL_MODE_FAILED",
                                },
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            })
                        continue

                    if req_id:
                        logger.info(
                            f"🎯 [INPUT] Routing to submit_user_input for AG2 "
                            f"InputRequestEvent: req_id={req_id}"
                        )
                        try:
                            ok = await self.submit_user_input(req_id, text)
                            logger.info(
                                f"✅ [INPUT] submit_user_input returned: {ok} "
                                f"for req_id={req_id}"
                            )
                            await websocket.send_json({
                                "type": "ack.input",
                                "data": {
                                    "input_request_id": req_id,
                                    "status": "accepted" if ok else "rejected",
                                },
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            })
                        except Exception as ie:
                            logger.error(
                                f"❌ Failed to process inbound user input {req_id}: {ie}",
                                exc_info=True,
                            )
                    else:
                        try:
                            target_chat_id = chat_id
                            try:
                                if ws_id:
                                    active_ctx = session_registry.get_active_workflow(ws_id)
                                    if active_ctx and getattr(active_ctx, "chat_id", None):
                                        target_chat_id = str(active_ctx.chat_id)
                            except Exception:
                                target_chat_id = chat_id
                            _tgt_conn = self.connections.get(target_chat_id)
                            _src_conn = self.connections.get(chat_id)
                            resolved_user_id = (
                                (_tgt_conn.user_id if _tgt_conn else None)
                                or (_src_conn.user_id if _src_conn else None)
                            )
                            await self.process_incoming_user_message(
                                chat_id=target_chat_id,
                                user_id=resolved_user_id,
                                content=text,
                                source='ws',
                            )
                            await websocket.send_json({
                                "type": "chat.input_ack",
                                "data": {"chat_id": target_chat_id, "status": "accepted"},
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            })

                            # Resume orchestration: start a new AG2 run
                            # with the user's message so agents continue
                            # the conversation (e.g. after handoff_to_user).
                            async def _resume_orchestration():
                                try:
                                    from mozaiksai.contracts import RunRequest as _RunRequest
                                    from mozaiksai.runtime.execution.run_supervisor import get_run_supervisor as _get_run_supervisor
                                    _run_req = _RunRequest(
                                        run_id=str(uuid.uuid4()),
                                        capability="agent",
                                        workflow_name=workflow_name,
                                        app_id=app_id or "",
                                        chat_id=target_chat_id,
                                        user_id=resolved_user_id or user_id,
                                        context={"message": text},
                                        metadata={},
                                    )
                                    async for _ev in _get_run_supervisor().start_run(_run_req):
                                        pass  # lifecycle events only; actual UI events stream via SimpleTransport
                                except Exception as resume_err:
                                    logger.error(
                                        f"Resume orchestration failed for {target_chat_id}: {resume_err}",
                                        exc_info=True,
                                    )

                            asyncio.create_task(_resume_orchestration())

                        except Exception as e:
                            logger.error(
                                f"Failed to process free-form user message for {chat_id}: {e}"
                            )
                            await websocket.send_json({
                                "type": "chat.error",
                                "data": {
                                    "message": "User message failed",
                                    "error_code": "USER_MESSAGE_FAILED",
                                },
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            })
                    continue

                # ----------------------------------------------------------
                # ui_tool_response
                # ----------------------------------------------------------
                if mtype == "ui_tool_response":
                    event_id = data.get('eventId') or data.get('ui_tool_id')
                    response_data = data.get('response', {})
                    if event_id:
                        try:
                            ok = await self.submit_ui_tool_response(event_id, response_data)
                            logger.info(
                                f"✅ UI tool response received for event {event_id}: {ok}"
                            )
                            await websocket.send_json({
                                "type": "ack.ui_tool_response",
                                "data": {
                                    "eventId": event_id,
                                    "status": "accepted" if ok else "rejected",
                                },
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            })
                        except Exception as uie:
                            logger.error(
                                f"❌ Failed to process UI tool response {event_id}: {uie}"
                            )
                            await websocket.send_json({
                                "type": "chat.error",
                                "data": {
                                    "message": "UI tool response failed",
                                    "error_code": "UI_TOOL_RESPONSE_FAILED",
                                },
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            })
                    continue

                # ----------------------------------------------------------
                # chat.artifact_action
                # ----------------------------------------------------------
                if mtype == "chat.artifact_action":
                    try:
                        await self._message_router.handle_artifact_action(
                            data, chat_id, websocket
                        )
                    except Exception as ae:
                        logger.error(
                            f"❌ Failed to process artifact action for chat {chat_id}: {ae}"
                        )
                        await websocket.send_json({
                            "type": "chat.error",
                            "data": {
                                "message": "Artifact action failed",
                                "error_code": "ARTIFACT_ACTION_FAILED",
                            },
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })
                    continue

                # ----------------------------------------------------------
                # chat.switch_workflow
                # ----------------------------------------------------------
                if mtype == "chat.switch_workflow":
                    try:
                        target_chat_id = data.get("chat_id")
                        frontend_context = data.get("frontend_context")

                        if not target_chat_id:
                            raise ValueError("chat_id required for workflow switch")

                        _sw_conn = self.connections.get(chat_id)
                        ws_id = _sw_conn.ws_id if _sw_conn else None
                        if not ws_id:
                            raise ValueError("WebSocket ID not found in connection metadata")

                        if frontend_context and isinstance(frontend_context, dict):
                            if target_chat_id not in self.connections:
                                self.connections[target_chat_id] = ConnectionState()
                            self.connections[target_chat_id].frontend_context = frontend_context
                            logger.info(
                                f"📋 Stored frontend context for {target_chat_id}: "
                                f"{list(frontend_context.keys())}"
                            )

                        active_context = session_registry.switch_workflow(
                            ws_id, target_chat_id
                        )

                        if not active_context:
                            raise ValueError(
                                f"Workflow {target_chat_id} not found or already completed"
                            )

                        logger.info(
                            f"🔄 Switched from {chat_id} to {target_chat_id} (ws_id={ws_id})"
                        )

                        await websocket.send_json({
                            "type": "chat.context_switched",
                            "data": {
                                "from_chat_id": chat_id,
                                "to_chat_id": target_chat_id,
                                "workflow_name": active_context.workflow_name,
                                "artifact_id": active_context.artifact_id,
                                "app_id": active_context.app_id,
                            },
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })
                    except Exception as se:
                        logger.error(f"❌ Failed to switch workflow: {se}")
                        await websocket.send_json({
                            "type": "chat.error",
                            "data": {
                                "message": f"Workflow switch failed: {str(se)}",
                                "error_code": "SWITCH_WORKFLOW_FAILED",
                            },
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })
                    continue

                # ----------------------------------------------------------
                # chat.enter_general_mode
                # ----------------------------------------------------------
                if mtype == "chat.enter_general_mode":
                    try:
                        _gm_conn = self.connections.get(chat_id)
                        ws_id = _gm_conn.ws_id if _gm_conn else None

                        if not ws_id:
                            raise ValueError("WebSocket ID not found in connection metadata")

                        session_registry.enter_general_mode(ws_id)
                        general_ctx = await self._ensure_general_chat_context(chat_id=chat_id)
                        general_chat_id = general_ctx.get("chat_id")

                        logger.info(
                            f"💬 Entered general mode (ws_id={ws_id}, "
                            f"general_chat={general_chat_id})"
                        )

                        await websocket.send_json({
                            "type": "chat.mode_changed",
                            "data": {
                                "mode": "general",
                                "general_chat_id": general_ctx.get("chat_id"),
                                "general_chat_label": general_ctx.get("label"),
                                "general_chat_sequence": general_ctx.get("sequence"),
                            },
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })
                    except Exception as ge:
                        logger.error(f"❌ Failed to enter general mode: {ge}")
                        await websocket.send_json({
                            "type": "chat.error",
                            "data": {
                                "message": f"General mode failed: {str(ge)}",
                                "error_code": "GENERAL_MODE_FAILED",
                            },
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })
                    continue

                # ----------------------------------------------------------
                # chat.start_general_chat
                # ----------------------------------------------------------
                if mtype == "chat.start_general_chat":
                    try:
                        _gc_conn = self.connections.get(chat_id)
                        ws_id = _gc_conn.ws_id if _gc_conn else None
                        if not ws_id:
                            raise ValueError("WebSocket ID not found in connection metadata")
                        session_registry.enter_general_mode(ws_id)
                        general_ctx = await self._ensure_general_chat_context(
                            chat_id=chat_id, force_new=True
                        )

                        await websocket.send_json({
                            "type": "chat.general_session_created",
                            "data": {
                                "general_chat_id": general_ctx.get("chat_id"),
                                "general_chat_label": general_ctx.get("label"),
                                "general_chat_sequence": general_ctx.get("sequence"),
                            },
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })
                        logger.info(
                            f"🆕 Started new general chat session "
                            f"{general_ctx.get('chat_id')} (ws_id={ws_id})"
                        )
                    except Exception as gc_err:
                        logger.error(f"❌ Failed to start new general chat: {gc_err}")
                        await websocket.send_json({
                            "type": "chat.error",
                            "data": {
                                "message": f"General chat creation failed: {gc_err}",
                                "error_code": "GENERAL_CHAT_CREATE_FAILED",
                            },
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })
                    continue

                # ----------------------------------------------------------
                # chat.start_workflow
                # ----------------------------------------------------------
                if mtype == "chat.start_workflow":
                    try:
                        target_workflow = data.get("workflow_name")
                        initial_message = data.get("initial_message") or data.get("message")
                        auto_run = bool(data.get("auto_run", True))
                        initial_agent_name_override = (
                            data.get("initial_agent") or data.get("initial_agent_name")
                        )
                        frontend_context = data.get("frontend_context")

                        if not target_workflow:
                            raise ValueError("workflow_name required")

                        _sw_conn = self.connections.get(chat_id)
                        ws_id = _sw_conn.ws_id if _sw_conn else None
                        ent_id = _sw_conn.app_id if _sw_conn else None
                        usr_id = _sw_conn.user_id if _sw_conn else None

                        if not ws_id or not ent_id or not usr_id:
                            raise ValueError("Missing connection metadata")

                        if not await self._message_router.check_pack_prereqs(
                            websocket=websocket,
                            chat_id=chat_id,
                            app_id=str(ent_id),
                            user_id=str(usr_id),
                            workflow_name=str(target_workflow),
                        ):
                            continue

                        new_chat_id = f"chat_{target_workflow}_{uuid.uuid4().hex[:8]}"

                        await pm.create_chat_session(
                            chat_id=new_chat_id,
                            app_id=str(ent_id),
                            workflow_name=str(target_workflow),
                            user_id=str(usr_id),
                        )

                        if frontend_context and isinstance(frontend_context, dict):
                            if new_chat_id not in self.connections:
                                self.connections[new_chat_id] = ConnectionState()
                            self.connections[new_chat_id].frontend_context = frontend_context
                            logger.info(
                                f"📋 Stored frontend context for new workflow "
                                f"{new_chat_id}: {list(frontend_context.keys())}"
                            )

                        session_registry.add_workflow(
                            ws_id=ws_id,
                            chat_id=new_chat_id,
                            workflow_name=target_workflow,
                            app_id=ent_id,
                            user_id=usr_id,
                            auto_activate=True,
                        )

                        logger.info(
                            f"🚀 Started new workflow {target_workflow} "
                            f"(chat_id={new_chat_id}, ws_id={ws_id})"
                        )

                        await websocket.send_json({
                            "type": "chat.workflow_started",
                            "data": {
                                "chat_id": new_chat_id,
                                "workflow_name": target_workflow,
                                "app_id": ent_id,
                                "user_id": usr_id,
                            },
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })

                        if auto_run:
                            self._background_tasks[new_chat_id] = asyncio.create_task(
                                self._run_workflow_background(
                                    chat_id=new_chat_id,
                                    workflow_name=str(target_workflow),
                                    app_id=str(ent_id),
                                    user_id=str(usr_id),
                                    ws_id=ws_id,
                                    initial_message=(
                                        str(initial_message)
                                        if isinstance(initial_message, str) and initial_message.strip()
                                        else None
                                    ),
                                    initial_agent_name_override=(
                                        str(initial_agent_name_override)
                                        if isinstance(initial_agent_name_override, str)
                                        and initial_agent_name_override.strip()
                                        else None
                                    ),
                                )
                            )
                    except Exception as we:
                        logger.error(f"❌ Failed to start workflow: {we}")
                        await websocket.send_json({
                            "type": "chat.error",
                            "data": {
                                "message": f"Workflow start failed: {str(we)}",
                                "error_code": "START_WORKFLOW_FAILED",
                            },
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })
                    continue

                # ----------------------------------------------------------
                # chat.start_workflow_batch
                # ----------------------------------------------------------
                if mtype == "chat.start_workflow_batch":
                    try:
                        runs = data.get("runs")
                        activate_first = bool(data.get("activate_first", False))
                        auto_run = bool(data.get("auto_run", True))

                        _sb_conn = self.connections.get(chat_id)
                        ws_id = _sb_conn.ws_id if _sb_conn else None
                        ent_id = _sb_conn.app_id if _sb_conn else None
                        usr_id = _sb_conn.user_id if _sb_conn else None
                        if not ws_id or not ent_id or not usr_id:
                            raise ValueError("Missing connection metadata")

                        if not isinstance(runs, list) or not runs:
                            raise ValueError("runs must be a non-empty list")

                        started: List[Dict[str, Any]] = []
                        blocked: List[Dict[str, Any]] = []
                        for i, run in enumerate(runs):
                            if not isinstance(run, dict):
                                raise ValueError("Each run must be an object")
                            target_workflow = run.get("workflow_name")
                            if not target_workflow:
                                raise ValueError("Each run requires workflow_name")

                            initial_message = (
                                run.get("initial_message")
                                or run.get("message")
                                or run.get("prompt")
                            )
                            initial_agent_name_override = (
                                run.get("initial_agent") or run.get("initial_agent_name")
                            )
                            label = run.get("label")

                            if not await self._message_router.check_pack_prereqs(
                                websocket=websocket,
                                chat_id=chat_id,
                                app_id=str(ent_id),
                                user_id=str(usr_id),
                                workflow_name=str(target_workflow),
                            ):
                                blocked.append({
                                    "workflow_name": str(target_workflow),
                                    "reason": "Prerequisites not met",
                                })
                                continue

                            new_chat_id = f"chat_{target_workflow}_{uuid.uuid4().hex[:8]}"

                            await pm.create_chat_session(
                                chat_id=new_chat_id,
                                app_id=str(ent_id),
                                workflow_name=str(target_workflow),
                                user_id=str(usr_id),
                            )

                            session_registry.add_workflow(
                                ws_id=ws_id,
                                chat_id=new_chat_id,
                                workflow_name=str(target_workflow),
                                app_id=str(ent_id),
                                user_id=str(usr_id),
                                auto_activate=bool(activate_first and i == 0),
                            )

                            started.append({
                                "chat_id": new_chat_id,
                                "workflow_name": str(target_workflow),
                                "app_id": str(ent_id),
                                "user_id": str(usr_id),
                                "label": str(label) if label else None,
                            })

                            await websocket.send_json({
                                "type": "chat.workflow_started",
                                "data": {
                                    "chat_id": new_chat_id,
                                    "workflow_name": str(target_workflow),
                                    "app_id": str(ent_id),
                                    "user_id": str(usr_id),
                                    "label": str(label) if label else None,
                                },
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            })

                            if auto_run:
                                self._background_tasks[new_chat_id] = asyncio.create_task(
                                    self._run_workflow_background(
                                        chat_id=new_chat_id,
                                        workflow_name=str(target_workflow),
                                        app_id=str(ent_id),
                                        user_id=str(usr_id),
                                        ws_id=ws_id,
                                        initial_message=(
                                            str(initial_message)
                                            if isinstance(initial_message, str)
                                            and initial_message.strip()
                                            else None
                                        ),
                                        initial_agent_name_override=(
                                            str(initial_agent_name_override)
                                            if isinstance(initial_agent_name_override, str)
                                            and initial_agent_name_override.strip()
                                            else None
                                        ),
                                    )
                                )

                        await websocket.send_json({
                            "type": "chat.workflow_batch_started",
                            "data": {
                                "count": len(started),
                                "workflows": started,
                                "blocked": blocked,
                            },
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })
                    except Exception as be:
                        logger.error(f"❌ Failed to start workflow batch: {be}")
                        await websocket.send_json({
                            "type": "chat.error",
                            "data": {
                                "message": f"Workflow batch start failed: {str(be)}",
                                "error_code": "START_WORKFLOW_BATCH_FAILED",
                            },
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })
                    continue

                # ----------------------------------------------------------
                # client.resume
                # ----------------------------------------------------------
                if mtype == "client.resume":
                    try:
                        last_client_index = data.get("lastClientIndex")
                        if not isinstance(last_client_index, int):
                            raise ValueError("lastClientIndex must be int")
                        await self._connection_manager.handle_resume_request(
                            chat_id,
                            last_client_index,
                            websocket,
                            send_event_cb=self.send_event_to_ui,
                        )
                    except Exception as re:
                        logger.error(
                            f"❌ Failed to process client.resume for chat {chat_id}: {re}"
                        )
                        await websocket.send_json({
                            "type": "chat.error",
                            "data": {
                                "message": f"Resume failed: {str(re)}",
                                "error_code": "RESUME_FAILED",
                            },
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })
                    continue

                # Unknown control message -> ignore silently
        except Exception as e:
            logger.warning(f"WebSocket error for chat {chat_id}: {e}")
        finally:
            # H1-H2: Clean up connection resources
            await self._connection_manager.cleanup_connection(chat_id)
            logger.info(f"🔌 WebSocket disconnected for chat_id: {chat_id}")



