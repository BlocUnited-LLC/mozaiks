"""
AG2 Termination Handler with Status Management
Automatically updates workflow status from 0 → 1 when AG2 conversations terminate
Based on TerminateTarget patterns logic (0 = resumable, 1 = completed)
"""
import asyncio
from time import perf_counter
from datetime import datetime, UTC
from typing import Optional, Dict, Any, Callable, TYPE_CHECKING
from dataclasses import dataclass

from logs.logging_config import get_workflow_logger
from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
from mozaiksai.core.events import get_event_dispatcher
# Avoid circular import: only import for typing
if TYPE_CHECKING:
    from mozaiksai.core.transport.simple_transport import SimpleTransport

wf_logger = get_workflow_logger("termination_handler")

@dataclass
class TerminationResult:
    """Result of conversation termination processing (numeric status: 0 in-progress, 1 completed)."""
    terminated: bool
    status: int  # 0 | 1
    workflow_complete: bool
    session_summary: Optional[Dict[str, Any]] = None

class AG2TerminationHandler:
    """
    Handles AG2 conversation termination and integrates with workflow status management.
    
    When AG2 conversations end (via TerminateTarget or max_turns), this handler:
    1. Detects the termination event
    2. Updates status from 'in_progress' to 'completed'
    3. Finalizes conversation in persistence manager
    4. Triggers workflow completion analytics
    5. Cleans up session state appropriately
    
    Status Pattern:
    - 'in_progress': Chat initiated/in progress (resumable)
    - 'completed'  : Chat ended/completed (not resumable)
    
    Integration Points:
    - AG2 GroupChat termination callbacks
    - PersistenceManager status updates
    - Workflow completion notifications
    """
    
    def __init__(self,
                 chat_id: str,
                 app_id: str,
                 workflow_name: str = "default",
                 persistence_manager: Optional[AG2PersistenceManager] = None,
                 transport: Optional['SimpleTransport'] = None):
        self.chat_id = chat_id
        self.app_id = app_id
        self.workflow_name = workflow_name
        self.persistence_manager = persistence_manager or AG2PersistenceManager()
        self.transport = transport

        # Termination detection state
        self.conversation_active = False
        self.termination_callbacks = []  # type: ignore[list-annotated]
        self.start_time = None
        self._ended: bool = False
        self._last_result: Optional[TerminationResult] = None
        self._end_lock = asyncio.Lock()

        wf_logger.info(f"🔄 Termination handler initialized for {self.workflow_name} workflow")
    
    def add_termination_callback(self, callback: Callable[[TerminationResult], None]):
        """Add callback to be triggered when conversation terminates"""
        self.termination_callbacks.append(callback)
        wf_logger.debug(f"📋 Added termination callback: {callback.__name__}")
    
    async def on_conversation_start(self, user_id: str):
        """Called when AG2 conversation begins"""
        self.conversation_active = True
        self.start_time = perf_counter()

        # Create the chat session document in the database
        await self.persistence_manager.create_chat_session(
            chat_id=self.chat_id,
            app_id=self.app_id,
            workflow_name=self.workflow_name,
            user_id=user_id
        )

        # Emit business event via unified dispatcher
        try:
            dispatcher = get_event_dispatcher()
            await dispatcher.emit_business_event(
                log_event_type="CONVERSATION_STARTED",
                description=f"AG2 conversation started for {self.workflow_name}",
                context={
                    "chat_id": self.chat_id,
                    "app_id": self.app_id,
                    "workflow_name": self.workflow_name,
                },
            )
        except Exception:
            pass

        wf_logger.info(f"🚀 AG2 conversation started for {self.workflow_name}")
    
    async def on_conversation_end(self,
                                  *,
                                  max_turns_reached: bool = False) -> TerminationResult:
        """
        Called when AG2 conversation ends (TerminateTarget triggered or max_turns reached)
        
        Args:
            max_turns_reached: Whether conversation ended due to max turns limit
        
        Returns:
            TerminationResult with completion details
        """
        async with self._end_lock:
            if self._ended and self._last_result:
                # Idempotent: return prior result without re-writing DB/events
                return self._last_result
                
            if not self.conversation_active and not self._ended:
                wf_logger.warning("⚠️ Termination handler called but conversation not active")
                return TerminationResult(
                    terminated=False,
                    status=0,
                    workflow_complete=False
                )

            # Mark inactive and compute duration
            self.conversation_active = False
            conversation_duration = perf_counter() - self.start_time if self.start_time else 0

            try:
                    # Mark the chat as completed in the database (reason removed)
                    status_updated = await self.persistence_manager.mark_chat_completed(
                        self.chat_id, self.app_id
                    )

                    if not status_updated:
                        wf_logger.error("❌ Failed to update workflow status to completed")

                    # Note: run_complete event is sent by AG2's RunCompletionEvent
                    # The orchestration layer handles forwarding it to the UI
                    # We only manage database status updates here
                    wf_logger.info(f"✅ Workflow status updated to completed for chat {self.chat_id}")

                    # Create termination result
                    session_summary = {
                        "duration_sec": conversation_duration,
                        "max_turns_reached": max_turns_reached,
                        "completed": True,
                    }
                    result = TerminationResult(
                        terminated=True,
                        status=1,
                        workflow_complete=True,
                        session_summary=session_summary
                    )

                    # Emit business event via unified dispatcher
                    try:
                        dispatcher = get_event_dispatcher()
                        await dispatcher.emit_business_event(
                            log_event_type="CONVERSATION_TERMINATED",
                            description="AG2 conversation terminated",
                            context={
                                "chat_id": self.chat_id,
                                "app_id": self.app_id,
                                "workflow_name": self.workflow_name,
                                "status": result.status,
                                "duration_ms": conversation_duration * 1000,
                                "max_turns_reached": max_turns_reached,
                                "workflow_complete": result.workflow_complete,
                            },
                        )
                    except Exception:
                        pass

                    # Trigger termination callbacks
                    for callback in self.termination_callbacks:
                        try:
                            callback(result)
                        except Exception as e:
                            wf_logger.error(f"❌ Termination callback failed: {e}")

                    wf_logger.info(f"✅ AG2 conversation terminated successfully (status={result.status})")
                    # Mark ended & cache result (outside lock minimal risk; attributes immutable)
                    self._ended = True
                    self._last_result = result
                    return result

            except Exception as e:
                wf_logger.error(f"❌ Failed to handle conversation termination: {e}")

                # Return failure result
                failure_result = TerminationResult(
                    terminated=False,
                    status=1,  # Conservatively mark completed; DB was updated above
                    workflow_complete=False
                )
                self._ended = True
                self._last_result = failure_result
                return failure_result
    
    async def detect_terminate_target(self, conversation_messages) -> bool:
        """
        Detect if TerminateTarget was triggered based on conversation content
        
        This analyzes the last few messages to detect termination patterns that
        would trigger AG2's TerminateTarget in the handoffs configuration.
        """
        if not conversation_messages:
            return False
        
        # Get the last few messages to analyze
        recent_messages = conversation_messages[-3:] if len(conversation_messages) >= 3 else conversation_messages
        
        termination_indicators = [
            "looks good", "approve", "finished", "done", "thank you",
            "approved", "satisfied", "complete", "end conversation",
            "terminate", "workflow approved", "all set"
        ]
        
        for message in recent_messages:
            content = message.get("content", "").lower()
            for indicator in termination_indicators:
                if indicator in content:
                    wf_logger.info(f"🎯 TerminateTarget pattern detected: '{indicator}' in message")
                    return True
        
        return False
    
    async def check_completion_status(self) -> Dict[str, Any]:
        """Return current chat session completion status.

        Uses the lean persistence manager's internal collection accessor
        A session is considered complete when status == 'completed'.
        We query by the canonical _id (chat_id) + app_id to match create / completion writes.
        """
        try:
            coll = await self.persistence_manager._coll()
            # New schema stores the session id as _id; chat_id maintained for compatibility.
            session = await coll.find_one({"_id": self.chat_id, "app_id": self.app_id})

            if not session:
                return {
                    "status": -1,
                    "reason": "not_found",
                    "conversation_active": self.conversation_active,
                    "workflow_name": self.workflow_name,
                }

            status = int(session.get("status", -1))
            return {
                "status": status,
                "conversation_active": self.conversation_active,
                "workflow_name": self.workflow_name,
            }
        except Exception as e:
            wf_logger.error(f"❌ Failed to check completion status: {e}")
            return {
                "status": -1,
                "reason": "error",
                "conversation_active": self.conversation_active,
                "workflow_name": self.workflow_name,
                "error": str(e),
            }

def create_termination_handler(chat_id: str, 
                             app_id: str, 
                             workflow_name: str = "default",
                             transport: Optional['SimpleTransport'] = None) -> AG2TerminationHandler:
    """
    Factory function to create configured termination handler
    ```
    """
    return AG2TerminationHandler(
        chat_id=chat_id,
        app_id=app_id,
        workflow_name=workflow_name,
        transport=transport
    )

