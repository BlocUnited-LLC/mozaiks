# ==============================================================================
# FILE: session_registry.py
# DESCRIPTION: Tracks multiple workflow contexts per WebSocket connection
# ==============================================================================

"""
Session Registry for Multi-Workflow WebSocket Management

Enables users to:
- Start multiple workflows in a single WebSocket connection
- Pause current workflow and switch to another (manual via UI buttons)
- Resume paused workflows without losing state
- Enter general mode (non-AG2 capability, no workflow active)

Key Concepts:
- One WebSocket connection can host multiple workflow sessions
- Only ONE workflow is "active" at a time; others are "paused"
- Each workflow has its own chat_id, AG2 GroupChat instance, and artifact
- Session state persists in MongoDB; registry tracks runtime state only

Example Flow:
1. User starts Generator workflow → active
2. User clicks "Investor Portal" → Generator paused, Investor active
3. User enters general mode → All workflows paused, general mode active
4. User clicks "Generator" tab → Generator resumes from paused state
"""

from typing import Dict, Optional, List
from dataclasses import dataclass, field
from datetime import datetime, UTC
from logs.logging_config import get_workflow_logger

logger = get_workflow_logger("session_registry")


@dataclass
class WorkflowContext:
    """Runtime state for a workflow within a WebSocket session."""
    chat_id: str
    workflow_name: str
    app_id: str
    user_id: str
    artifact_id: Optional[str] = None
    status: str = "active"  # "active", "paused", "completed"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_active: datetime = field(default_factory=lambda: datetime.now(UTC))
    
    def to_dict(self) -> dict:
        """Serialize for JSON responses."""
        return {
            "chat_id": self.chat_id,
            "workflow_name": self.workflow_name,
            "app_id": self.app_id,
            "user_id": self.user_id,
            "artifact_id": self.artifact_id,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "last_active": self.last_active.isoformat()
        }


