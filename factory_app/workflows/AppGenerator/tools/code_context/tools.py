"""
Code Context Workflow Tools

Three tools for AppGenerator/AgentGenerator workflows:
1. index_codebase: Index files (full or incremental)
2. get_code_context_for_agent: Retrieve formatted context for specific agent
3. get_code_diff: Compare versions and get detailed changes

All tools are multi-tenant safe (scoped by app_id) and compatible with AG2 tool registration.
"""

import logging
from typing import Dict, Any, List, Optional, Annotated

from .extractor import TreeSitterChunker, extract_codebase, TREE_SITTER_AVAILABLE
from .formatter import AgentContextFormatter
from .persistence import CodeContextPersistence

logger = logging.getLogger(__name__)

# Lazy-initialized globals (set by workflow runtime)
_db_client = None
_persistence = None
_chunker = None


def initialize_code_context_tools(db_client):
    """
    Initialize code context tools with database connection.
    Called once by the runtime during workflow setup.
    
    Args:
        db_client: MongoDB database client
    """
    global _db_client, _persistence, _chunker
    
    _db_client = db_client
    _persistence = CodeContextPersistence(db_client)
    _chunker = TreeSitterChunker()
    
    logger.info("Code context tools initialized")
    logger.info(f"Tree-sitter available: {TREE_SITTER_AVAILABLE}")


def index_codebase(
    app_id: Annotated[str, "Application ID (scopes all operations)"],
    workspace_id: Annotated[str, "Workspace/project identifier"],
    file_paths: Annotated[List[str], "List of file paths to index (relative or absolute)"],
    content_map: Annotated[Dict[str, str], "Map of file_path -> file content"],
    source_agent: Annotated[str, "Agent that produced these files (determines context_type)"] = "Unknown",
    mode: Annotated[str, "Indexing mode: 'auto' (recommended), 'full' (clean slate), or 'incremental' (merge)"] = "auto",
    ignore_patterns: Annotated[Optional[List[str]], "Glob patterns to exclude (e.g., '*.pyc', '__pycache__/*')"] = None,
    deleted_files: Annotated[Optional[List[str]], "Files to remove from index (only used in incremental mode)"] = None
) -> Dict[str, Any]:
    """
    Index codebase files and store extracted context.
    
    IMPORTANT: The source_agent parameter determines the context_type assigned to extracted code.
    This enables agent-specific context retrieval later via get_code_context_for_agent.
    
    Mode is typically 'auto' (recommended):
    - If workspace has no existing index: acts as 'full' (baseline)
    - If workspace has existing index: acts as 'incremental' (merge)
    
    Returns:
    {
        "success": bool,
        "version_hash": str,  # unique identifier for this version
        "indexed_files": int,
        "total_symbols": int,
        "tree_sitter_available": bool,  # whether full AST parsing was used
        "message": str
    }
    """
    if not _persistence:
        return {
            "success": False,
            "message": "Code context tools not initialized. Call initialize_code_context_tools first."
        }
    
    try:
        # Auto-detect mode if needed
        if mode == "auto":
            existing_hash = _persistence.get_version_hash(app_id, workspace_id)
            mode = "incremental" if existing_hash else "full"
            logger.debug(f"Auto-detected mode: {mode} (existing_hash={existing_hash})")
        
        # Extract contexts from provided files using TreeSitterChunker
        extracted = extract_codebase(file_paths, content_map, source_agent, ignore_patterns)
        
        if not extracted:
            return {
                "success": False,
                "message": "No files were successfully extracted. Check file_paths and content_map."
            }
        
        # Store contexts
        if mode == "incremental":
            version_hash = _persistence.update_incremental(
                app_id, workspace_id, extracted, deleted_files
            )
        else:
            version_hash = _persistence.store_context(
                app_id, workspace_id, extracted, mode="full"
            )
        
        total_symbols = sum(len(ctx.get("symbols", [])) + len(ctx.get("classes", [])) + len(ctx.get("functions", [])) 
                            for ctx in extracted.values())
        
        return {
            "success": True,
            "version_hash": version_hash,
            "indexed_files": len(extracted),
            "total_symbols": total_symbols,
            "tree_sitter_available": TREE_SITTER_AVAILABLE,
            "message": f"Successfully indexed {len(extracted)} files with {total_symbols} symbols"
        }
    
    except Exception as e:
        logger.error(f"index_codebase failed: {e}", exc_info=True)
        return {
            "success": False,
            "message": f"Indexing failed: {str(e)}"
        }


