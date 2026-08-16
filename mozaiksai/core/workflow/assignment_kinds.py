"""Canonical workflow assignment-kind registry.

This registry is intentionally small and public so AppBuildPlan task validation,
task-batch work assignment contracts, and future structured-output schemas do
not grow drifting private task taxonomies.
"""

from __future__ import annotations

from enum import StrEnum


class AssignmentKind(StrEnum):
    SUBSCRIPTION_CONFIG = "subscription_config"
    SERVICE_FOUNDATION = "service_foundation"
    MODULE_CONTRACT = "module_contract"
    PERSISTENCE_CONTRACT = "persistence_contract"
    DATA_MIGRATIONS = "data_migrations"
    DATA_MODELS = "data_models"
    BUSINESS_SERVICES = "business_services"
    API_SURFACE = "api_surface"
    PAGE_BUNDLE = "page_bundle"
    AGENT_BACKEND_INTEGRATION = "agent_backend_integration"
    REFINEMENT_HARNESS = "refinement_harness"
    INTEGRATION = "integration"
    VALIDATION = "validation"


APP_BUILD_ASSIGNMENT_KINDS: frozenset[AssignmentKind] = frozenset(
    {
        AssignmentKind.SUBSCRIPTION_CONFIG,
        AssignmentKind.SERVICE_FOUNDATION,
        AssignmentKind.MODULE_CONTRACT,
        AssignmentKind.PERSISTENCE_CONTRACT,
        AssignmentKind.DATA_MIGRATIONS,
        AssignmentKind.DATA_MODELS,
        AssignmentKind.BUSINESS_SERVICES,
        AssignmentKind.API_SURFACE,
        AssignmentKind.PAGE_BUNDLE,
        AssignmentKind.AGENT_BACKEND_INTEGRATION,
        AssignmentKind.REFINEMENT_HARNESS,
    }
)
WORK_INTEGRATION_EXTENSION_KINDS: frozenset[AssignmentKind] = frozenset(
    {
        AssignmentKind.INTEGRATION,
        AssignmentKind.VALIDATION,
    }
)
REGISTERED_ASSIGNMENT_KINDS: frozenset[AssignmentKind] = (
    APP_BUILD_ASSIGNMENT_KINDS | WORK_INTEGRATION_EXTENSION_KINDS
)


def registered_assignment_kind_values() -> frozenset[str]:
    return frozenset(kind.value for kind in REGISTERED_ASSIGNMENT_KINDS)


def app_build_assignment_kind_values() -> frozenset[str]:
    return frozenset(kind.value for kind in APP_BUILD_ASSIGNMENT_KINDS)


__all__ = [
    "APP_BUILD_ASSIGNMENT_KINDS",
    "REGISTERED_ASSIGNMENT_KINDS",
    "WORK_INTEGRATION_EXTENSION_KINDS",
    "AssignmentKind",
    "app_build_assignment_kind_values",
    "registered_assignment_kind_values",
]
