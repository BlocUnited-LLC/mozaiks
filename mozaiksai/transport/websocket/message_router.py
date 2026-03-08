# ==============================================================================
# FILE: mozaiksai/transport/websocket/message_router.py
# DESCRIPTION: Inbound message validation, routing, persistence, artifact actions
# ==============================================================================
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

try:  # pymongo optional in some test environments
    from pymongo import ReturnDocument  # type: ignore
except Exception:  # pragma: no cover
    class ReturnDocument:  # minimal fallback so attribute exists
        AFTER = 1

from logs.logging_config import get_core_logger

logger = get_core_logger("transport.message_router")


class MessageRouter:
    """Inbound message validation, routing, persistence, and artifact actions.

    Extracted from ``SimpleTransport`` (Phase 5).

    Responsibilities:
    - Validating inbound WebSocket message schemas.
    - Persisting free-form user messages to MongoDB.
    - Processing component actions (context variable updates).
    - Routing artifact actions (launch_workflow, update_state, etc.).
    - Validating pack prerequisites before workflow starts.

    Dependencies (injected via constructor):
    - ``connection_manager`` — read access to ``connections``, ``get_next_sequence``.
    - ``event_sender`` — for emitting events to the UI.
    """

    def __init__(
        self,
        *,
        connection_manager: Any,
        event_sender: Any,
    ) -> None:
        self._cm = connection_manager
        self._event_sender = event_sender
        self._persistence_manager: Any = None

    # ------------------------------------------------------------------
    # Lazy persistence manager
    # ------------------------------------------------------------------

    def _get_or_create_persistence_manager(self) -> Any:
        """Return cached AG2PersistenceManager instance (lazy import)."""
        if self._persistence_manager is None:
            from mozaiksai.runtime.data.persistence.persistence_manager import AG2PersistenceManager

            self._persistence_manager = AG2PersistenceManager()
        return self._persistence_manager

    # ------------------------------------------------------------------
    # Inbound message validation (H3)
    # ------------------------------------------------------------------

    def validate_inbound_message(self, message_data: dict) -> bool:
        """Validate inbound WebSocket message schema."""
        if not isinstance(message_data, dict):
            return False

        msg_type = message_data.get('type') or message_data.get('kind')
        if not msg_type or not isinstance(msg_type, str):
            return False

        # T1: Validate required fields based on message type
        if msg_type == "user.input.submit":
            base_ok = "chat_id" in message_data and "text" in message_data
            if not base_ok:
                return False
            return True

        elif msg_type == "ui_tool_response":
            return ("ui_tool_id" in message_data or "eventId" in message_data)

        elif msg_type == "client.resume":
            return (
                all(field in message_data for field in ["chat_id", "lastClientIndex"])
                and isinstance(message_data.get("lastClientIndex"), int)
            )

        elif msg_type in (
            "chat.enter_general_mode",
            "chat.start_general_chat",
            "chat.switch_workflow",
            "chat.start_workflow",
            "chat.start_workflow_batch",
        ):
            return True

        # Unknown message types are invalid
        return False

    # ------------------------------------------------------------------
    # User message persistence
    # ------------------------------------------------------------------

    async def process_incoming_user_message(
        self, *, chat_id: str, user_id: Optional[str], content: str, source: str = 'ws'
    ) -> None:
        """Persist and forward a free-form user message into the active workflow orchestration.

        Used by both WebSocket (user.input.submit without request_id) and HTTP input endpoint.
        Appends the message to persistence so that future resume operations have it, then
        emits the event to the UI.
        """
        if not content:
            return

        index: Optional[int] = None
        try:
            pm = self._get_or_create_persistence_manager()
            coll = await pm._coll()  # type: ignore[attr-defined]
            now_dt = datetime.now(timezone.utc)
            bump = await coll.find_one_and_update(
                {"_id": chat_id},
                {"$inc": {"last_sequence": 1}, "$set": {"last_updated_at": now_dt}},
                return_document=ReturnDocument.AFTER,
            )
            seq = int(bump.get('last_sequence', 1)) if bump else 1
            index = seq - 1  # zero-based index for UI
            msg_doc = {
                'role': 'user',
                'name': 'user',
                'content': content,
                'timestamp': now_dt,
                'event_type': 'message.created',
                'sequence': seq,
                'source': source,
            }
            await coll.update_one({"_id": chat_id}, {"$push": {"messages": msg_doc}})
        except Exception as e:
            # Persistence failure should not block UI emission
            logger.error(f"Failed to persist user message for {chat_id}: {e}")
            try:
                seq_fallback = self._cm.get_next_sequence(chat_id)
                index = max(0, seq_fallback - 1)
            except Exception:
                index = 0

        # Always emit event (best-effort) even if persistence failed
        try:
            await self._event_sender.send_event_to_ui(
                {'kind': 'text', 'agent': 'user', 'content': content, 'index': index},
                chat_id,
            )
        except Exception as emit_err:
            logger.error(f"Failed to emit user message event for {chat_id}: {emit_err}")

    # ------------------------------------------------------------------
    # Component action processing
    # ------------------------------------------------------------------

    async def process_component_action(
        self,
        *,
        chat_id: str,
        app_id: str,
        component_id: str,
        action_type: str,
        action_data: dict,
    ) -> Dict[str, Any]:
        """Apply a component action to context variables and emit acknowledgement.

        Returns a structured result indicating applied changes.
        """
        conn = self._cm.connections.get(chat_id)
        context = conn.context if conn else None
        applied: Dict[str, Any] = {}
        try:
            sets = action_data.get('set') if isinstance(action_data, dict) else None
            if context and isinstance(sets, dict):
                for k, v in sets.items():
                    try:
                        context.set(k, v)
                        applied[k] = v
                    except Exception as ce:
                        logger.debug(f"Context set failed for {k}: {ce}")

                # Persist a lightweight snapshot of changed keys
                try:
                    pm = self._get_or_create_persistence_manager()
                    coll = await pm._coll()  # type: ignore[attr-defined]
                    now = datetime.now(timezone.utc)
                    snapshot_doc = {
                        'role': 'system',
                        'name': 'context',
                        'content': {
                            'updated': applied,
                            'component_id': component_id,
                            'action_type': action_type,
                        },
                        'timestamp': now,
                        'event_type': 'context.updated',
                    }
                    await coll.update_one(
                        {"_id": chat_id, "app_id": app_id},
                        {"$push": {"messages": snapshot_doc}, "$set": {"last_updated_at": now}},
                    )
                except Exception as pe:
                    logger.debug(f"Context snapshot persistence failed: {pe}")

            # Emit acknowledgement event
            await self._event_sender.send_event_to_ui(
                {
                    'kind': 'component_action_ack',
                    'component_id': component_id,
                    'action_type': action_type,
                    'applied': applied,
                    'chat_id': chat_id,
                },
                chat_id,
            )
            return {'applied': applied, 'component_id': component_id, 'action_type': action_type}
        except Exception as e:
            logger.error(f"Component action processing failed for {chat_id}: {e}")
            raise

    # ------------------------------------------------------------------
    # Pack prerequisite validation
    # ------------------------------------------------------------------

    async def check_pack_prereqs(
        self,
        *,
        websocket: Any,
        chat_id: str,
        app_id: str,
        user_id: str,
        workflow_name: str,
    ) -> bool:
        """Validate pack prerequisites and notify the client if blocked.

        Returns True if the workflow is ALLOWED to proceed.
        Returns False if blocked (sends chat.prereq_blocked to the client).
        """
        from mozaiksai.kernel.pack.gating import validate_pack_prereqs

        pm = self._get_or_create_persistence_manager()
        ok, error_msg = await validate_pack_prereqs(
            app_id=str(app_id),
            user_id=str(user_id),
            workflow_name=str(workflow_name),
            persistence=pm,
        )
        if ok:
            return True

        logger.warning(
            "⚠️ Prerequisite validation failed for %s: %s", workflow_name, error_msg
        )
        await websocket.send_json(
            {
                "type": "chat.prereq_blocked",
                "data": {
                    "workflow_name": str(workflow_name),
                    "message": error_msg or "Prerequisites not met",
                    "error_code": "WORKFLOW_PREREQS_NOT_MET",
                },
                "chat_id": chat_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        return False

    # ------------------------------------------------------------------
    # Artifact action routing
    # ------------------------------------------------------------------

    async def handle_artifact_action(
        self, event: Dict[str, Any], chat_id: str, websocket: Any
    ) -> None:
        """Handle artifact action events from frontend (launch_workflow, update_state, etc.)."""
        from mozaiksai.runtime.sessions import session_manager

        data = event.get("data", {})
        action = data.get("action")
        payload = data.get("payload", {})
        artifact_id = data.get("artifact_id")

        conn_meta = self._cm.connections.get(chat_id)
        app_id = conn_meta.app_id if conn_meta else None
        user_id = conn_meta.user_id if conn_meta else None

        if not app_id or not user_id:
            logger.error(
                f"❌ Missing app_id or user_id for artifact action in chat {chat_id}"
            )
            return

        # Route: launch_workflow
        if action == "launch_workflow":
            target_workflow = payload.get("workflow_name")
            if not target_workflow:
                logger.warning("⚠️ Missing workflow_name in launch_workflow action")
                return

            logger.info(f"🚀 Launching workflow {target_workflow} from chat {chat_id}")

            if not await self.check_pack_prereqs(
                websocket=websocket,
                chat_id=chat_id,
                app_id=str(app_id),
                user_id=str(user_id),
                workflow_name=str(target_workflow),
            ):
                return

            new_session = await session_manager.create_workflow_session(
                app_id, user_id, target_workflow
            )
            artifact = await session_manager.create_artifact_instance(
                app_id,
                target_workflow,
                payload.get("artifact_type", "ActionPlan"),
            )
            await session_manager.attach_artifact_to_session(
                new_session["_id"], artifact["_id"], app_id
            )

            logger.info(
                f"✅ Created new session {new_session['_id']} with artifact {artifact['_id']}"
            )

            await websocket.send_json({
                "type": "chat.navigate",
                "data": {
                    "chat_id": new_session["_id"],
                    "workflow_name": target_workflow,
                    "artifact_instance_id": artifact["_id"],
                    "app_id": app_id,
                },
                "correlation_id": event.get("correlation_id"),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            return

        # Route: update_state
        if action == "update_state" and artifact_id:
            state_updates = payload.get("state_updates", {})
            if not state_updates:
                logger.warning("⚠️ Empty state_updates in update_state action")
                return

            await session_manager.update_artifact_state(
                artifact_id, app_id, state_updates
            )

            logger.info(
                f"✅ Updated artifact state for {artifact_id}: "
                f"{list(state_updates.keys())}"
            )

            await websocket.send_json({
                "type": "artifact.state.updated",
                "data": {
                    "artifact_id": artifact_id,
                    "state_delta": state_updates,
                },
                "chat_id": chat_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            return

        # Route: other actions (forward to agent or handle directly)
        logger.info(f"🔄 Artifact action {action} received for chat {chat_id}")
        await websocket.send_json({
            "type": "ack.artifact_action",
            "data": {
                "action": action,
                "status": "received",
            },
            "chat_id": chat_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