def get_code_context_for_agent(
    app_id: Annotated[str, "Application ID"],
    workspace_id: Annotated[str, "Workspace identifier"],
    agent_name: Annotated[str, "Agent requesting context (e.g., 'ServiceAgent', 'ControllerAgent', 'ModelAgent')"],
    version_hash: Annotated[Optional[str], "Optional: specific version (defaults to latest)"] = None,
    max_tokens: Annotated[Optional[int], "Optional: token budget override"] = None
) -> Dict[str, Any]:
    """
    Retrieve formatted code context tailored for a specific agent.
    
    This is the PREFERRED method for agent context retrieval. It uses the AgentContextFormatter
    which understands what each agent needs based on its role.
    
    Agent Requirements:
    - ServiceAgent: config_config, database_config, model_context
    - ControllerAgent: config_config, service_context  
    
    Returns:
    {
        "success": bool,
        "context": str,  # formatted context string ready for agent prompt
        "version_hash": str,
        "context_types_included": [str],  # which context types were included
        "message": str
    }
    """
    if not _persistence:
        return {
            "success": False,
            "context": "",
            "message": "Code context tools not initialized"
        }
    
    try:
        # Get required context types for this agent
        required_types = AgentContextFormatter.agent_requirements.get(agent_name, [])
        if not required_types:
            logger.warning(f"No context requirements defined for agent: {agent_name}")
            return {
                "success": False,
                "context": "",
                "message": f"No context requirements defined for agent: {agent_name}. "
                          f"Valid agents: {', '.join(AgentContextFormatter.agent_requirements.keys())}"
            }
        
        # Retrieve stored context
        contexts = _persistence.get_context(app_id, workspace_id, version_hash)
        
        if contexts is None:
            return {
                "success": False,
                "context": "",
                "message": f"No indexed context found for workspace '{workspace_id}'"
            }
        
        # Get version hash if not provided
        if not version_hash:
            version_hash = _persistence.get_version_hash(app_id, workspace_id)
        
        # Aggregate contexts by context_type
        aggregated = {}
        for file_path, ctx in contexts.items():
            ctx_type = ctx.get("context_type", "raw_context")
            if ctx_type in required_types:
                if ctx_type not in aggregated:
                    aggregated[ctx_type] = []
                aggregated[ctx_type].append(ctx)
        
        # Format using AgentContextFormatter
        formatter = AgentContextFormatter(aggregated)
        formatted = formatter.format_for_agent(agent_name)
        
        # Apply token limit if specified
        if max_tokens and len(formatted) > max_tokens * 4:  # rough char-to-token ratio
            formatted = formatted[:max_tokens * 4] + "\n\n... (truncated to fit token budget)"
        
        return {
            "success": True,
            "context": formatted,
            "version_hash": version_hash,
            "context_types_included": list(aggregated.keys()),
            "file_count": len(contexts),
            "message": f"Retrieved context for {agent_name} ({sum(len(v) for v in aggregated.values())} chunks from {len(aggregated)} context types)"
        }
    
    except Exception as e:
        logger.error(f"get_code_context_for_agent failed: {e}", exc_info=True)
        return {
            "success": False,
            "context": "",
            "message": f"Context retrieval failed: {str(e)}"
        }


