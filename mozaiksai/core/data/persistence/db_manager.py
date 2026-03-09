# ==============================================================================
# FILE: core/workflow/db_manager.py
# DESCRIPTION: Agent-friendly database operations manager 
# PURPOSE: Provides simple CRUD operations for agents via json configuration
# ==============================================================================

from __future__ import annotations
from typing import Dict, Any, Optional, Annotated
from datetime import datetime, timezone
from bson import ObjectId

from logs.logging_config import get_workflow_logger
from mozaiksai.core.core_config import get_mongo_client
from mozaiksai.core.workflow.workflow_manager import workflow_manager

class DatabaseManagerError(Exception):
    """Custom exception for database manager errors."""
    pass

async def save_to_database(
    data: Annotated[Dict[str, Any], "The data to save to the database"],
    database_name: Annotated[Optional[str], "Database name (defaults from workflow config)"] = None,
    collection_name: Annotated[Optional[str], "Collection name (defaults from workflow config)"] = None,
    **runtime: Any,
) -> Dict[str, Any]:
    """AGENT CONTRACT: Save data to MongoDB database.

    Primary Objective:
        Provide agents an easy way to save data to MongoDB using workflow configuration
        or explicit parameters. Handles app_id context automatically.

    EXECUTION STEPS:
        1. Resolve database/collection from workflow config or parameters
        2. Add metadata (app_id, created_at, updated_at) automatically
        3. Insert document and return confirmation with MongoDB _id

    Args:
        data: Dictionary of data to save
        database_name: Override database name (optional)
        collection_name: Override collection name (optional)
        **runtime: Contains chat_id, app_id, workflow_name, context_variables

    Returns:
        Dict containing status, inserted_id, and metadata

    Example Usage in Agent Tool:
        result = await save_to_database({
            "api_key_service": "openai",
            "key_length": 47,
            "requested_at": "2024-01-15T10:30:00Z"
        }, collection_name="api_keys")
    """
    chat_id = runtime.get("chat_id")
    app_id = runtime.get("app_id")
    workflow_name = runtime.get("workflow_name") or runtime.get("workflow")
    context_variables = runtime.get("context_variables")

    logger = get_workflow_logger(workflow_name=(workflow_name or "unknown"), chat_id=chat_id, app_id=app_id)
    
    # Resolve configuration
    try:
        config = _get_database_config(workflow_name, database_name, collection_name)
        db_name = config["database_name"]
        coll_name = config["collection_name"]
    except Exception as e:
        return {"status": "error", "message": f"Configuration error: {e}"}

    # Prepare document with metadata
    document = dict(data)  # Copy to avoid mutation
    now = datetime.now(timezone.utc)
    
    # Add app context
    if app_id:
        try:
            document["app_id"] = ObjectId(app_id)
        except:
            document["app_id"] = app_id
    
    # Add timestamps
    document["created_at"] = now
    document["updated_at"] = now
    
    # Add chat context if available
    if chat_id:
        document["chat_id"] = chat_id
    if workflow_name:
        document["workflow_name"] = workflow_name

    try:
        client = get_mongo_client()
        db = client[db_name]
        collection = db[coll_name]
        
        result = await collection.insert_one(document)
        inserted_id = str(result.inserted_id)
        
        logger.info(f"💾 Document saved to {db_name}.{coll_name}: {inserted_id}")
        
        # Update context variables if available
        if context_variables:
            try:
                saves = context_variables.get('database_saves', []) or []
                save_record = {
                    'id': inserted_id,
                    'database': db_name,
                    'collection': coll_name,
                    'saved_at': now.isoformat(),
                    'data_keys': list(data.keys())
                }
                saves.append(save_record)
                context_variables.set('database_saves', saves)
                context_variables.set('last_save', save_record)
            except Exception:
                pass  # Context variables are optional
        
        return {
            "status": "success",
            "inserted_id": inserted_id,
            "database": db_name,
            "collection": coll_name,
            "document_size": len(document),
            "created_at": now.isoformat()
        }
        
    except Exception as e:
        logger.error(f"❌ Database save failed: {e}")
        return {"status": "error", "message": f"Save failed: {e}"}

