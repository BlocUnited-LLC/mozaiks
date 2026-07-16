"""Canonical framework-owned persistence namespaces.

Mozaiks uses one canonical system database for runtime state and builder
artifacts. Generated/hosted app business collections are separate from these
framework-owned collections and should be scoped by app/module contracts.

AG2 1.0 beta telemetry is exported through OpenTelemetry spans. Runtime token usage
measurements are stored separately in RuntimeUsageEvents.
"""

from __future__ import annotations

SYSTEM_DATABASE = "mozaiksai"


class RuntimeCollections:
    CHAT_SESSIONS = "ChatSessions"
    AG2_STREAM_EVENTS = "AG2StreamEvents"
    AG2_STREAM_HEADS = "AG2StreamHeads"
    GENERAL_CHAT_SESSIONS = "GeneralChatSessions"
    GENERAL_CHAT_COUNTERS = "GeneralChatCounters"
    RUNTIME_USAGE_EVENTS = "RuntimeUsageEvents"
    RUNTIME_TOKEN_BUDGET_ALERTS = "RuntimeTokenBudgetAlerts"
    RUNTIME_TOKEN_WALLET_BALANCES = "RuntimeTokenWalletBalances"
    RUNTIME_TOKEN_WALLET_ENTRIES = "RuntimeTokenWalletEntries"
    RUNTIME_BILLING_FULFILLMENT_COMMANDS = "RuntimeBillingFulfillmentCommands"


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
    CONNECTORS = "Connectors"


__all__ = [
    "SYSTEM_DATABASE",
    "RuntimeCollections",
    "BuilderCollections",
    "PlatformCollections",
]

