"""
Hook: Index Agent Code Output

This hook runs as an update_agent_state hook. It checks the chat history
for code_files from the previous agent and indexes them before the next
agent runs, ensuring downstream agents have up-to-date code context.

Hook type: update_agent_state
Signature: (agent, messages) -> None

The hook examines the last message in history, extracts any code_files,
and indexes them so the current agent (and future agents) can see them.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

from mozaiksai.core.workflow.generator_support.code_files import (
    extract_code_file_entries_from_payload,
)

logger = logging.getLogger(__name__)

# Agents that produce code_files output
CODE_PRODUCING_AGENTS = {
    "DatabaseAgent",
    "ConfigMiddlewareAgent", 
    "ModelAgent",
    "ServiceAgent",
    "ControllerAgent",
}

# Track what we've already indexed to avoid duplicate work
_indexed_hashes: set = set()


def _extract_code_files(message_content: str) -> Optional[List[Dict[str, str]]]:
    """
    Extract code_files from message content.
    
    Handles:
    - Raw JSON: {"code_files": [...]}
    - Markdown-wrapped: ```json\n{"code_files": [...]}\n```
    """
    if not message_content:
        return None
    
    content = message_content.strip()
    
    # Try to find JSON in markdown code block first
    json_match = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', content)
    if json_match:
        content = json_match.group(1)
    
    # Try to parse as JSON
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            extracted = extract_code_file_entries_from_payload(parsed)
            if extracted:
                return extracted
            code_files = parsed.get("content")
            if isinstance(code_files, list):
                return code_files
    except json.JSONDecodeError:
        pass
    
    # Try to find code_files array directly in text
    code_files_match = re.search(r'"code_files"\s*:\s*(\[[\s\S]*?\])', content)
    if code_files_match:
        try:
            code_files = json.loads(code_files_match.group(1))
            if isinstance(code_files, list):
                return code_files
        except json.JSONDecodeError:
            pass
    
    return None


def _compute_content_hash(code_files: List[Dict[str, str]]) -> str:
    """Compute a hash of code_files to avoid re-indexing."""
    import hashlib
    content = json.dumps(code_files, sort_keys=True)
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def index_prior_agent_output(agent, messages: List[Dict[str, Any]]) -> None:
    """
    Update agent state hook that indexes code_files from previous agent.
    
    This runs BEFORE each agent speaks. It looks at the last message in
    the chat history, and if it contains code_files from a code-producing
    agent, indexes them so the current agent has fresh context.
    
    Args:
        agent: The current agent about to speak
        messages: Chat history
    """
    global _indexed_hashes
    
    try:
        # Need at least one message to check
        if not messages:
            return
        
        # Get the last message
        last_msg = messages[-1] if messages else None
        if not isinstance(last_msg, dict):
            return
        
        # Check if it's from a code-producing agent
        source_agent = last_msg.get("name", "")
        if source_agent not in CODE_PRODUCING_AGENTS:
            return
        
        # Get content
        content = last_msg.get("content", "")
        if not content:
            return
        
        # Extract code_files
        code_files = _extract_code_files(content)
        if not code_files:
            return
        
        # Check if already indexed
        content_hash = _compute_content_hash(code_files)
        if content_hash in _indexed_hashes:
            logger.debug(f"[{source_agent}] Code files already indexed (hash: {content_hash})")
            return
        
        # Get context variables from agent
        context_variables = getattr(agent, "context_variables", {})
        app_id = context_variables.get("app_id")
        workspace_id = context_variables.get("workspace_id", app_id)
        
        if not app_id:
            logger.debug(f"[{agent.name}] No app_id in context, skipping indexing")
            return
        
        # Build content map
        content_map = {}
        file_paths = []
        for file_entry in code_files:
            if isinstance(file_entry, dict):
                filename = file_entry.get("filename", "")
                file_content = file_entry.get("content", "")
                if filename and file_content:
                    content_map[filename] = file_content
                    file_paths.append(filename)
        
        if not content_map:
            return
        
        # Import and call index_codebase
        try:
            from .code_context.tools import index_codebase
            
            result = index_codebase(
                app_id=app_id,
                workspace_id=workspace_id,
                file_paths=file_paths,
                content_map=content_map,
                source_agent=source_agent,
                mode="incremental"
            )
            
            if result.get("success"):
                _indexed_hashes.add(content_hash)
                logger.info(
                    f"[{source_agent}] → Indexed {result.get('indexed_files', 0)} files "
                    f"({result.get('total_symbols', 0)} symbols) before [{agent.name}]"
                )
            else:
                logger.warning(f"[{source_agent}] Indexing failed: {result.get('message')}")
                
        except ImportError as e:
            logger.warning(f"Could not import code_context tools: {e}")
        except Exception as e:
            logger.error(f"Error indexing code output: {e}", exc_info=True)
        
    except Exception as e:
        logger.error(f"index_prior_agent_output hook error: {e}", exc_info=True)


def reset_indexed_hashes() -> None:
    """Reset the indexed hashes tracker. Called at workflow start."""
    global _indexed_hashes
    _indexed_hashes = set()

