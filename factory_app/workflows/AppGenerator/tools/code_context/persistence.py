"""
Code Context Persistence

Stores extracted code context in MongoDB keyed by (app_id, workspace_id, version_hash).
Supports incremental updates, version comparison for diff detection, and 
context_type filtering for agent-specific retrieval.

Enhanced Features:
- context_type field per file (model_context, service_context, etc.)
- Agent-based filtering: retrieve only context types an agent needs
- Relationship data persistence for models (field relationships, cardinality)
"""

import logging
from typing import Dict, Any, List, Optional, Set
from datetime import datetime
import hashlib

logger = logging.getLogger(__name__)

# Valid context types that can be stored
VALID_CONTEXT_TYPES = {
    "config_config",
    "database_config", 
    "middleware_config",
    "model_context",
    "service_context",
    "controller_context",
    "route_context",
    "frontend_config_context",
    "utilities_api_context",
    "utilities_styles_context",
    "component_context",
    "pages_context",
    "unknown"
}


class CodeContextPersistence:
    """
    Manages persistence of extracted code contexts.
    
    Schema:
    {
        "app_id": str,
        "workspace_id": str,  # identifies the project/repo being generated
        "version_hash": str,  # hash of all file hashes (version identifier)
        "indexed_at": datetime,
        "source_agent": str,  # which agent triggered the indexing
        "files": {
            "path/to/file.py": {
                "hash": "abc123...",
                "language": "python",
                "context_type": "model_context|service_context|...",
                "imports": [...],
                "classes": [...],
                "functions": [...],
                "relationships": [...],  # for models: field relationships
                ...
            }
        },
        "context_type_index": {
            "model_context": ["path/to/models/user.py", ...],
            "service_context": ["path/to/services/user_service.py", ...],
            ...
        },
        "metadata": {
            "mode": "full|incremental",
            "file_count": int,
            "total_classes": int,
            "total_functions": int,
            "context_type_counts": {"model_context": 5, ...}
        }
    }
    """
    
    def __init__(self, db_client):
        """
        Args:
            db_client: MongoDB database client
        """
        self.db = db_client
        self.collection = self.db["CodeContext"]
        
        # Create indexes for efficient queries
        self.collection.create_index([("app_id", 1), ("workspace_id", 1), ("version_hash", 1)])
        self.collection.create_index([("app_id", 1), ("workspace_id", 1), ("indexed_at", -1)])
        # Index for context_type queries
        self.collection.create_index([("app_id", 1), ("workspace_id", 1), ("context_type_index", 1)])
    
    def store_context(
        self,
        app_id: str,
        workspace_id: str,
        extracted_contexts: Dict[str, Dict[str, Any]],
        mode: str = "full",
        source_agent: Optional[str] = None
    ) -> str:
        """
        Store extracted contexts.
        
        Args:
            app_id: Application ID
            workspace_id: Workspace/project identifier
            extracted_contexts: Map of file_path -> extracted context
            mode: "full" or "incremental"
            source_agent: Which agent triggered the indexing (optional)
        
        Returns:
            version_hash: Unique identifier for this version
        """
        # Compute version hash from all file hashes
        file_hashes = sorted([ctx["hash"] for ctx in extracted_contexts.values()])
        version_hash = hashlib.sha256("".join(file_hashes).encode()).hexdigest()[:16]
        
        # Build context_type index for fast agent-based queries
        context_type_index: Dict[str, List[str]] = {}
        context_type_counts: Dict[str, int] = {}
        total_classes = 0
        total_functions = 0
        
        for file_path, ctx in extracted_contexts.items():
            # Get context_type (default to "unknown" if not present)
            ctx_type = ctx.get("context_type", "unknown")
            
            # Build index: context_type -> list of file paths
            if ctx_type not in context_type_index:
                context_type_index[ctx_type] = []
            context_type_index[ctx_type].append(file_path)
            
            # Count context types
            context_type_counts[ctx_type] = context_type_counts.get(ctx_type, 0) + 1
            
            # Count classes and functions
            total_classes += len(ctx.get("classes", []))
            total_functions += len(ctx.get("functions", []))
        
        document = {
            "app_id": app_id,
            "workspace_id": workspace_id,
            "version_hash": version_hash,
            "indexed_at": datetime.utcnow(),
            "source_agent": source_agent,
            "files": extracted_contexts,
            "context_type_index": context_type_index,
            "metadata": {
                "mode": mode,
                "file_count": len(extracted_contexts),
                "total_classes": total_classes,
                "total_functions": total_functions,
                "context_type_counts": context_type_counts
            }
        }
        
        # Upsert (replace if same version exists)
        self.collection.replace_one(
            {
                "app_id": app_id,
                "workspace_id": workspace_id,
                "version_hash": version_hash
            },
            document,
            upsert=True
        )
        
        logger.info(
            f"Stored code context: app={app_id}, workspace={workspace_id}, "
            f"version={version_hash}, files={len(extracted_contexts)}, "
            f"classes={total_classes}, functions={total_functions}, "
            f"context_types={list(context_type_counts.keys())}"
        )
        
        return version_hash
    
    def get_context(
        self,
        app_id: str,
        workspace_id: str,
        version_hash: Optional[str] = None
    ) -> Optional[Dict[str, Dict[str, Any]]]:
        """
        Retrieve stored contexts.
        
        Args:
            app_id: Application ID
            workspace_id: Workspace identifier
            version_hash: Specific version (if None, returns latest)
        
        Returns:
            Map of file_path -> extracted context, or None if not found
        """
        query = {"app_id": app_id, "workspace_id": workspace_id}
        
        if version_hash:
            query["version_hash"] = version_hash
            document = self.collection.find_one(query)
        else:
            # Get latest version
            document = self.collection.find_one(
                query,
                sort=[("indexed_at", -1)]
            )
        
        if not document:
            return None
        
        return document.get("files", {})

    def get_context_by_type(
        self,
        app_id: str,
        workspace_id: str,
        context_types: List[str],
        version_hash: Optional[str] = None
    ) -> Dict[str, Dict[str, Any]]:
        """
        Retrieve context for specific context types (e.g., only model_context).
        Uses the context_type_index for efficient retrieval.
        
        Args:
            app_id: Application ID
            workspace_id: Workspace identifier
            context_types: List of context types to retrieve
            version_hash: Specific version (optional)
            
        Returns:
            Map of file_path -> extracted context
        """
        query = {"app_id": app_id, "workspace_id": workspace_id}
        
        if version_hash:
            query["version_hash"] = version_hash
            document = self.collection.find_one(query)
        else:
            # Get latest version
            document = self.collection.find_one(
                query,
                sort=[("indexed_at", -1)]
            )
            
        if not document:
            return {}
            
        files = document.get("files", {})
        context_type_index = document.get("context_type_index", {})
        
        result = {}
        for ctx_type in context_types:
            file_paths = context_type_index.get(ctx_type, [])
            for path in file_paths:
                if path in files:
                    result[path] = files[path]
                    
        return result
    
    def get_version_hash(
        self,
        app_id: str,
        workspace_id: str
    ) -> Optional[str]:
        """Get the latest version hash for a workspace."""
        document = self.collection.find_one(
            {"app_id": app_id, "workspace_id": workspace_id},
            sort=[("indexed_at", -1)],
            projection={"version_hash": 1}
        )
        return document["version_hash"] if document else None
    
    def compare_versions(
        self,
        app_id: str,
        workspace_id: str,
        old_hash: str,
        new_hash: str
    ) -> Dict[str, Any]:
        """
        Compare two versions and return diff information.
        
        Returns:
        {
            "added_files": [file_paths],
            "removed_files": [file_paths],
            "modified_files": [file_paths],
            "unchanged_files": [file_paths],
            "detailed_changes": {
                "path/to/file.py": {
                    "added_classes": [...],
                    "removed_classes": [...],
                    "modified_classes": [...],
                    "added_functions": [...],
                    ...
                }
            }
        }
        """
        old_context = self.get_context(app_id, workspace_id, old_hash)
        new_context = self.get_context(app_id, workspace_id, new_hash)
        
        if not old_context or not new_context:
            logger.warning(f"Cannot compare: missing version (old={bool(old_context)}, new={bool(new_context)})")
            return {
                "added_files": list(new_context.keys()) if new_context else [],
                "removed_files": list(old_context.keys()) if old_context else [],
                "modified_files": [],
                "unchanged_files": [],
                "detailed_changes": {}
            }
        
        old_files = set(old_context.keys())
        new_files = set(new_context.keys())
        
        added_files = list(new_files - old_files)
        removed_files = list(old_files - new_files)
        common_files = old_files & new_files
        
        modified_files = []
        unchanged_files = []
        detailed_changes = {}
        
        for file_path in common_files:
            old_file = old_context[file_path]
            new_file = new_context[file_path]
            
            # Compare by hash
            if old_file["hash"] != new_file["hash"]:
                modified_files.append(file_path)
                
                # Detailed symbol-level diff
                changes = {}
                
                # Compare classes
                old_classes = {c["name"]: c for c in old_file.get("classes", [])}
                new_classes = {c["name"]: c for c in new_file.get("classes", [])}
                changes["added_classes"] = [c for name, c in new_classes.items() if name not in old_classes]
                changes["removed_classes"] = [c for name, c in old_classes.items() if name not in new_classes]
                changes["modified_classes"] = [c for name, c in new_classes.items() if name in old_classes and c != old_classes[name]]
                
                # Compare functions
                old_funcs = {f["name"]: f for f in old_file.get("functions", [])}
                new_funcs = {f["name"]: f for f in new_file.get("functions", [])}
                changes["added_functions"] = [f for name, f in new_funcs.items() if name not in old_funcs]
                changes["removed_functions"] = [f for name, f in old_funcs.items() if name not in new_funcs]
                changes["modified_functions"] = [f for name, f in new_funcs.items() if name in old_funcs and f != old_funcs[name]]
                
                detailed_changes[file_path] = changes
            else:
                unchanged_files.append(file_path)
        
        return {
            "added_files": sorted(added_files),
            "removed_files": sorted(removed_files),
            "modified_files": sorted(modified_files),
            "unchanged_files": sorted(unchanged_files),
            "detailed_changes": detailed_changes
        }
    
    def update_incremental(
        self,
        app_id: str,
        workspace_id: str,
        new_or_modified_contexts: Dict[str, Dict[str, Any]],
        deleted_files: Optional[List[str]] = None
    ) -> str:
        """
        Incrementally update stored context.
        
        Fetches latest version, merges changes, and stores as new version.
        
        Args:
            app_id: Application ID
            workspace_id: Workspace identifier
            new_or_modified_contexts: Map of file_path -> extracted context
            deleted_files: List of file paths to remove
        
        Returns:
            new_version_hash: Version hash after update
        """
        # Get latest context
        latest = self.get_context(app_id, workspace_id)
        if latest is None:
            # No existing context, treat as full index
            return self.store_context(app_id, workspace_id, new_or_modified_contexts, mode="full")
        
        # Merge: update existing files, keep unchanged files
        merged = dict(latest)  # Copy existing
        merged.update(new_or_modified_contexts)  # Apply updates
        
        # Remove deleted files
        if deleted_files:
            for file_path in deleted_files:
                merged.pop(file_path, None)
        
        # Store as new version
        return self.store_context(app_id, workspace_id, merged, mode="incremental")
    
    def clear_workspace(
        self,
        app_id: str,
        workspace_id: str
    ) -> int:
        """
        Clear all indexed versions for a workspace.
        
        Returns:
            Number of deleted documents
        """
        result = self.collection.delete_many({
            "app_id": app_id,
            "workspace_id": workspace_id
        })
        logger.info(f"Cleared workspace: app={app_id}, workspace={workspace_id}, deleted={result.deleted_count}")
        return result.deleted_count
    
    def list_versions(
        self,
        app_id: str,
        workspace_id: str,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        List recent versions for a workspace.
        
        Returns list of:
        {
            "version_hash": str,
            "indexed_at": datetime,
            "file_count": int,
            "total_symbols": int
        }
        """
        cursor = self.collection.find(
            {"app_id": app_id, "workspace_id": workspace_id},
            projection={"version_hash": 1, "indexed_at": 1, "metadata": 1},
            sort=[("indexed_at", -1)],
            limit=limit
        )
        
        versions = []
        for doc in cursor:
            versions.append({
                "version_hash": doc["version_hash"],
                "indexed_at": doc["indexed_at"],
                "file_count": doc.get("metadata", {}).get("file_count", 0),
                "total_symbols": doc.get("metadata", {}).get("total_symbols", 0)
            })
        
        return versions