class SessionRegistry:
    """
    Manages workflow contexts across WebSocket connections.
    
    Responsibilities:
    - Track active/paused workflows per connection
    - Enforce "one active workflow per connection" rule
    - Provide context switching (pause current, activate target)
    - Support general mode (all workflows paused)
    
    NOT Responsible For:
    - Persisting workflow state (MongoDB handles that)
    - Starting/stopping AG2 GroupChat instances (orchestration layer)
    - LLM intent detection (UI buttons drive switching)
    """
    
    def __init__(self):
        # ws_id -> list of WorkflowContext
        self._workflows: Dict[str, List[WorkflowContext]] = {}
        
        # ws_id -> currently active chat_id (None = general mode)
        self._active_chat: Dict[str, Optional[str]] = {}
        
        logger.info("SessionRegistry initialized")
    
    def add_workflow(
        self, 
        ws_id: str, 
        chat_id: str, 
        workflow_name: str,
        app_id: str,
        user_id: str,
        artifact_id: Optional[str] = None,
        auto_activate: bool = True
    ) -> WorkflowContext:
        """
        Register a new workflow context in this WebSocket session.
        
        Args:
            ws_id: WebSocket connection ID
            chat_id: Unique chat/session ID for this workflow
            workflow_name: Workflow name (e.g., "Generator", "Investor")
            app_id: App identifier
            user_id: User identifier
            artifact_id: Optional artifact ID to display
            auto_activate: If True, pause all other workflows and activate this one
        
        Returns:
            Created WorkflowContext
        """
        if ws_id not in self._workflows:
            self._workflows[ws_id] = []
            self._active_chat[ws_id] = None
        
        # Check if workflow already exists (prevent duplicates)
        for wf in self._workflows[ws_id]:
            if wf.chat_id == chat_id:
                logger.warning(
                    f"Workflow {chat_id} already exists in session {ws_id}, updating instead"
                )
                wf.artifact_id = artifact_id
                wf.last_active = datetime.now(UTC)
                if auto_activate:
                    return self.switch_workflow(ws_id, chat_id)
                return wf
        
        context = WorkflowContext(
            chat_id=chat_id,
            workflow_name=workflow_name,
            app_id=app_id,
            user_id=user_id,
            artifact_id=artifact_id,
            status="paused"  # Start paused, then activate if requested
        )
        
        self._workflows[ws_id].append(context)
        
        logger.info(
            f"Added workflow context: {workflow_name} (chat_id={chat_id}) to session {ws_id}"
        )
        
        if auto_activate:
            return self.switch_workflow(ws_id, chat_id)
        
        return context
    
    def switch_workflow(self, ws_id: str, chat_id: str) -> Optional[WorkflowContext]:
        """
        Switch to a different workflow context (pause current, resume target).
        
        Args:
            ws_id: WebSocket connection ID
            chat_id: Target chat_id to activate
        
        Returns:
            Activated WorkflowContext or None if not found
        """
        if ws_id not in self._workflows:
            logger.warning(f"Session {ws_id} not found in registry")
            return None
        
        # Pause all workflows in this session
        for wf in self._workflows[ws_id]:
            if wf.status == "active":
                wf.status = "paused"
                logger.debug(f"Paused workflow {wf.chat_id}")
        
        # Activate target workflow
        for wf in self._workflows[ws_id]:
            if wf.chat_id == chat_id and wf.status != "completed":
                wf.status = "active"
                wf.last_active = datetime.now(UTC)
                self._active_chat[ws_id] = chat_id
                logger.info(f"Activated workflow {chat_id} in session {ws_id}")
                return wf
        
        logger.warning(f"Workflow {chat_id} not found or already completed in session {ws_id}")
        return None
    
    def enter_general_mode(self, ws_id: str):
        """
        Pause all workflows and enter general (non-AG2) mode.
        
        Args:
            ws_id: WebSocket connection ID
        """
        if ws_id not in self._workflows:
            return
        
        for wf in self._workflows[ws_id]:
            if wf.status == "active":
                wf.status = "paused"
        
        self._active_chat[ws_id] = None
        logger.info(f"Session {ws_id} entered general mode (all workflows paused)")
    
    def get_active_workflow(self, ws_id: str) -> Optional[WorkflowContext]:
        """
        Get currently active workflow for this WebSocket.
        
        Returns:
            Active WorkflowContext or None if in general mode
        """
        if ws_id not in self._workflows:
            return None
        
        for wf in self._workflows[ws_id]:
            if wf.status == "active":
                return wf
        
        return None
    
    def get_all_workflows(self, ws_id: str) -> List[WorkflowContext]:
        """
        Get all workflow contexts (active + paused + completed) for this session.
        
        Returns:
            List of WorkflowContext objects
        """
        return self._workflows.get(ws_id, [])
    
    def complete_workflow(self, ws_id: str, chat_id: str):
        """
        Mark a workflow as completed.
        
        Args:
            ws_id: WebSocket connection ID
            chat_id: Chat ID to mark as completed
        """
        if ws_id not in self._workflows:
            return
        
        for wf in self._workflows[ws_id]:
            if wf.chat_id == chat_id:
                wf.status = "completed"
                logger.info(f"Marked workflow {chat_id} as completed in session {ws_id}")
                
                # If this was the active workflow, clear active state
                if self._active_chat.get(ws_id) == chat_id:
                    self._active_chat[ws_id] = None
                
                break
    
    def remove_session(self, ws_id: str):
        """
        Clean up session registry when WebSocket disconnects.
        
        Args:
            ws_id: WebSocket connection ID
        """
        if ws_id in self._workflows:
            workflow_count = len(self._workflows[ws_id])
            del self._workflows[ws_id]
            del self._active_chat[ws_id]
            logger.info(f"Removed session {ws_id} ({workflow_count} workflows)")
    
    def get_workflow_by_chat_id(self, ws_id: str, chat_id: str) -> Optional[WorkflowContext]:
        """
        Get a specific workflow context by chat_id.
        
        Returns:
            WorkflowContext or None if not found
        """
        if ws_id not in self._workflows:
            return None
        
        for wf in self._workflows[ws_id]:
            if wf.chat_id == chat_id:
                return wf
        
        return None
    
    def is_in_general_mode(self, ws_id: str) -> bool:
        """
        Check if session is in general mode (no active workflow).
        
        Returns:
            True if in general mode, False otherwise
        """
        return self._active_chat.get(ws_id) is None


# Global singleton
session_registry = SessionRegistry()
