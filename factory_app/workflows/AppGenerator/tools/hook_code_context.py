import logging
from typing import Dict, Any, List, Optional
from .code_context.tools import get_code_context_for_agent

logger = logging.getLogger(__name__)

def inject_code_context(agent, messages: List[Dict[str, Any]]) -> None:
    """
    Update agent state hook to inject code context.
    
    Retrieves relevant code context for the agent using the code context management system
    and appends it to the system message.
    
    This allows agents to always have the latest view of the codebase parts relevant
    to their role (e.g., ServiceAgent sees service files, ControllerAgent sees controllers).
    """
    try:
        # 1. Resolve Context Variables
        # AG2 agents usually have context_variables injected
        context_variables = getattr(agent, "context_variables", {})
        
        app_id = context_variables.get("app_id")
        workspace_id = context_variables.get("workspace_id", app_id) # Default to app_id if workspace_id missing
        
        if not app_id:
            # Can't retrieve context without app_id
            # This might happen during initialization or testing
            return

        # 2. Retrieve Context
        # We pass context_variables just in case, but mainly rely on explicit IDs
        context_str = get_code_context_for_agent(
            agent_name=agent.name,
            app_id=app_id,
            workspace_id=workspace_id,
            context_variables=context_variables
        )
        
        if not context_str:
            # No context found or needed for this agent
            return

        # 3. Inject into System Message
        # We use a distinct section header to manage updates
        header = "\n\n[CODE CONTEXT]"
        current_system_message = agent.system_message
        
        if "[CODE CONTEXT]" in current_system_message:
            # Update existing section
            # We assume [CODE CONTEXT] is the last section we manage, or we split by it
            parts = current_system_message.split("[CODE CONTEXT]")
            base_message = parts[0].strip()
            # We discard the old context part
            new_system_message = f"{base_message}{header}\n{context_str}"
        else:
            # Append new section
            new_system_message = f"{current_system_message}{header}\n{context_str}"
            
        # Only update if changed to avoid unnecessary log noise or processing
        if new_system_message != current_system_message:
            agent.update_system_message(new_system_message)
            logger.info(f"[{agent.name}] Injected code context ({len(context_str)} chars)")

    except Exception as e:
        logger.error(f"[{agent.name}] Failed to inject code context: {e}")
