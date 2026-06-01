"""Future generated-module persistence API.

This package defines the generated-module persistence API. Generated code
should use ctx.persistence rather than importing get_mongo_client() directly.
"""

from __future__ import annotations

from .adapter import ModulePersistenceContext, PersistenceCollection
from .indexes import DatabaseIndexApplyError, apply_database_indexes
from .intent_loader import (
    DataContractLoadError,
    index_data_contract_by_entity,
    load_data_contract,
)
from .migrations import (
    APP_DATA_MIGRATIONS_COLLECTION,
    DatabaseMigrationError,
    DatabaseMigrationOperationError,
    apply_data_migrations,
    get_migration_health_report,
    load_data_migrations,
    migration_hash,
)
from .mongo import MongoPersistenceCollection, MongoPersistenceContext
from .naming import (
    collection_name_for,
    safe_identifier,
    scope_filter_for,
    scope_metadata,
    short_stable_hash,
)
from .startup_policy import (
    DATABASE_STARTUP_POLICY_ENV,
    DatabaseStartupPolicyError,
    get_database_startup_policy,
)

__all__ = [
    "ModulePersistenceContext",
    "MongoPersistenceCollection",
    "MongoPersistenceContext",
    "PersistenceCollection",
    "DataContractLoadError",
    "DatabaseIndexApplyError",
    "DATABASE_STARTUP_POLICY_ENV",
    "APP_DATA_MIGRATIONS_COLLECTION",
    "DatabaseMigrationError",
    "DatabaseMigrationOperationError",
    "DatabaseStartupPolicyError",
    "apply_database_indexes",
    "apply_data_migrations",
    "collection_name_for",
    "get_migration_health_report",
    "index_data_contract_by_entity",
    "load_data_contract",
    "load_data_migrations",
    "migration_hash",
    "get_database_startup_policy",
    "safe_identifier",
    "scope_filter_for",
    "scope_metadata",
    "short_stable_hash",
]
