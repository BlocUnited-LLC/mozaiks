"""Canonical workflow assignment-kind registry.

This registry is intentionally small and public so AppBuildPlan task validation,
task-batch work assignment contracts, and future structured-output schemas do
not grow drifting private task taxonomies.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class AssignmentKind(StrEnum):
    """Closed assignment vocabulary.

    The first block is the still-live AppBuildPlan vocabulary.  It remains
    accepted by the production generator until the atomic 5D cutover, but it
    is not executable semantic-compiler authority.  The second block is the
    exact family vocabulary used by offline CompilationPlan contracts.
    """

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
    MODULE_BACKEND_IMPLEMENTATION = "module_backend_implementation"
    INTEGRATION_ADAPTER_IMPLEMENTATION = "integration_adapter_implementation"
    APP_ROUTE_EXTENSION_IMPLEMENTATION = "app_route_extension_implementation"
    CUSTOM_PAGE_IMPLEMENTATION = "custom_page_implementation"
    WORKFLOW_PARTICIPANT_IMPLEMENTATION = "workflow_participant_implementation"
    WORKFLOW_STRUCTURED_MODELS_IMPLEMENTATION = (
        "workflow_structured_models_implementation"
    )
    WORKFLOW_TOOL_IMPLEMENTATION = "workflow_tool_implementation"
    WORKFLOW_UI_IMPLEMENTATION = "workflow_ui_implementation"
    MODULE_HELPER_IMPLEMENTATION = "module_helper_implementation"
    MODULE_ADMIN_PAGE_IMPLEMENTATION = "module_admin_page_implementation"


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
COMPILER_ASSIGNMENT_KINDS: frozenset[AssignmentKind] = frozenset(
    {
        AssignmentKind.MODULE_BACKEND_IMPLEMENTATION,
        AssignmentKind.INTEGRATION_ADAPTER_IMPLEMENTATION,
        AssignmentKind.APP_ROUTE_EXTENSION_IMPLEMENTATION,
        AssignmentKind.CUSTOM_PAGE_IMPLEMENTATION,
        AssignmentKind.WORKFLOW_PARTICIPANT_IMPLEMENTATION,
        AssignmentKind.WORKFLOW_STRUCTURED_MODELS_IMPLEMENTATION,
        AssignmentKind.WORKFLOW_TOOL_IMPLEMENTATION,
        AssignmentKind.WORKFLOW_UI_IMPLEMENTATION,
        AssignmentKind.MODULE_HELPER_IMPLEMENTATION,
        AssignmentKind.MODULE_ADMIN_PAGE_IMPLEMENTATION,
    }
)
REGISTERED_ASSIGNMENT_KINDS: frozenset[AssignmentKind] = (
    APP_BUILD_ASSIGNMENT_KINDS | COMPILER_ASSIGNMENT_KINDS
)


@dataclass(frozen=True)
class AssignmentContractDescriptor:
    """Locator for one proven workflow-owned structured-output contract."""

    assignment_kind: AssignmentKind
    workflow_name: str
    structured_output_model_id: str
    owned_artifact_families: tuple[str, ...]
    validator_ids: tuple[str, ...]
    identity_bindings: tuple[tuple[str, str], ...]


_ASSIGNMENT_CONTRACT_DESCRIPTORS: tuple[AssignmentContractDescriptor, ...] = (
    AssignmentContractDescriptor(
        AssignmentKind.MODULE_BACKEND_IMPLEMENTATION,
        "AppGenerator",
        "ModuleBackendImplementationOutput",
        (
            "module_backend_policy",
            "module_backend_repo",
            "module_backend_schemas",
            "module_backend_service",
        ),
        ("module_loader",),
        (("module_id", "module_id"), ("backend_role", "backend_role")),
    ),
    AssignmentContractDescriptor(
        AssignmentKind.INTEGRATION_ADAPTER_IMPLEMENTATION,
        "AppGenerator",
        "IntegrationAdapterImplementationOutput",
        ("app_service_adapter",),
        ("generated_app_validator",),
        (("integration_id", "pack_id"), ("adapter_area", "adapter_area")),
    ),
    AssignmentContractDescriptor(
        AssignmentKind.APP_ROUTE_EXTENSION_IMPLEMENTATION,
        "AppGenerator",
        "AppRouteExtensionImplementationOutput",
        ("app_service_route",),
        ("generated_app_validator",),
        (("route_id", "pack_id"),),
    ),
    AssignmentContractDescriptor(
        AssignmentKind.CUSTOM_PAGE_IMPLEMENTATION,
        "AppGenerator",
        "CustomPageImplementationOutput",
        ("app_ui_custom_route",),
        ("generated_app_validator",),
        (("page_id", "page_id"),),
    ),
    AssignmentContractDescriptor(
        AssignmentKind.WORKFLOW_PARTICIPANT_IMPLEMENTATION,
        "AgentGenerator",
        "WorkflowParticipantImplementationOutput",
        ("workflow_config",),
        ("workflow_manager",),
        (("workflow_id", "workflow_id"),),
    ),
    AssignmentContractDescriptor(
        AssignmentKind.WORKFLOW_STRUCTURED_MODELS_IMPLEMENTATION,
        "AgentGenerator",
        "WorkflowStructuredModelsOutput",
        ("workflow_config",),
        ("workflow_manager",),
        (("workflow_id", "workflow_id"),),
    ),
    AssignmentContractDescriptor(
        AssignmentKind.WORKFLOW_TOOL_IMPLEMENTATION,
        "AgentGenerator",
        "WorkflowToolImplementationOutput",
        ("workflow_tool",),
        ("workflow_manager",),
        (("workflow_id", "workflow_id"), ("tool_id", "tool_id")),
    ),
    AssignmentContractDescriptor(
        AssignmentKind.WORKFLOW_UI_IMPLEMENTATION,
        "AgentGenerator",
        "WorkflowUiImplementationOutput",
        ("workflow_ui",),
        ("generated_app_validator",),
        (("workflow_id", "workflow_id"), ("component_id", "component_id")),
    ),
    AssignmentContractDescriptor(
        AssignmentKind.MODULE_HELPER_IMPLEMENTATION,
        "AppGenerator",
        "ModuleHelperImplementationOutput",
        ("module_backend_helper",),
        ("module_loader",),
        (("module_id", "module_id"), ("helper_id", "helper_id")),
    ),
    AssignmentContractDescriptor(
        AssignmentKind.MODULE_ADMIN_PAGE_IMPLEMENTATION,
        "AppGenerator",
        "ModuleAdminPageImplementationOutput",
        ("module_admin_ui",),
        ("generated_app_validator",),
        (("module_id", "module_id"), ("page_id", "page_id")),
    ),
)
ASSIGNMENT_CONTRACT_DESCRIPTORS: Mapping[
    AssignmentKind, AssignmentContractDescriptor
] = MappingProxyType(
    {
        descriptor.assignment_kind: descriptor
        for descriptor in _ASSIGNMENT_CONTRACT_DESCRIPTORS
    }
)


def assignment_contract_descriptor(
    kind: AssignmentKind | str,
) -> AssignmentContractDescriptor | None:
    return ASSIGNMENT_CONTRACT_DESCRIPTORS.get(AssignmentKind(kind))


def registered_assignment_kind_values() -> frozenset[str]:
    return frozenset(kind.value for kind in REGISTERED_ASSIGNMENT_KINDS)


def app_build_assignment_kind_values() -> frozenset[str]:
    return frozenset(kind.value for kind in APP_BUILD_ASSIGNMENT_KINDS)


__all__ = [
    "APP_BUILD_ASSIGNMENT_KINDS",
    "COMPILER_ASSIGNMENT_KINDS",
    "REGISTERED_ASSIGNMENT_KINDS",
    "AssignmentKind",
    "AssignmentContractDescriptor",
    "ASSIGNMENT_CONTRACT_DESCRIPTORS",
    "assignment_contract_descriptor",
    "app_build_assignment_kind_values",
    "registered_assignment_kind_values",
]