async def load_from_database(
    query: Annotated[Dict[str, Any], "MongoDB query to find documents"],
    database_name: Annotated[Optional[str], "Database name (defaults from workflow config)"] = None,
    collection_name: Annotated[Optional[str], "Collection name (defaults from workflow config)"] = None,
    limit: Annotated[int, "Maximum number of documents to return"] = 10,
    **runtime: Any,
) -> Dict[str, Any]:
    """AGENT CONTRACT: Load data from MongoDB database.

    Primary Objective:
        Provide agents an easy way to query MongoDB using workflow configuration.
        Automatically scopes to app_id for security.

    Args:
        query: MongoDB query dict (app_id will be added automatically)
        database_name: Override database name (optional)
        collection_name: Override collection name (optional)
        limit: Maximum documents to return (default 10)

    Returns:
        Dict containing status, documents, and metadata

    Example Usage:
        result = await load_from_database({
            "api_key_service": "openai",
            "created_at": {"$gte": datetime(2024, 1, 1)}
        }, collection_name="api_keys")
    """
    chat_id = runtime.get("chat_id")
    app_id = runtime.get("app_id")
    workflow_name = runtime.get("workflow_name") or runtime.get("workflow")

    logger = get_workflow_logger(workflow_name=(workflow_name or "unknown"), chat_id=chat_id, app_id=app_id)
    
    # Resolve configuration
    try:
        config = _get_database_config(workflow_name, database_name, collection_name)
        db_name = config["database_name"]
        coll_name = config["collection_name"]
    except Exception as e:
        return {"status": "error", "message": f"Configuration error: {e}"}

    # Build secure query (always scope to app)
    secure_query = dict(query)
    if app_id:
        try:
            secure_query["app_id"] = ObjectId(app_id)
        except:
            secure_query["app_id"] = app_id

    try:
        client = get_mongo_client()
        db = client[db_name]
        collection = db[coll_name]
        
        cursor = collection.find(secure_query).limit(limit)
        documents = await cursor.to_list(length=limit)
        
        # Convert ObjectIds to strings for JSON serialization
        for doc in documents:
            if "_id" in doc:
                doc["_id"] = str(doc["_id"])
            if "app_id" in doc and isinstance(doc["app_id"], ObjectId):
                doc["app_id"] = str(doc["app_id"])
        
        logger.info(f"📖 Loaded {len(documents)} documents from {db_name}.{coll_name}")
        
        return {
            "status": "success",
            "documents": documents,
            "count": len(documents),
            "database": db_name,
            "collection": coll_name,
            "query": query  # Return original query (not the secure one)
        }
        
    except Exception as e:
        logger.error(f"❌ Database load failed: {e}")
        return {"status": "error", "message": f"Load failed: {e}"}

async def update_in_database(
    query: Annotated[Dict[str, Any], "MongoDB query to find documents to update"],
    update_data: Annotated[Dict[str, Any], "Data to update in matching documents"],
    database_name: Annotated[Optional[str], "Database name (defaults from workflow config)"] = None,
    collection_name: Annotated[Optional[str], "Collection name (defaults from workflow config)"] = None,
    update_many: Annotated[bool, "Whether to update multiple documents (default: False)"] = False,
    **runtime: Any,
) -> Dict[str, Any]:
    """AGENT CONTRACT: Update data in MongoDB database.

    Primary Objective:
        Provide agents an easy way to update MongoDB documents using workflow configuration.
        Automatically scopes to app_id and adds updated_at timestamp.

    Args:
        query: MongoDB query to find documents to update
        update_data: Data to update (will be wrapped in $set automatically)
        database_name: Override database name (optional)
        collection_name: Override collection name (optional)
        update_many: Update multiple documents vs just the first match

    Returns:
        Dict containing status, modified_count, and metadata
    """
    chat_id = runtime.get("chat_id")
    app_id = runtime.get("app_id")
    workflow_name = runtime.get("workflow_name") or runtime.get("workflow")

    logger = get_workflow_logger(workflow_name=(workflow_name or "unknown"), chat_id=chat_id, app_id=app_id)
    
    # Resolve configuration
    try:
        config = _get_database_config(workflow_name, database_name, collection_name)
        db_name = config["database_name"]
        coll_name = config["collection_name"]
    except Exception as e:
        return {"status": "error", "message": f"Configuration error: {e}"}

    # Build secure query
    secure_query = dict(query)
    if app_id:
        try:
            secure_query["app_id"] = ObjectId(app_id)
        except:
            secure_query["app_id"] = app_id

    # Prepare update with timestamp
    update_doc = {
        "$set": {
            **update_data,
            "updated_at": datetime.now(timezone.utc)
        }
    }

    try:
        client = get_mongo_client()
        db = client[db_name]
        collection = db[coll_name]
        
        if update_many:
            result = await collection.update_many(secure_query, update_doc)
        else:
            result = await collection.update_one(secure_query, update_doc)
        
        logger.info(f"✏️ Updated {result.modified_count} documents in {db_name}.{coll_name}")
        
        return {
            "status": "success",
            "modified_count": result.modified_count,
            "matched_count": result.matched_count,
            "database": db_name,
            "collection": coll_name,
            "update_many": update_many
        }
        
    except Exception as e:
        logger.error(f"❌ Database update failed: {e}")
        return {"status": "error", "message": f"Update failed: {e}"}

