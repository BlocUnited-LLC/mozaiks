# ==============================================================================
# FILE: core/workflow/session_manager.py
# DESCRIPTION: Manage workflow sessions and artifact instances for multi-workflow navigation
# ==============================================================================

import time
import uuid
from typing import Optional, Dict, Any
from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager


async def create_workflow_session(app_id: str, user_id: str, workflow_name: str) -> Dict[str, Any]:
    """
    Create a new WorkflowSession document and return it.
    
    Sessions start as IN_PROGRESS and stay that way until completed.
    Users can have multiple IN_PROGRESS sessions simultaneously.
    
    Args:
        app_id: App ID
        user_id: User ID
        workflow_name: Name of the workflow to start
        
    Returns:
        Dict containing the created session document with _id (chat_id), status=IN_PROGRESS, etc.
    """
    pm = AG2PersistenceManager()
    chat_id = f"chat_{uuid.uuid4().hex[:12]}"
    doc = {
        "_id": chat_id,
        "app_id": app_id,
        "user_id": user_id,
        "workflow_name": workflow_name,
        "status": "IN_PROGRESS",
        "artifact_instance_id": None,
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    coll = await pm._coll("WorkflowSessions")
    await coll.replace_one({"_id": chat_id}, doc, upsert=True)
    return doc


# Note: No pause/resume methods needed
# All sessions stay IN_PROGRESS until completed
# Users can have multiple IN_PROGRESS sessions simultaneously
# Resume is automatic when reconnecting to an existing chat_id


async def complete_workflow_session(chat_id: str, app_id: str) -> None:
    """
    Mark an existing WorkflowSession as COMPLETED.
    
    Args:
        chat_id: Chat/session ID to complete
        app_id: App ID for multi-tenant isolation
    """
    pm = AG2PersistenceManager()
    coll = await pm._coll("WorkflowSessions")
    await coll.update_one(
        {"_id": chat_id, "app_id": app_id},
        {"$set": {"status": "COMPLETED", "updated_at": time.time()}}
    )


async def create_artifact_instance(
    app_id: str,
    workflow_name: str,
    artifact_type: str,
    initial_state: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Create a persistent ArtifactInstance storing artifact state (JSON blob).
    
    Args:
        app_id: App ID
        workflow_name: Workflow name associated with this artifact
        artifact_type: Type of artifact (ActionPlan, FantasyApp, etc.)
        initial_state: Optional initial state dict
        
    Returns:
        Dict containing the created artifact document with _id, state, etc.
    """
    pm = AG2PersistenceManager()
    aid = f"artifact_{uuid.uuid4().hex[:12]}"
    doc = {
        "_id": aid,
        "app_id": app_id,
        "workflow_name": workflow_name,
        "artifact_type": artifact_type,
        "state": initial_state or {},
        "last_active_chat_id": None,
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    coll = await pm._coll("ArtifactInstances")
    await coll.replace_one({"_id": aid}, doc, upsert=True)
    return doc


async def attach_artifact_to_session(chat_id: str, artifact_id: str, app_id: str) -> None:
    """
    Attach an artifact instance to a workflow session and update last_active_chat_id.
    
    Args:
        chat_id: Chat/session ID
        artifact_id: Artifact instance ID
        app_id: App ID for multi-tenant isolation
    """
    pm = AG2PersistenceManager()
    sess_coll = await pm._coll("WorkflowSessions")
    art_coll = await pm._coll("ArtifactInstances")
    
    await sess_coll.update_one(
        {"_id": chat_id, "app_id": app_id},
        {"$set": {"artifact_instance_id": artifact_id, "updated_at": time.time()}}
    )
    await art_coll.update_one(
        {"_id": artifact_id, "app_id": app_id},
        {"$set": {"last_active_chat_id": chat_id, "updated_at": time.time()}}
    )


async def update_artifact_state(
    artifact_id: str,
    app_id: str,
    state_updates: Dict[str, Any]
) -> None:
    """
    Update artifact state with partial updates (merges into existing state).
    
    Args:
        artifact_id: Artifact instance ID
        app_id: App ID for multi-tenant isolation
        state_updates: Dict of state keys to update
    """
    pm = AG2PersistenceManager()
    coll = await pm._coll("ArtifactInstances")
    
    # Build update operations for nested state fields
    update_ops = {}
    for key, value in state_updates.items():
        update_ops[f"state.{key}"] = value
    
    update_ops["updated_at"] = time.time()
    
    await coll.update_one(
        {"_id": artifact_id, "app_id": app_id},
        {"$set": update_ops}
    )


async def get_artifact_instance(artifact_id: str, app_id: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve an artifact instance by ID.
    
    Args:
        artifact_id: Artifact instance ID
        app_id: App ID for multi-tenant isolation
        
    Returns:
        Artifact document or None if not found
    """
    pm = AG2PersistenceManager()
    coll = await pm._coll("ArtifactInstances")
    doc = await coll.find_one({"_id": artifact_id, "app_id": app_id})
    return doc


async def get_workflow_session(chat_id: str, app_id: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve a workflow session by chat_id.
    
    Args:
        chat_id: Chat/session ID
        app_id: App ID for multi-tenant isolation
        
    Returns:
        Session document or None if not found
    """
    pm = AG2PersistenceManager()
    coll = await pm._coll("WorkflowSessions")
    doc = await coll.find_one({"_id": chat_id, "app_id": app_id})
    return doc