def get_code_diff(
    app_id: Annotated[str, "Application ID"],
    workspace_id: Annotated[str, "Workspace identifier"],
    old_hash: Annotated[str, "Previous version hash (baseline)"],
    new_hash: Annotated[str, "New version hash (current)"],
    scope: Annotated[Optional[List[str]], "Optional: filter diff to specific paths"] = None,
    format_style: Annotated[str, "Diff format: 'summary' (files only) or 'detailed' (symbol-level changes)"] = "summary"
) -> Dict[str, Any]:
    """
    Compare two code versions and return changes.
    
    Useful for modification workflows where you need to:
    - Show agent what files changed
    - Identify added/removed symbols
    - Focus regeneration on modified areas only
    
    Returns:
    {
        "success": bool,
        "added_files": [file_paths],
        "removed_files": [file_paths],
        "modified_files": [file_paths],
        "unchanged_files": [file_paths],
        "detailed_changes": {  # only if format_style='detailed'
            "path/to/file.py": {
                "added_symbols": [...],
                "removed_symbols": [...],
                "modified_symbols": [...]
            }
        },
        "diff_summary": str,  # human-readable summary
        "message": str
    }
    """
    if not _persistence:
        return {
            "success": False,
            "message": "Code context tools not initialized"
        }
    
    try:
        # Compare versions
        diff = _persistence.compare_versions(app_id, workspace_id, old_hash, new_hash)
        
        # Apply scope filter if provided
        if scope:
            scope_set = set(scope)
            diff["added_files"] = [f for f in diff["added_files"] if f in scope_set]
            diff["removed_files"] = [f for f in diff["removed_files"] if f in scope_set]
            diff["modified_files"] = [f for f in diff["modified_files"] if f in scope_set]
            diff["unchanged_files"] = [f for f in diff["unchanged_files"] if f in scope_set]
            
            if "detailed_changes" in diff:
                diff["detailed_changes"] = {
                    k: v for k, v in diff["detailed_changes"].items() if k in scope_set
                }
        
        # Generate summary
        summary_lines = []
        summary_lines.append(f"Changes from {old_hash} to {new_hash}:")
        summary_lines.append(f"  Added: {len(diff['added_files'])} files")
        summary_lines.append(f"  Removed: {len(diff['removed_files'])} files")
        summary_lines.append(f"  Modified: {len(diff['modified_files'])} files")
        summary_lines.append(f"  Unchanged: {len(diff['unchanged_files'])} files")
        
        if format_style == "detailed" and diff.get("detailed_changes"):
            summary_lines.append("\nDetailed symbol changes:")
            for file_path, changes in diff["detailed_changes"].items():
                added_count = len(changes["added_symbols"])
                removed_count = len(changes["removed_symbols"])
                modified_count = len(changes["modified_symbols"])
                if added_count or removed_count or modified_count:
                    summary_lines.append(
                        f"  {file_path}: +{added_count} -{removed_count} ~{modified_count} symbols"
                    )
        
        diff["diff_summary"] = "\n".join(summary_lines)
        diff["success"] = True
        diff["message"] = "Diff computed successfully"
        
        # Remove detailed_changes if not requested
        if format_style != "detailed" and "detailed_changes" in diff:
            del diff["detailed_changes"]
        
        return diff
    
    except Exception as e:
        logger.error(f"get_code_diff failed: {e}", exc_info=True)
        return {
            "success": False,
            "message": f"Diff comparison failed: {str(e)}"
        }


# Tool registry for AG2 integration
CODE_CONTEXT_TOOLS = [
    index_codebase,
    get_code_context_for_agent,
    get_code_diff
]

# Export key classes for direct use
__all__ = [
    "initialize_code_context_tools",
    "index_codebase",
    "get_code_context_for_agent",
    "get_code_diff",
    "CODE_CONTEXT_TOOLS",
    "TreeSitterChunker",
    "AgentContextFormatter",
    "TREE_SITTER_AVAILABLE"
]
