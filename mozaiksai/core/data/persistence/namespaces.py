"""Canonical framework-owned persistence namespaces.

Mozaiks uses one canonical system database for runtime state and builder
artifacts. Generated/hosted app business collections are separate from these
framework-owned collections and should be scoped by app/module contracts.
"""

from __future__ import annotations


SYSTEM_DATABASE = "mozaiksai"


class RuntimeCollections:
    CHAT_SESSIONS = "ChatSessions"
    WORKFLOW_STATS = "WorkflowStats"
    GENERAL_CHAT_SESSIONS = "GeneralChatSessions"
    GENERAL_CHAT_COUNTERS = "GeneralChatCounters"


class BuilderCollections:
    CONCEPTS = "BuilderConcepts"
    BUILD_PLANS = "BuilderBuildPlans"
    DESIGN_DOCUMENTS = "DesignDocuments"
    THEME_CAPTURES = "ThemeCaptures"
    DATA_CONTRACTS = "DataContracts"
    DATABASE_MIGRATIONS = "DatabaseMigrations"
    WORKFLOW_EXPORTS = "WorkflowExports"
    LLM_CONFIG = "LLMConfig"


class PlatformCollections:
    BUILD_EVENTS_OUTBOX = "PlatformBuildEventsOutbox"
    BUILD_STATE = "BuildState"
    APP_CONNECTORS = "AppConnectors"


__all__ = [
    "SYSTEM_DATABASE",
    "RuntimeCollections",
    "BuilderCollections",
    "PlatformCollections",
]

