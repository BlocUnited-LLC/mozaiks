# ==============================================================================
# FILE: core/workflow/context/db_adapters.py
# DESCRIPTION: Database adapters for modular context variable loading.
# ==============================================================================

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from logs.logging_config import get_workflow_logger
from .schema import ContextVariableSource

logger = get_workflow_logger("db_adapters")


class DatabaseAdapter(ABC):
    """Abstract base class for database adapters."""

    @abstractmethod
    async def fetch_one(
        self,
        source: ContextVariableSource,
        query: Dict[str, Any],
        projection: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch a single document from the data source.

        Args:
            source: The context variable source definition.
            query: The query to execute.
            projection: The projection to apply.

        Returns:
            A single document or None if not found.
        """
        raise NotImplementedError


class MongoAdapter(DatabaseAdapter):
    """Adapter for fetching data from MongoDB."""

    async def fetch_one(
        self,
        source: ContextVariableSource,
        query: Dict[str, Any],
        projection: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Fetches a single document from MongoDB."""
        db_name = source.database_name
        collection = source.collection
        if not db_name or not collection:
            logger.warning(
                "MongoAdapter skipping fetch (database_name=%s, collection=%s)",
                db_name,
                collection,
            )
            return None

        try:
            from mozaiksai.core.core_config import get_mongo_client

            client = get_mongo_client()
            logger.info(f"MongoAdapter querying {db_name}.{collection} with query={query}, projection={projection}")
            
            # Sort by _id descending to get most recent document first (handles duplicates)
            # MongoDB ObjectId contains timestamp, so this gives us chronological ordering
            cursor = client[db_name][collection].find(query, projection).sort("_id", -1).limit(1)
            docs = await cursor.to_list(length=1)
            doc = docs[0] if docs else None
            
            logger.info(f"MongoAdapter query result: doc={'found (most recent)' if doc else 'None'}")
            if doc:
                logger.info(f"MongoAdapter document keys: {list(doc.keys())}")
            return doc
        except Exception as err:
            logger.error(
                f"MongoAdapter failed fetching from {db_name}.{collection}: {err}"
            )
            return None


_ADAPTER_REGISTRY: Dict[str, DatabaseAdapter] = {
    "mongodb": MongoAdapter(),
}


def get_db_adapter(source: ContextVariableSource) -> Optional[DatabaseAdapter]:
    """
    Factory function to get a database adapter based on the source definition.
    Defaults to MongoDB if db_type is not specified for backward compatibility.
    """
    db_type = getattr(source, "db_type", "mongodb") or "mongodb"
    adapter = _ADAPTER_REGISTRY.get(db_type.lower())
    if not adapter:
        logger.warning(f"No database adapter found for db_type='{db_type}'")
    return adapter
