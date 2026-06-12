"""Future generated-module persistence API.

This package defines the generated-module persistence API. Generated code
should use ctx.persistence rather than importing get_mongo_client() directly.
"""

from __future__ import annotations

from .adapter import ModulePersistenceContext, PersistenceCollection
from .app_data import (
    DATA_COLLECTIONS,
    AppData,
    AppDataAliasError,
    aliases_from_data_contract,
    app_data_from_context,
    collection_name_for_alias,
    default_data_contract_path,
    known_data_aliases,
    load_app_data_contract,
)
from .indexes import (
    DatabaseIndexApplyError,
    DataContractIndexPlanItem,
    DataContractIndexRunResult,
    apply_database_indexes,
    run_data_index_application,
)
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
    "AppData",
    "AppDataAliasError",
    "DataContractLoadError",
    "DataContractIndexPlanItem",
    "DataContractIndexRunResult",
    "DATA_COLLECTIONS",
    "DatabaseIndexApplyError",
    "DATABASE_STARTUP_POLICY_ENV",
    "APP_DATA_MIGRATIONS_COLLECTION",
    "DatabaseMigrationError",
    "DatabaseMigrationOperationError",
    "DatabaseStartupPolicyError",
    "aliases_from_data_contract",
    "app_data_from_context",
    "apply_database_indexes",
    "apply_data_migrations",
    "collection_name_for_alias",
    "collection_name_for",
    "default_data_contract_path",
    "get_migration_health_report",
    "index_data_contract_by_entity",
    "known_data_aliases",
    "load_app_data_contract",
    "load_data_contract",
    "load_data_migrations",
    "migration_hash",
    "run_data_index_application",
    "get_database_startup_policy",
    "safe_identifier",
    "scope_filter_for",
    "scope_metadata",
    "short_stable_hash",
]
