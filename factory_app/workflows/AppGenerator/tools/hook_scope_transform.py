"""
Hook: Apply Scope to File Paths

When generation_scope="feature", transforms output file paths to be
feature-scoped. For example:
  - backend/services/task_service.py → backend/features/projects/services/task_service.py
  - frontend/src/pages/Home.jsx → frontend/src/features/projects/pages/Home.jsx

Canonical app-module generation does not use backend/models.py or
backend/models/*.py. Typed generated module shapes belong in backend/schemas.py.

This hook runs as process_last_received_message to transform agent output
before it's stored.

Hook type: process_last_received_message
Signature: message(list|str) -> str
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

# Global context for scope settings (set by runtime via update_agent_state)
_scope_context: Dict[str, Any] = {}


def set_scope_context(context: Dict[str, Any]) -> None:
    """Set the scope context. Called by a setup hook."""
    global _scope_context
    _scope_context = context or {}


def _get_scope_settings() -> tuple:
    """Get current scope settings."""
    scope = _scope_context.get("generation_scope", "full-app")
    feature_name = _scope_context.get("feature_name")
    return scope, feature_name


def _transform_path(filepath: str, feature_name: str) -> str:
    """
    Transform a file path to be feature-scoped.
    
    Backend files:
      backend/services/user_service.py → backend/features/{feature}/services/user_service.py
      backend/controllers/user_controller.py → backend/features/{feature}/controllers/user_controller.py
    
    Frontend files:
      frontend/src/pages/Home.jsx → frontend/src/features/{feature}/pages/Home.jsx
      frontend/src/components/UserCard.jsx → frontend/src/features/{feature}/components/UserCard.jsx
    
    Shared files (NOT transformed):
      backend/config.py → backend/config.py (shared infrastructure)
      backend/database/database.py → backend/database/database.py
      frontend/src/config.js → frontend/src/config.js
    """
    # Normalize path
    path = filepath.replace("\\", "/")
    
    # Patterns that should be feature-scoped
    backend_patterns = [
        (r"^backend/services/", f"backend/features/{feature_name}/services/"),
        (r"^backend/controllers/", f"backend/features/{feature_name}/controllers/"),
        (r"^backend/routes/", f"backend/features/{feature_name}/routes/"),
    ]
    
    frontend_patterns = [
        (r"^frontend/src/pages/", f"frontend/src/features/{feature_name}/pages/"),
        (r"^frontend/src/components/", f"frontend/src/features/{feature_name}/components/"),
        (r"^src/pages/", f"src/features/{feature_name}/pages/"),
        (r"^src/components/", f"src/features/{feature_name}/components/"),
    ]
    
    all_patterns = backend_patterns + frontend_patterns
    
    for pattern, replacement in all_patterns:
        if re.match(pattern, path):
            return re.sub(pattern, replacement, path)
    
    return filepath


def _transform_code_files(code_files: List[Dict[str, str]], feature_name: str) -> List[Dict[str, str]]:
    """Transform all file paths in code_files list."""
    transformed = []
    for file_entry in code_files:
        if isinstance(file_entry, dict):
            new_entry = dict(file_entry)
            if "filename" in new_entry:
                new_entry["filename"] = _transform_path(new_entry["filename"], feature_name)
            transformed.append(new_entry)
        else:
            transformed.append(file_entry)
    return transformed


def apply_scope_to_output(message: Union[str, List[Dict[str, Any]]]) -> Union[str, List[Dict[str, Any]]]:
    """
    Process message and apply scope transformations to file paths.
    
    Only active when generation_scope="feature" and feature_name is set.
    """
    try:
        scope, feature_name = _get_scope_settings()
        
        # Only transform in feature scope
        if scope != "feature" or not feature_name:
            return message
        
        # Get content to transform
        if isinstance(message, str):
            content = message
        elif isinstance(message, list) and message:
            last_msg = message[-1]
            if isinstance(last_msg, dict):
                content = last_msg.get("content", "")
            else:
                return message
        else:
            return message
        
        if not content:
            return message
        
        # Try to parse as JSON with code_files
        try:
            # Handle markdown-wrapped JSON
            json_match = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', content)
            json_str = json_match.group(1) if json_match else content
            
            parsed = json.loads(json_str)
            if isinstance(parsed, dict):
                code_files = parsed.get("code_files") or parsed.get("content")
                if isinstance(code_files, list):
                    # Transform paths
                    transformed = _transform_code_files(code_files, feature_name)
                    parsed["code_files"] = transformed
                    
                    # Reconstruct message
                    new_json = json.dumps(parsed, indent=2)
                    
                    if isinstance(message, str):
                        return new_json
                    elif isinstance(message, list):
                        new_message = list(message)
                        new_message[-1] = dict(new_message[-1])
                        new_message[-1]["content"] = new_json
                        return new_message
        except json.JSONDecodeError:
            pass
        
    except Exception as e:
        logger.error(f"apply_scope_to_output error: {e}", exc_info=True)
    
    return message


def inject_scope_context(agent, messages: List[Dict[str, Any]]) -> None:
    """
    Update agent state hook to set scope context from context_variables.
    Must run before apply_scope_to_output can work.
    """
    try:
        context_variables = getattr(agent, "context_variables", {})
        set_scope_context({
            "generation_scope": context_variables.get("generation_scope", "full-app"),
            "feature_name": context_variables.get("feature_name"),
        })
    except Exception as e:
        logger.error(f"inject_scope_context error: {e}", exc_info=True)