async def delete_from_database(
    query: Annotated[Dict[str, Any], "MongoDB query to find documents to delete"],
    database_name: Annotated[Optional[str], "Database name (defaults from workflow config)"] = None,
    collection_name: Annotated[Optional[str], "Collection name (defaults from workflow config)"] = None,
    delete_many: Annotated[bool, "Whether to delete multiple documents (default: False)"] = False,
    **runtime: Any,
) -> Dict[str, Any]:
    """AGENT CONTRACT: Delete data from MongoDB database.

    Primary Objective:
        Provide agents an easy way to delete MongoDB documents using workflow configuration.
        Automatically scopes to app_id for security.

    Args:
        query: MongoDB query to find documents to delete
        database_name: Override database name (optional)
        collection_name: Override collection name (optional)  
        delete_many: Delete multiple documents vs just the first match

    Returns:
        Dict containing status, deleted_count, and metadata
    """
    chat_id = runtime.get("chat_id")
    app_id = runtime.get("app_id")
    workflow_name = runtime.get("workflow_name") or runtime.get("workflow")

    logger = get_workflow_logger(workflow_name=(workflow_name or "unknown"), chat_id=chat_id, app_id=app_id)
    
    # Resolve configuration
    try:
        config = _get_database_config(workflow_name, database_name, collection_name)
        db_name = config["database_name"]
        coll_name = config["collection_name"]
    except Exception as e:
        return {"status": "error", "message": f"Configuration error: {e}"}

    # Build secure query
    secure_query = dict(query)
    if app_id:
        try:
            secure_query["app_id"] = ObjectId(app_id)
        except:
            secure_query["app_id"] = app_id

    try:
        client = get_mongo_client()
        db = client[db_name]
        collection = db[coll_name]
        
        if delete_many:
            result = await collection.delete_many(secure_query)
        else:
            result = await collection.delete_one(secure_query)
        
        logger.info(f"🗑️ Deleted {result.deleted_count} documents from {db_name}.{coll_name}")
        
        return {
            "status": "success",
            "deleted_count": result.deleted_count,
            "database": db_name,
            "collection": coll_name,
            "delete_many": delete_many
        }
        
    except Exception as e:
        logger.error(f"❌ Database delete failed: {e}")
        return {"status": "error", "message": f"Delete failed: {e}"}

def _get_database_config(workflow_name: Optional[str], database_name: Optional[str], collection_name: Optional[str]) -> Dict[str, str]:
    """Helper to resolve database configuration from workflow or parameters."""
    
    # If both parameters provided, use them
    if database_name and collection_name:
        return {
            "database_name": database_name,
            "collection_name": collection_name
        }
    
    # Try to load from workflow configuration
    if workflow_name:
        try:
            workflow_config = workflow_manager.get_config(workflow_name)
            if workflow_config and 'database_manager' in workflow_config:
                db_config = workflow_config['database_manager']
                
                resolved_db = database_name or db_config.get('default_database', 'MozaiksAI')
                resolved_collection = collection_name or db_config.get('default_collection')
                
                if resolved_collection:
                    return {
                        "database_name": resolved_db,
                        "collection_name": resolved_collection
                    }
        except Exception:
            pass
    
    # Fallback validation
    if not database_name or not collection_name:
        raise DatabaseManagerError(
            f"Database configuration incomplete. Need database_name='{database_name}' "
            f"and collection_name='{collection_name}'. Either provide both parameters "
            f"or configure database_manager in {workflow_name} workflow json."
        )
    
    return {
        "database_name": database_name,
        "collection_name": collection_name
    }


def get_db_manager() -> Dict[str, Any]:
    """Expose the async database helpers as a stable tool mapping."""

    return {
        "save_to_database": save_to_database,
        "load_from_database": load_from_database,
        "update_in_database": update_in_database,
        "delete_from_database": delete_from_database,
    }
