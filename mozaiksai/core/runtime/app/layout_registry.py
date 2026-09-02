"""Canonical typed application layout registry.

This module defines the first versioned registry for the Mozaiks application
layout contract. It is intentionally data-only: no filesystem access, no
loader behavior, no generator prompt behavior, and no path helper mutation.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mozaiksai.core.stub_kinds import StubKind
from mozaiksai.core.workflow.assignment_kinds import AssignmentKind

SCHEMA_VERSION: Literal["mozaiks.app_layout.v2"] = "mozaiks.app_layout.v2"

_PLACEHOLDER_RE = re.compile(r"\{([a-z][a-z0-9_]*)\}")
_GLOB_CHARS = frozenset("*?[")
_RESERVED_MODULE_BACKEND_HELPERS = frozenset(
    {
        "api",
        "base_handler",
        "handler",
        "models",
        "policy",
        "repo",
        "schemas",
        "service",
    }
)


class LayoutModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=False)


class PathScope(StrEnum):
    WORKSPACE_ROOT = "workspace_root"
    APP_BUNDLE_ROOT = "app_bundle_root"
    MODULE_RELATIVE = "module_relative"
    WORKFLOW_RELATIVE = "workflow_relative"
    GENERATED_STAGING = "generated_staging"
    DEPLOYMENT_DERIVED = "deployment_derived"


class ArtifactKind(StrEnum):
    APP_MANIFEST = "app_manifest"
    APP_CONFIG = "app_config"
    APP_AUTH_CONFIG = "app_auth_config"
    APP_SHELL_CONFIG = "app_shell_config"
    APP_INTEGRATIONS_CONFIG = "app_integrations_config"
    APP_TARGETS_CONFIG = "app_targets_config"
    APP_REFINEMENT_POLICY = "app_refinement_policy"
    APP_SUBSCRIPTION_CONFIG = "app_subscription_config"
    APP_PROVENANCE = "app_provenance"
    APP_BRAND_THEME = "app_brand_theme"
    APP_DASHBOARD = "app_dashboard"
    APP_DATA_CONTRACT = "app_data_contract"
    APP_DATA_MIGRATION = "app_data_migration"
    APP_SECRET_REFERENCES = "app_secret_references"
    APP_ROOT_SUPPORT = "app_root_support"
    APP_DEPLOYMENT_ARTIFACT = "app_deployment_artifact"
    APP_SERVICE_SUPPORT = "app_service_support"
    APP_SERVICE_INTEGRATION_CLIENT = "app_service_integration_client"
    APP_SERVICE_ADAPTER = "app_service_adapter"
    APP_SERVICE_ROUTE = "app_service_route"
    APP_UI_ROUTE_MANIFEST = "app_ui_route_manifest"
    APP_UI_PAGE_SCHEMA = "app_ui_page_schema"
    APP_UI_CUSTOM_ROUTE = "app_ui_custom_route"
    APP_UI_AUTH_ADAPTER = "app_ui_auth_adapter"
    APP_UI_EXTENSION_BARREL = "app_ui_extension_barrel"
    APP_UI_MODULE_API = "app_ui_module_api"
    APP_ADMIN_REGISTRY = "app_admin_registry"
    APP_FRAMEWORK_METADATA = "app_framework_metadata"
    APP_BACKEND_SUPPORT = "app_backend_support"
    APP_REFINEMENT_HARNESS = "app_refinement_harness"
    MODULE_MANIFEST = "module_manifest"
    MODULE_CONTRACT = "module_contract"
    MODULE_RUNTIME_EXTENSIONS = "module_runtime_extensions"
    MODULE_BACKEND_HANDLER = "module_backend_handler"
    MODULE_BACKEND_BASE_HANDLER = "module_backend_base_handler"
    MODULE_BACKEND_API = "module_backend_api"
    MODULE_BACKEND_SERVICE = "module_backend_service"
    MODULE_BACKEND_REPO = "module_backend_repo"
    MODULE_BACKEND_POLICY = "module_backend_policy"
    MODULE_BACKEND_SCHEMAS = "module_backend_schemas"
    MODULE_BACKEND_HELPER = "module_backend_helper"
    MODULE_ADMIN_UI = "module_admin_ui"
    MODULE_UI_EXTENSION_BARREL = "module_ui_extension_barrel"
    WORKFLOW_MANIFEST = "workflow_manifest"
    WORKFLOW_CONFIG = "workflow_config"
    WORKFLOW_TOOL = "workflow_tool"
    WORKFLOW_UI = "workflow_ui"
    WORKFLOW_TASK_BATCH = "workflow_task_batch"
    BUILD_CONTEXT_REGISTRY = "build_context_registry"
    BUILD_CONTEXT_ASSET = "build_context_asset"
    CAPABILITY_PACK_OUTPUT = "capability_pack_output"
    GENERATED_APP_STAGING = "generated_app_staging"
    GENERATED_WORKFLOW_STAGING = "generated_workflow_staging"
    PROHIBITED_LEGACY = "prohibited_legacy"


class LayoutOwner(StrEnum):
    PLATFORM = "platform"
    RUNTIME = "runtime"
    FACTORY = "factory"
    APP_WORKSPACE = "app_workspace"
    MODULE = "module"
    WORKFLOW = "workflow"
    CAPABILITY_PACK = "capability_pack"
    DOWNLOAD_RENDERER = "download_renderer"
    REGISTERED_EXTENSION = "registered_extension"
    PROHIBITED = "prohibited"


class Requirement(StrEnum):
    REQUIRED = "required"
    CONDITIONAL = "conditional"
    OPTIONAL = "optional"
    GENERATED = "generated"
    PROHIBITED = "prohibited"


class Multiplicity(StrEnum):
    SINGLE = "single"
    MANY = "many"


class ConditionIdentifier(StrEnum):
    ALWAYS = "always"
    WHEN_APP_DECLARED = "when_app_declared"
    WHEN_MODULE_DECLARED = "when_module_declared"
    WHEN_PAGE_DECLARED = "when_page_declared"
    WHEN_CUSTOM_ROUTE_DECLARED = "when_custom_route_declared"
    WHEN_WORKFLOW_DECLARED = "when_workflow_declared"
    WHEN_AUTH_ENABLED = "when_auth_enabled"
    WHEN_DATA_CONTRACT_REQUIRED = "when_data_contract_required"
    WHEN_SUBSCRIPTIONS_REQUIRED = "when_subscriptions_required"
    WHEN_REFINEMENT_HARNESS_REQUIRED = "when_refinement_harness_required"
    WHEN_MANAGED_CAPABILITY_SELECTED = "when_managed_capability_selected"
    WHEN_DEPLOYMENT_EXPORT_REQUESTED = "when_deployment_export_requested"
    WHEN_GENERATED_STAGING_SELECTED = "when_generated_staging_selected"
    WHEN_EXTENSION_SELECTED = "when_extension_selected"
    NEVER = "never"


class MaterializerIdentifier(StrEnum):
    APP_GENERATOR = "app_generator"
    MODULE_CONTRACT_EXECUTOR = "module_contract_executor"
    MODULE_BACKEND_EXECUTOR = "module_backend_executor"
    PAGE_SCHEMA_EXECUTOR = "page_schema_executor"
    WORKFLOW_GENERATOR = "workflow_generator"
    CAPABILITY_PACK_MATERIALIZER = "capability_pack_materializer"
    DOWNLOAD_DEPLOYMENT_RENDERER = "download_deployment_renderer"
    PRESERVED_OPAQUE = "preserved_opaque"
    HUMAN_AUTHORED = "human_authored"
    NONE = "none"


class ValidatorIdentifier(StrEnum):
    APP_LOADER = "app_loader"
    MODULE_LOADER = "module_loader"
    WORKFLOW_MANAGER = "workflow_manager"
    GENERATED_APP_VALIDATOR = "generated_app_validator"
    DATA_CONTRACT_LOADER = "data_contract_loader"
    SUBSCRIPTIONS_LOADER = "subscriptions_loader"
    PROVENANCE_LOADER = "provenance_loader"
    APP_PATHS = "app_paths"
    NONE = "none"


class RuntimeConsumerIdentifier(StrEnum):
    APP_LOADER = "app_loader"
    PLATFORM_HOST = "platform_host"
    MODULE_LOADER = "module_loader"
    MODULE_EXECUTOR = "module_executor"
    MODULE_EVENT_ROUTER = "module_event_router"
    WORKFLOW_MANAGER = "workflow_manager"
    REFINEMENT_ENGINE = "refinement_engine"
    ENTITLEMENT_ADAPTER = "entitlement_adapter"
    PROVENANCE_LOADER = "provenance_loader"
    DOWNLOAD_EXPORT = "download_export"
    NONE = "none"


class SecurityClass(StrEnum):
    PUBLIC_METADATA = "public_metadata"
    INTERNAL_CONTRACT = "internal_contract"
    SECRET_REFERENCE_NAMES = "secret_reference_names"
    EXECUTABLE_STUB = "executable_stub"
    DEPLOYMENT_METADATA = "deployment_metadata"
    GENERATED_STAGING = "generated_staging"
    PROHIBITED = "prohibited"


class PlaceholderIdentifier(StrEnum):
    APP_ID = "app_id"
    BUILD_ID = "build_id"
    MODULE_ID = "module_id"
    PAGE_ID = "page_id"
    WORKFLOW_ID = "workflow_id"
    MIGRATION_ID = "migration_id"
    PACK_ID = "pack_id"
    ADAPTER_AREA = "adapter_area"
    HELPER_ID = "helper_id"
    TOOL_ID = "tool_id"
    COMPONENT_ID = "component_id"
    ASSET_ID = "asset_id"
    EXTENSION_ID = "extension_id"


class ExtensionSlot(StrEnum):
    MANAGED_CAPABILITY_CONFIG = "managed_capability_config"
    MANAGED_CAPABILITY_CLIENT = "managed_capability_client"
    CAPABILITY_PACK_OUTPUT = "capability_pack_output"
    SERVICE_ADAPTER = "service_adapter"
    SERVICE_ROUTE = "service_route"
    BUILD_CONTEXT_PACK = "build_context_pack"


class ArtifactFamily(LayoutModel):
    kind: ArtifactKind
    owner: LayoutOwner
    requirement: Requirement
    multiplicity: Multiplicity
    condition: ConditionIdentifier
    path_scope: PathScope
    path_template: str = Field(min_length=1)
    materializer: MaterializerIdentifier
    validator: ValidatorIdentifier
    runtime_consumer: RuntimeConsumerIdentifier
    security_class: SecurityClass
    assignment_kinds: tuple[AssignmentKind, ...] = Field(default_factory=tuple)
    allowed_stub_kinds: tuple[StubKind, ...] = Field(default_factory=tuple)
    dependency_families: tuple[ArtifactKind, ...] = Field(default_factory=tuple)
    semantic_input_kinds: tuple[str, ...] = Field(default_factory=tuple)
    summary: str | None = None

    @field_validator("path_template")
    @classmethod
    def _validate_template(cls, value: str) -> str:
        return _normalize_template(value)

    @field_validator("assignment_kinds")
    @classmethod
    def _normalize_assignment_kinds(cls, value: tuple[AssignmentKind, ...]) -> tuple[AssignmentKind, ...]:
        parsed = tuple(AssignmentKind(item) for item in value)
        if len(parsed) != len(set(parsed)):
            raise ValueError("assignment_kinds must be unique")
        return tuple(sorted(parsed, key=lambda kind: kind.value))

    @field_validator("allowed_stub_kinds")
    @classmethod
    def _normalize_stub_kinds(cls, value: tuple[StubKind, ...]) -> tuple[StubKind, ...]:
        return tuple(sorted(set(value), key=lambda kind: kind.value))

    @field_validator("dependency_families")
    @classmethod
    def _normalize_dependency_families(
        cls, value: tuple[ArtifactKind, ...]
    ) -> tuple[ArtifactKind, ...]:
        return tuple(sorted(set(value), key=lambda kind: kind.value))

    @field_validator("semantic_input_kinds")
    @classmethod
    def _normalize_semantic_input_kinds(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        # Lazy import avoids a layout-registry <-> semantics package cycle.
        from mozaiksai.core.semantics.graph import SemanticNodeKind

        try:
            normalized = tuple(sorted({SemanticNodeKind(item).value for item in value}))
        except ValueError as exc:
            raise ValueError("semantic_input_kinds contains an unknown node kind") from exc
        if len(normalized) != len(value):
            raise ValueError("semantic_input_kinds must be unique")
        return normalized

    @model_validator(mode="after")
    def _validate_dependencies_exclude_self(self) -> ArtifactFamily:
        if self.kind in self.dependency_families:
            raise ValueError(f"artifact family {self.kind.value!r} cannot depend on itself")
        return self

    @model_validator(mode="after")
    def _validate_condition(self) -> ArtifactFamily:
        if self.requirement == Requirement.PROHIBITED and self.condition != ConditionIdentifier.NEVER:
            raise ValueError("prohibited artifact families must use condition=never")
        if self.requirement != Requirement.PROHIBITED and self.condition == ConditionIdentifier.NEVER:
            raise ValueError("condition=never is reserved for prohibited artifact families")
        return self

    @property
    def placeholders(self) -> tuple[PlaceholderIdentifier, ...]:
        names = tuple(_PLACEHOLDER_RE.findall(self.path_template))
        return tuple(PlaceholderIdentifier(name) for name in names)

    @property
    def identity_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "owner": self.owner.value,
            "requirement": self.requirement.value,
            "multiplicity": self.multiplicity.value,
            "condition": self.condition.value,
            "path_scope": self.path_scope.value,
            "path_template": self.path_template,
            "materializer": self.materializer.value,
            "validator": self.validator.value,
            "runtime_consumer": self.runtime_consumer.value,
            "security_class": self.security_class.value,
            "assignment_kinds": [kind.value for kind in self.assignment_kinds],
            "allowed_stub_kinds": [kind.value for kind in self.allowed_stub_kinds],
            "dependency_families": [kind.value for kind in self.dependency_families],
            "semantic_input_kinds": list(self.semantic_input_kinds),
        }


class ArtifactMatch(LayoutModel):
    family: ArtifactFamily
    normalized_path: str
    values: dict[PlaceholderIdentifier, str] = Field(default_factory=dict)


class LayoutExtension(LayoutModel):
    slot: ExtensionSlot
    pack_id: str = Field(min_length=1)
    path: str | None = None

    @field_validator("pack_id")
    @classmethod
    def _validate_pack_id(cls, value: str) -> str:
        text = value.strip()
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", text):
            raise ValueError("pack_id must be a lowercase registry identifier")
        return text

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalize_template(value)

    @model_validator(mode="after")
    def _validate_slot_path(self) -> LayoutExtension:
        if self.slot == ExtensionSlot.CAPABILITY_PACK_OUTPUT:
            if not self.path:
                raise ValueError("capability_pack_output extensions require an exact path")
            _validate_capability_pack_output_path(self.path)
        elif self.path is not None:
            raise ValueError("path is only valid for capability_pack_output extensions")
        return self


class AppLayoutRegistry(LayoutModel):
    schema_version: Literal["mozaiks.app_layout.v2"] = SCHEMA_VERSION
    families: tuple[ArtifactFamily, ...] = Field(min_length=1)
    registry_digest: str = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_registry(self) -> AppLayoutRegistry:
        _validate_unique_templates(self.families)
        _validate_dependency_closure(self.families)
        expected = _stable_digest(
            {
                "schema_version": self.schema_version,
                "families": [family.identity_payload for family in self.families],
            }
        )
        if self.registry_digest != expected:
            raise ValueError("registry_digest does not match registered artifact families")
        return self

    def ordered_families(self) -> tuple[ArtifactFamily, ...]:
        """Deterministic dependency-respecting family order.

        Kahn topological order over ``dependency_families`` (a dependency
        orders before its dependents), with ties broken by (kind, path_scope,
        path_template) so the order is total and identical on every host and
        across processes. The registry validator has already proven the
        dependency graph acyclic and closed, so this cannot fail.
        """
        by_kind: dict[ArtifactKind, list[ArtifactFamily]] = {}
        for family in self.families:
            by_kind.setdefault(family.kind, []).append(family)

        def _sort_key(family: ArtifactFamily) -> tuple[str, str, str]:
            return (family.kind.value, family.path_scope.value, family.path_template)

        indegree: dict[ArtifactKind, int] = {kind: 0 for kind in by_kind}
        dependents: dict[ArtifactKind, set[ArtifactKind]] = {kind: set() for kind in by_kind}
        for family in self.families:
            for dependency in family.dependency_families:
                if family.kind not in dependents[dependency]:
                    dependents[dependency].add(family.kind)
                    indegree[family.kind] += 1

        ready = sorted((kind for kind, count in indegree.items() if count == 0), key=lambda k: k.value)
        ordered: list[ArtifactFamily] = []
        while ready:
            kind = ready.pop(0)
            ordered.extend(sorted(by_kind[kind], key=_sort_key))
            newly_ready: list[ArtifactKind] = []
            for dependent in dependents[kind]:
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    newly_ready.append(dependent)
            if newly_ready:
                ready = sorted([*ready, *newly_ready], key=lambda k: k.value)
        return tuple(ordered)

    def iter_artifact_kinds(self) -> tuple[ArtifactKind, ...]:
        return tuple(sorted({family.kind for family in self.families}, key=lambda kind: kind.value))

    def match_path(self, path: str, scope: PathScope | str) -> ArtifactMatch:
        resolved_scope = PathScope(scope)
        normalized = _normalize_runtime_path(path)
        matches = [
            match
            for family in self.families
            if family.path_scope is resolved_scope
            for match in [_match_family(family, normalized)]
            if match is not None
        ]
        if not matches:
            raise ValueError(f"path {path!r} is not registered for scope {resolved_scope.value!r}")
        prohibited = [m for m in matches if m.family.requirement is Requirement.PROHIBITED]
        if prohibited:
            # Prohibition is absolute: no extension or literal registration may
            # shadow a prohibited family through specificity precedence.
            prohibited.sort(key=lambda m: (len(m.values), m.family.path_template))
            return prohibited[0]
        if len(matches) > 1:
            # Deterministic specificity precedence: a template with fewer
            # placeholders is more literal and wins (a registered-extension
            # literal beats the generic core template it specializes).  Only a
            # tie between distinct templates is genuine ambiguity.
            best_specificity = min(len(match.values) for match in matches)
            matches = [match for match in matches if len(match.values) == best_specificity]
        if len(matches) > 1:
            templates = sorted(match.family.path_template for match in matches)
            raise ValueError(f"path {path!r} is ambiguous for scope {resolved_scope.value!r}: {templates}")
        return matches[0]

    def kinds_for_assignment(self, kind: AssignmentKind | str) -> tuple[ArtifactKind, ...]:
        assignment_kind = AssignmentKind(kind)
        return tuple(
            sorted(
                {
                    family.kind
                    for family in self.families
                    if assignment_kind in family.assignment_kinds
                    and family.requirement != Requirement.PROHIBITED
                },
                key=lambda item: item.value,
            )
        )

    def validate_registered_path(
        self,
        path: str,
        assignment_kind: AssignmentKind | str | None,
        scope: PathScope | str,
    ) -> ArtifactMatch:
        match = self.match_path(path, scope)
        if match.family.requirement == Requirement.PROHIBITED:
            raise ValueError(f"path {path!r} is explicitly prohibited by {SCHEMA_VERSION}")
        if assignment_kind is None:
            return match
        resolved_kind = AssignmentKind(assignment_kind)
        if resolved_kind not in match.family.assignment_kinds:
            raise ValueError(
                f"path {path!r} is registered as {match.family.kind.value}, "
                f"not owned by assignment kind {resolved_kind.value!r}"
            )
        return match


def iter_artifact_kinds() -> tuple[ArtifactKind, ...]:
    return default_app_layout_registry().iter_artifact_kinds()


def match_path(path: str, scope: PathScope | str) -> ArtifactMatch:
    return default_app_layout_registry().match_path(path, scope)


def kinds_for_assignment(kind: AssignmentKind | str) -> tuple[ArtifactKind, ...]:
    return default_app_layout_registry().kinds_for_assignment(kind)


def validate_registered_path(
    path: str,
    assignment_kind: AssignmentKind | str | None,
    scope: PathScope | str,
) -> ArtifactMatch:
    return default_app_layout_registry().validate_registered_path(path, assignment_kind, scope)


def resolve_taxonomy_artifact_family(
    identifier: str,
    *,
    taxonomy_registry: Any = None,
    layout_registry: AppLayoutRegistry | None = None,
) -> tuple[ArtifactFamily, ...]:
    """Resolve a taxonomy name to authoritative layout rows.

    The taxonomy supplies only the ``ArtifactKind`` identifier. Every path,
    validator, consumer, owner, and security classification returned here
    comes from this layout registry.
    """
    from mozaiksai.core.taxonomy import SemanticCategory, default_taxonomy_registry

    taxonomy = taxonomy_registry or default_taxonomy_registry()
    entry = taxonomy.resolve(SemanticCategory.ARTIFACT_FAMILY, identifier)
    try:
        kind = ArtifactKind(entry.identifier)
    except ValueError as exc:
        raise ValueError(
            f"taxonomy artifact-family {entry.identifier!r} has no ArtifactKind"
        ) from exc
    registry = layout_registry or default_app_layout_registry()
    rows = tuple(family for family in registry.families if family.kind is kind)
    if not rows:
        raise ValueError(
            f"taxonomy artifact-family {entry.identifier!r} has no layout_registry row"
        )
    return rows


def default_app_layout_registry() -> AppLayoutRegistry:
    return build_app_layout_registry()


def build_app_layout_registry(extensions: tuple[LayoutExtension, ...] = ()) -> AppLayoutRegistry:
    families = tuple(sorted([*_core_families(), *_extension_families(extensions)], key=_family_sort_key))
    return AppLayoutRegistry(
        families=families,
        registry_digest=_stable_digest(
            {
                "schema_version": SCHEMA_VERSION,
                "families": [family.identity_payload for family in families],
            }
        ),
    )


def _core_families() -> tuple[ArtifactFamily, ...]:
    app = PathScope.APP_BUNDLE_ROOT
    workflow = PathScope.WORKFLOW_RELATIVE
    workspace = PathScope.WORKSPACE_ROOT
    module = PathScope.MODULE_RELATIVE
    generated = PathScope.GENERATED_STAGING
    deployment = PathScope.DEPLOYMENT_DERIVED
    page_inputs = (
        "page",
        "section",
        "action",
        "workflow",
    )
    module_inputs = (
        "module",
        "action",
        "capability",
        "permission",
        "event",
        "reaction",
        "notification",
    )
    data_inputs = (
        "data_collection",
        "data_alias",
        "module",
    )
    subscription_inputs = (
        "plan",
        "product",
        "meter",
        "limit",
        "capability",
    )
    workflow_inputs = (
        "workflow",
        "trigger",
        "event",
        "capability",
    )
    application_inputs = ("application",)
    auth_inputs = ("auth",)
    integration_inputs = ("integration",)
    return (
        _family(ArtifactKind.APP_MANIFEST, LayoutOwner.PLATFORM, Requirement.REQUIRED, app, "app.json", ValidatorIdentifier.APP_LOADER, RuntimeConsumerIdentifier.APP_LOADER, inputs=application_inputs),
        _family(ArtifactKind.APP_CONFIG, LayoutOwner.PLATFORM, Requirement.OPTIONAL, app, "config/ai.json", ValidatorIdentifier.APP_PATHS, RuntimeConsumerIdentifier.PLATFORM_HOST),
        _family(ArtifactKind.APP_AUTH_CONFIG, LayoutOwner.PLATFORM, Requirement.CONDITIONAL, app, "config/auth.yaml", ValidatorIdentifier.APP_PATHS, RuntimeConsumerIdentifier.PLATFORM_HOST, condition=ConditionIdentifier.WHEN_AUTH_ENABLED, inputs=auth_inputs),
        _family(ArtifactKind.APP_CONFIG, LayoutOwner.PLATFORM, Requirement.OPTIONAL, app, "config/asset_manifest.json", ValidatorIdentifier.APP_PATHS, RuntimeConsumerIdentifier.PLATFORM_HOST, assignment=(AssignmentKind.PAGE_BUNDLE,)),
        _family(ArtifactKind.APP_INTEGRATIONS_CONFIG, LayoutOwner.APP_WORKSPACE, Requirement.CONDITIONAL, app, "config/integrations.json", ValidatorIdentifier.APP_PATHS, RuntimeConsumerIdentifier.PLATFORM_HOST, condition=ConditionIdentifier.WHEN_APP_DECLARED, inputs=integration_inputs),
        _family(ArtifactKind.APP_INTEGRATIONS_CONFIG, LayoutOwner.APP_WORKSPACE, Requirement.CONDITIONAL, app, "config/integrations.yaml", ValidatorIdentifier.APP_PATHS, RuntimeConsumerIdentifier.PLATFORM_HOST, condition=ConditionIdentifier.WHEN_APP_DECLARED, inputs=integration_inputs),
        _family(ArtifactKind.APP_INTEGRATIONS_CONFIG, LayoutOwner.APP_WORKSPACE, Requirement.CONDITIONAL, app, "config/integrations.yml", ValidatorIdentifier.APP_PATHS, RuntimeConsumerIdentifier.PLATFORM_HOST, condition=ConditionIdentifier.WHEN_APP_DECLARED, inputs=integration_inputs),
        _family(ArtifactKind.APP_REFINEMENT_POLICY, LayoutOwner.APP_WORKSPACE, Requirement.CONDITIONAL, app, "config/refinement_policy.yaml", ValidatorIdentifier.APP_PATHS, RuntimeConsumerIdentifier.REFINEMENT_ENGINE, condition=ConditionIdentifier.WHEN_REFINEMENT_HARNESS_REQUIRED, assignment=(AssignmentKind.REFINEMENT_HARNESS,)),
        _family(ArtifactKind.APP_SHELL_CONFIG, LayoutOwner.PLATFORM, Requirement.OPTIONAL, app, "config/shell.json", ValidatorIdentifier.APP_PATHS, RuntimeConsumerIdentifier.PLATFORM_HOST, assignment=(AssignmentKind.PAGE_BUNDLE,)),
        _family(ArtifactKind.APP_SUBSCRIPTION_CONFIG, LayoutOwner.APP_WORKSPACE, Requirement.CONDITIONAL, app, "config/subscriptions.yaml", ValidatorIdentifier.SUBSCRIPTIONS_LOADER, RuntimeConsumerIdentifier.ENTITLEMENT_ADAPTER, condition=ConditionIdentifier.WHEN_SUBSCRIPTIONS_REQUIRED, assignment=(AssignmentKind.SUBSCRIPTION_CONFIG,), inputs=subscription_inputs),
        _family(ArtifactKind.APP_TARGETS_CONFIG, LayoutOwner.APP_WORKSPACE, Requirement.OPTIONAL, app, "config/targets.json", ValidatorIdentifier.APP_PATHS, RuntimeConsumerIdentifier.PLATFORM_HOST),
        _family(ArtifactKind.APP_DATA_CONTRACT, LayoutOwner.APP_WORKSPACE, Requirement.CONDITIONAL, app, "data/contract.json", ValidatorIdentifier.DATA_CONTRACT_LOADER, RuntimeConsumerIdentifier.APP_LOADER, condition=ConditionIdentifier.WHEN_DATA_CONTRACT_REQUIRED, assignment=(AssignmentKind.PERSISTENCE_CONTRACT,), inputs=data_inputs),
        _family(ArtifactKind.APP_DATA_MIGRATION, LayoutOwner.APP_WORKSPACE, Requirement.CONDITIONAL, app, "data/migrations/{migration_id}.json", ValidatorIdentifier.DATA_CONTRACT_LOADER, RuntimeConsumerIdentifier.APP_LOADER, condition=ConditionIdentifier.WHEN_DATA_CONTRACT_REQUIRED, multiplicity=Multiplicity.MANY, assignment=(AssignmentKind.DATA_MIGRATIONS,)),
        _family(ArtifactKind.APP_SECRET_REFERENCES, LayoutOwner.APP_WORKSPACE, Requirement.CONDITIONAL, app, "security/secrets.yaml", ValidatorIdentifier.GENERATED_APP_VALIDATOR, RuntimeConsumerIdentifier.PLATFORM_HOST, condition=ConditionIdentifier.WHEN_APP_DECLARED, security=SecurityClass.SECRET_REFERENCE_NAMES, assignment=(AssignmentKind.SERVICE_FOUNDATION,)),
        _family(ArtifactKind.APP_PROVENANCE, LayoutOwner.FACTORY, Requirement.OPTIONAL, app, "provenance.yaml", ValidatorIdentifier.PROVENANCE_LOADER, RuntimeConsumerIdentifier.PROVENANCE_LOADER, assignment=(AssignmentKind.PAGE_BUNDLE,)),
        _family(ArtifactKind.APP_BRAND_THEME, LayoutOwner.APP_WORKSPACE, Requirement.OPTIONAL, app, "brand/theme_config.json", ValidatorIdentifier.GENERATED_APP_VALIDATOR, RuntimeConsumerIdentifier.PLATFORM_HOST, assignment=(AssignmentKind.PAGE_BUNDLE,)),
        _family(ArtifactKind.APP_DASHBOARD, LayoutOwner.PLATFORM, Requirement.OPTIONAL, app, "dashboard/dashboard.yaml", ValidatorIdentifier.GENERATED_APP_VALIDATOR, RuntimeConsumerIdentifier.PLATFORM_HOST, assignment=(AssignmentKind.PAGE_BUNDLE,)),
        _family(ArtifactKind.APP_UI_ROUTE_MANIFEST, LayoutOwner.PLATFORM, Requirement.OPTIONAL, app, "ui/route_manifest.json", ValidatorIdentifier.GENERATED_APP_VALIDATOR, RuntimeConsumerIdentifier.PLATFORM_HOST, assignment=(AssignmentKind.PAGE_BUNDLE,), deps=(ArtifactKind.APP_UI_CUSTOM_ROUTE, ArtifactKind.APP_UI_PAGE_SCHEMA)),
        _family(ArtifactKind.APP_UI_PAGE_SCHEMA, LayoutOwner.APP_WORKSPACE, Requirement.CONDITIONAL, app, "ui/pages/{page_id}.yaml", ValidatorIdentifier.GENERATED_APP_VALIDATOR, RuntimeConsumerIdentifier.PLATFORM_HOST, condition=ConditionIdentifier.WHEN_PAGE_DECLARED, multiplicity=Multiplicity.MANY, assignment=(AssignmentKind.PAGE_BUNDLE,), stubs=(StubKind.JS_FRONTEND,), inputs=page_inputs),
        _family(ArtifactKind.APP_UI_CUSTOM_ROUTE, LayoutOwner.APP_WORKSPACE, Requirement.CONDITIONAL, app, "ui/pages/custom/{page_id}.jsx", ValidatorIdentifier.GENERATED_APP_VALIDATOR, RuntimeConsumerIdentifier.PLATFORM_HOST, condition=ConditionIdentifier.WHEN_CUSTOM_ROUTE_DECLARED, multiplicity=Multiplicity.MANY, security=SecurityClass.EXECUTABLE_STUB, assignment=(AssignmentKind.PAGE_BUNDLE,)),
        _family(ArtifactKind.APP_UI_AUTH_ADAPTER, LayoutOwner.APP_WORKSPACE, Requirement.CONDITIONAL, app, "ui/auth/authAdapter.js", ValidatorIdentifier.GENERATED_APP_VALIDATOR, RuntimeConsumerIdentifier.PLATFORM_HOST, condition=ConditionIdentifier.WHEN_AUTH_ENABLED, security=SecurityClass.EXECUTABLE_STUB, assignment=(AssignmentKind.PAGE_BUNDLE,)),
        _family(ArtifactKind.APP_UI_EXTENSION_BARREL, LayoutOwner.APP_WORKSPACE, Requirement.CONDITIONAL, app, "ui/index.js", ValidatorIdentifier.GENERATED_APP_VALIDATOR, RuntimeConsumerIdentifier.PLATFORM_HOST, condition=ConditionIdentifier.WHEN_CUSTOM_ROUTE_DECLARED, security=SecurityClass.EXECUTABLE_STUB, assignment=(AssignmentKind.PAGE_BUNDLE,), deps=(ArtifactKind.APP_UI_CUSTOM_ROUTE,)),
        _family(ArtifactKind.APP_UI_MODULE_API, LayoutOwner.PLATFORM, Requirement.CONDITIONAL, app, "ui/lib/moduleApi.js", ValidatorIdentifier.GENERATED_APP_VALIDATOR, RuntimeConsumerIdentifier.PLATFORM_HOST, condition=ConditionIdentifier.WHEN_CUSTOM_ROUTE_DECLARED, security=SecurityClass.EXECUTABLE_STUB, assignment=(AssignmentKind.PAGE_BUNDLE,)),
        _family(ArtifactKind.APP_ADMIN_REGISTRY, LayoutOwner.PLATFORM, Requirement.OPTIONAL, app, "admin/admin_registry.yaml", ValidatorIdentifier.GENERATED_APP_VALIDATOR, RuntimeConsumerIdentifier.PLATFORM_HOST),
        _family(ArtifactKind.APP_FRAMEWORK_METADATA, LayoutOwner.FACTORY, Requirement.OPTIONAL, app, ".mozaiks/pack_provenance.json", ValidatorIdentifier.PROVENANCE_LOADER, RuntimeConsumerIdentifier.PROVENANCE_LOADER),
        _family(ArtifactKind.APP_BACKEND_SUPPORT, LayoutOwner.APP_WORKSPACE, Requirement.OPTIONAL, app, "backend/admin_config.py", ValidatorIdentifier.GENERATED_APP_VALIDATOR, RuntimeConsumerIdentifier.PLATFORM_HOST, security=SecurityClass.EXECUTABLE_STUB, assignment=(AssignmentKind.API_SURFACE,)),
        _family(ArtifactKind.APP_REFINEMENT_HARNESS, LayoutOwner.APP_WORKSPACE, Requirement.CONDITIONAL, app, "refinement_harness/config/harness.yaml", ValidatorIdentifier.GENERATED_APP_VALIDATOR, RuntimeConsumerIdentifier.REFINEMENT_ENGINE, condition=ConditionIdentifier.WHEN_REFINEMENT_HARNESS_REQUIRED, assignment=(AssignmentKind.REFINEMENT_HARNESS,)),
        _family(ArtifactKind.APP_REFINEMENT_HARNESS, LayoutOwner.APP_WORKSPACE, Requirement.CONDITIONAL, app, "refinement_harness/config/tools.yaml", ValidatorIdentifier.GENERATED_APP_VALIDATOR, RuntimeConsumerIdentifier.REFINEMENT_ENGINE, condition=ConditionIdentifier.WHEN_REFINEMENT_HARNESS_REQUIRED, assignment=(AssignmentKind.REFINEMENT_HARNESS,)),
        _family(ArtifactKind.APP_REFINEMENT_HARNESS, LayoutOwner.APP_WORKSPACE, Requirement.CONDITIONAL, app, "refinement_harness/config/policies.yaml", ValidatorIdentifier.GENERATED_APP_VALIDATOR, RuntimeConsumerIdentifier.REFINEMENT_ENGINE, condition=ConditionIdentifier.WHEN_REFINEMENT_HARNESS_REQUIRED, assignment=(AssignmentKind.REFINEMENT_HARNESS,)),
        _family(ArtifactKind.APP_REFINEMENT_HARNESS, LayoutOwner.APP_WORKSPACE, Requirement.CONDITIONAL, app, "refinement_harness/prompts/{pack_id}.yaml", ValidatorIdentifier.GENERATED_APP_VALIDATOR, RuntimeConsumerIdentifier.REFINEMENT_ENGINE, condition=ConditionIdentifier.WHEN_REFINEMENT_HARNESS_REQUIRED, multiplicity=Multiplicity.MANY, assignment=(AssignmentKind.REFINEMENT_HARNESS,)),
        _family(ArtifactKind.MODULE_MANIFEST, LayoutOwner.MODULE, Requirement.CONDITIONAL, app, "modules/{module_id}/module.yaml", ValidatorIdentifier.MODULE_LOADER, RuntimeConsumerIdentifier.MODULE_LOADER, condition=ConditionIdentifier.WHEN_MODULE_DECLARED, multiplicity=Multiplicity.MANY, assignment=(AssignmentKind.MODULE_CONTRACT,), stubs=(StubKind.PYTHON_BACKEND,), inputs=module_inputs),
        _family(ArtifactKind.MODULE_CONTRACT, LayoutOwner.MODULE, Requirement.CONDITIONAL, app, "modules/{module_id}/contracts/events.yaml", ValidatorIdentifier.MODULE_LOADER, RuntimeConsumerIdentifier.MODULE_EVENT_ROUTER, condition=ConditionIdentifier.WHEN_MODULE_DECLARED, multiplicity=Multiplicity.MANY, assignment=(AssignmentKind.MODULE_CONTRACT,), deps=(ArtifactKind.MODULE_MANIFEST,)),
        _family(ArtifactKind.MODULE_CONTRACT, LayoutOwner.MODULE, Requirement.CONDITIONAL, app, "modules/{module_id}/contracts/reactions.yaml", ValidatorIdentifier.MODULE_LOADER, RuntimeConsumerIdentifier.MODULE_EVENT_ROUTER, condition=ConditionIdentifier.WHEN_MODULE_DECLARED, multiplicity=Multiplicity.MANY, assignment=(AssignmentKind.MODULE_CONTRACT,), deps=(ArtifactKind.MODULE_MANIFEST,)),
        _family(ArtifactKind.MODULE_CONTRACT, LayoutOwner.MODULE, Requirement.CONDITIONAL, app, "modules/{module_id}/contracts/notifications.yaml", ValidatorIdentifier.MODULE_LOADER, RuntimeConsumerIdentifier.MODULE_EVENT_ROUTER, condition=ConditionIdentifier.WHEN_MODULE_DECLARED, multiplicity=Multiplicity.MANY, assignment=(AssignmentKind.MODULE_CONTRACT,), deps=(ArtifactKind.MODULE_MANIFEST,)),
        _family(ArtifactKind.MODULE_CONTRACT, LayoutOwner.MODULE, Requirement.CONDITIONAL, app, "modules/{module_id}/contracts/policy_hooks.yaml", ValidatorIdentifier.MODULE_LOADER, RuntimeConsumerIdentifier.MODULE_EXECUTOR, condition=ConditionIdentifier.WHEN_MODULE_DECLARED, multiplicity=Multiplicity.MANY, assignment=(AssignmentKind.MODULE_CONTRACT,), deps=(ArtifactKind.MODULE_MANIFEST,)),
        _family(ArtifactKind.MODULE_CONTRACT, LayoutOwner.MODULE, Requirement.CONDITIONAL, app, "modules/{module_id}/contracts/settings.yaml", ValidatorIdentifier.MODULE_LOADER, RuntimeConsumerIdentifier.MODULE_EXECUTOR, condition=ConditionIdentifier.WHEN_MODULE_DECLARED, multiplicity=Multiplicity.MANY, assignment=(AssignmentKind.MODULE_CONTRACT,), deps=(ArtifactKind.MODULE_MANIFEST,)),
        _family(ArtifactKind.MODULE_CONTRACT, LayoutOwner.MODULE, Requirement.CONDITIONAL, app, "modules/{module_id}/contracts/admin.yaml", ValidatorIdentifier.MODULE_LOADER, RuntimeConsumerIdentifier.PLATFORM_HOST, condition=ConditionIdentifier.WHEN_MODULE_DECLARED, multiplicity=Multiplicity.MANY, assignment=(AssignmentKind.MODULE_CONTRACT,), deps=(ArtifactKind.MODULE_MANIFEST,)),
        _family(ArtifactKind.MODULE_CONTRACT, LayoutOwner.MODULE, Requirement.CONDITIONAL, app, "modules/{module_id}/contracts/profile.yaml", ValidatorIdentifier.MODULE_LOADER, RuntimeConsumerIdentifier.PLATFORM_HOST, condition=ConditionIdentifier.WHEN_MODULE_DECLARED, multiplicity=Multiplicity.MANY, assignment=(AssignmentKind.MODULE_CONTRACT,), deps=(ArtifactKind.MODULE_MANIFEST,)),
        _family(ArtifactKind.MODULE_CONTRACT, LayoutOwner.MODULE, Requirement.CONDITIONAL, app, "modules/{module_id}/contracts/relationships.yaml", ValidatorIdentifier.MODULE_LOADER, RuntimeConsumerIdentifier.PLATFORM_HOST, condition=ConditionIdentifier.WHEN_MODULE_DECLARED, multiplicity=Multiplicity.MANY, assignment=(AssignmentKind.MODULE_CONTRACT,), deps=(ArtifactKind.MODULE_MANIFEST,)),
        _family(ArtifactKind.MODULE_CONTRACT, LayoutOwner.MODULE, Requirement.CONDITIONAL, app, "modules/{module_id}/contracts/service.yaml", ValidatorIdentifier.MODULE_LOADER, RuntimeConsumerIdentifier.PLATFORM_HOST, condition=ConditionIdentifier.WHEN_MODULE_DECLARED, multiplicity=Multiplicity.MANY, assignment=(AssignmentKind.MODULE_CONTRACT,), deps=(ArtifactKind.MODULE_MANIFEST,)),
        _family(ArtifactKind.MODULE_CONTRACT, LayoutOwner.MODULE, Requirement.CONDITIONAL, app, "modules/{module_id}/contracts/commercial.yaml", ValidatorIdentifier.MODULE_LOADER, RuntimeConsumerIdentifier.PLATFORM_HOST, condition=ConditionIdentifier.WHEN_MODULE_DECLARED, multiplicity=Multiplicity.MANY, assignment=(AssignmentKind.MODULE_CONTRACT,), deps=(ArtifactKind.MODULE_MANIFEST,)),
        _family(ArtifactKind.MODULE_RUNTIME_EXTENSIONS, LayoutOwner.MODULE, Requirement.CONDITIONAL, app, "modules/{module_id}/runtime_extensions.yaml", ValidatorIdentifier.MODULE_LOADER, RuntimeConsumerIdentifier.PLATFORM_HOST, condition=ConditionIdentifier.WHEN_MODULE_DECLARED, multiplicity=Multiplicity.MANY, assignment=(AssignmentKind.MODULE_CONTRACT,), deps=(ArtifactKind.MODULE_MANIFEST,)),
        _family(ArtifactKind.MODULE_BACKEND_HANDLER, LayoutOwner.MODULE, Requirement.CONDITIONAL, app, "modules/{module_id}/backend/handler.py", ValidatorIdentifier.MODULE_LOADER, RuntimeConsumerIdentifier.MODULE_EXECUTOR, condition=ConditionIdentifier.WHEN_MODULE_DECLARED, multiplicity=Multiplicity.MANY, security=SecurityClass.EXECUTABLE_STUB, assignment=(AssignmentKind.BUSINESS_SERVICES,), deps=(ArtifactKind.MODULE_MANIFEST,)),
        _family(ArtifactKind.MODULE_BACKEND_API, LayoutOwner.MODULE, Requirement.OPTIONAL, app, "modules/{module_id}/backend/api.py", ValidatorIdentifier.MODULE_LOADER, RuntimeConsumerIdentifier.PLATFORM_HOST, multiplicity=Multiplicity.MANY, security=SecurityClass.EXECUTABLE_STUB, assignment=(AssignmentKind.API_SURFACE,), deps=(ArtifactKind.MODULE_MANIFEST,)),
        _family(ArtifactKind.MODULE_BACKEND_BASE_HANDLER, LayoutOwner.MODULE, Requirement.OPTIONAL, app, "modules/{module_id}/backend/base_handler.py", ValidatorIdentifier.MODULE_LOADER, RuntimeConsumerIdentifier.MODULE_EXECUTOR, multiplicity=Multiplicity.MANY, security=SecurityClass.EXECUTABLE_STUB, deps=(ArtifactKind.MODULE_MANIFEST,)),
        _family(ArtifactKind.MODULE_BACKEND_SERVICE, LayoutOwner.MODULE, Requirement.CONDITIONAL, app, "modules/{module_id}/backend/service.py", ValidatorIdentifier.MODULE_LOADER, RuntimeConsumerIdentifier.MODULE_EXECUTOR, condition=ConditionIdentifier.WHEN_MODULE_DECLARED, multiplicity=Multiplicity.MANY, security=SecurityClass.EXECUTABLE_STUB, assignment=(AssignmentKind.BUSINESS_SERVICES,), deps=(ArtifactKind.MODULE_MANIFEST,)),
        _family(ArtifactKind.MODULE_BACKEND_REPO, LayoutOwner.MODULE, Requirement.CONDITIONAL, app, "modules/{module_id}/backend/repo.py", ValidatorIdentifier.MODULE_LOADER, RuntimeConsumerIdentifier.MODULE_EXECUTOR, condition=ConditionIdentifier.WHEN_MODULE_DECLARED, multiplicity=Multiplicity.MANY, security=SecurityClass.EXECUTABLE_STUB, assignment=(AssignmentKind.BUSINESS_SERVICES,), deps=(ArtifactKind.MODULE_MANIFEST,)),
        _family(ArtifactKind.MODULE_BACKEND_POLICY, LayoutOwner.MODULE, Requirement.CONDITIONAL, app, "modules/{module_id}/backend/policy.py", ValidatorIdentifier.MODULE_LOADER, RuntimeConsumerIdentifier.MODULE_EXECUTOR, condition=ConditionIdentifier.WHEN_MODULE_DECLARED, multiplicity=Multiplicity.MANY, security=SecurityClass.EXECUTABLE_STUB, assignment=(AssignmentKind.BUSINESS_SERVICES,), deps=(ArtifactKind.MODULE_MANIFEST,)),
        _family(ArtifactKind.MODULE_BACKEND_SCHEMAS, LayoutOwner.MODULE, Requirement.CONDITIONAL, app, "modules/{module_id}/backend/schemas.py", ValidatorIdentifier.MODULE_LOADER, RuntimeConsumerIdentifier.MODULE_EXECUTOR, condition=ConditionIdentifier.WHEN_MODULE_DECLARED, multiplicity=Multiplicity.MANY, security=SecurityClass.EXECUTABLE_STUB, assignment=(AssignmentKind.DATA_MODELS,), deps=(ArtifactKind.MODULE_MANIFEST,)),
        _family(ArtifactKind.MODULE_BACKEND_HELPER, LayoutOwner.MODULE, Requirement.CONDITIONAL, app, "modules/{module_id}/backend/{helper_id}.py", ValidatorIdentifier.MODULE_LOADER, RuntimeConsumerIdentifier.MODULE_EXECUTOR, condition=ConditionIdentifier.WHEN_MODULE_DECLARED, multiplicity=Multiplicity.MANY, security=SecurityClass.EXECUTABLE_STUB, deps=(ArtifactKind.MODULE_MANIFEST,)),
        _family(ArtifactKind.MODULE_ADMIN_UI, LayoutOwner.MODULE, Requirement.OPTIONAL, app, "modules/{module_id}/ui/admin/{page_id}.jsx", ValidatorIdentifier.GENERATED_APP_VALIDATOR, RuntimeConsumerIdentifier.PLATFORM_HOST, multiplicity=Multiplicity.MANY, security=SecurityClass.EXECUTABLE_STUB, deps=(ArtifactKind.MODULE_MANIFEST,)),
        _family(ArtifactKind.MODULE_UI_EXTENSION_BARREL, LayoutOwner.MODULE, Requirement.OPTIONAL, app, "modules/{module_id}/ui/index.js", ValidatorIdentifier.GENERATED_APP_VALIDATOR, RuntimeConsumerIdentifier.PLATFORM_HOST, multiplicity=Multiplicity.MANY, security=SecurityClass.EXECUTABLE_STUB, deps=(ArtifactKind.MODULE_MANIFEST,)),
        _family(ArtifactKind.APP_SERVICE_SUPPORT, LayoutOwner.APP_WORKSPACE, Requirement.OPTIONAL, app, "services/config.py", ValidatorIdentifier.GENERATED_APP_VALIDATOR, RuntimeConsumerIdentifier.PLATFORM_HOST, security=SecurityClass.EXECUTABLE_STUB, assignment=(AssignmentKind.SERVICE_FOUNDATION,)),
        _family(ArtifactKind.APP_SERVICE_INTEGRATION_CLIENT, LayoutOwner.APP_WORKSPACE, Requirement.CONDITIONAL, app, "services/integrations/{pack_id}_client.py", ValidatorIdentifier.GENERATED_APP_VALIDATOR, RuntimeConsumerIdentifier.PLATFORM_HOST, condition=ConditionIdentifier.WHEN_MANAGED_CAPABILITY_SELECTED, multiplicity=Multiplicity.MANY, security=SecurityClass.EXECUTABLE_STUB, assignment=(AssignmentKind.SERVICE_FOUNDATION, AssignmentKind.API_SURFACE)),
        _family(ArtifactKind.APP_SERVICE_ROUTE, LayoutOwner.APP_WORKSPACE, Requirement.OPTIONAL, app, "services/routes/{pack_id}.py", ValidatorIdentifier.GENERATED_APP_VALIDATOR, RuntimeConsumerIdentifier.PLATFORM_HOST, multiplicity=Multiplicity.MANY, security=SecurityClass.EXECUTABLE_STUB, assignment=(AssignmentKind.SERVICE_FOUNDATION, AssignmentKind.API_SURFACE)),
        _family(ArtifactKind.APP_SERVICE_ADAPTER, LayoutOwner.APP_WORKSPACE, Requirement.OPTIONAL, app, "services/adapters/{adapter_area}/{pack_id}.py", ValidatorIdentifier.GENERATED_APP_VALIDATOR, RuntimeConsumerIdentifier.PLATFORM_HOST, multiplicity=Multiplicity.MANY, security=SecurityClass.EXECUTABLE_STUB, assignment=(AssignmentKind.SERVICE_FOUNDATION,)),
        *(_family(ArtifactKind.APP_ROOT_SUPPORT, LayoutOwner.APP_WORKSPACE, Requirement.OPTIONAL, app, path, ValidatorIdentifier.APP_PATHS, RuntimeConsumerIdentifier.PLATFORM_HOST) for path in _APP_ROOT_SUPPORT_FILES),
        _family(ArtifactKind.APP_DEPLOYMENT_ARTIFACT, LayoutOwner.DOWNLOAD_RENDERER, Requirement.GENERATED, deployment, "Dockerfile", ValidatorIdentifier.GENERATED_APP_VALIDATOR, RuntimeConsumerIdentifier.DOWNLOAD_EXPORT, condition=ConditionIdentifier.WHEN_DEPLOYMENT_EXPORT_REQUESTED, security=SecurityClass.DEPLOYMENT_METADATA),
        _family(ArtifactKind.APP_DEPLOYMENT_ARTIFACT, LayoutOwner.DOWNLOAD_RENDERER, Requirement.GENERATED, deployment, "docker-compose.yml", ValidatorIdentifier.GENERATED_APP_VALIDATOR, RuntimeConsumerIdentifier.DOWNLOAD_EXPORT, condition=ConditionIdentifier.WHEN_DEPLOYMENT_EXPORT_REQUESTED, security=SecurityClass.DEPLOYMENT_METADATA),
        _family(ArtifactKind.APP_DEPLOYMENT_ARTIFACT, LayoutOwner.DOWNLOAD_RENDERER, Requirement.GENERATED, deployment, ".env.example", ValidatorIdentifier.GENERATED_APP_VALIDATOR, RuntimeConsumerIdentifier.DOWNLOAD_EXPORT, condition=ConditionIdentifier.WHEN_DEPLOYMENT_EXPORT_REQUESTED, security=SecurityClass.DEPLOYMENT_METADATA),
        _family(ArtifactKind.APP_DEPLOYMENT_ARTIFACT, LayoutOwner.DOWNLOAD_RENDERER, Requirement.GENERATED, deployment, ".env.staging.example", ValidatorIdentifier.GENERATED_APP_VALIDATOR, RuntimeConsumerIdentifier.DOWNLOAD_EXPORT, condition=ConditionIdentifier.WHEN_DEPLOYMENT_EXPORT_REQUESTED, security=SecurityClass.DEPLOYMENT_METADATA),
        _family(ArtifactKind.APP_DEPLOYMENT_ARTIFACT, LayoutOwner.DOWNLOAD_RENDERER, Requirement.GENERATED, deployment, ".env.production.example", ValidatorIdentifier.GENERATED_APP_VALIDATOR, RuntimeConsumerIdentifier.DOWNLOAD_EXPORT, condition=ConditionIdentifier.WHEN_DEPLOYMENT_EXPORT_REQUESTED, security=SecurityClass.DEPLOYMENT_METADATA),
        _family(ArtifactKind.APP_DEPLOYMENT_ARTIFACT, LayoutOwner.DOWNLOAD_RENDERER, Requirement.GENERATED, deployment, "deployment.manifest.json", ValidatorIdentifier.GENERATED_APP_VALIDATOR, RuntimeConsumerIdentifier.DOWNLOAD_EXPORT, condition=ConditionIdentifier.WHEN_DEPLOYMENT_EXPORT_REQUESTED, security=SecurityClass.DEPLOYMENT_METADATA),
        _family(ArtifactKind.APP_DEPLOYMENT_ARTIFACT, LayoutOwner.DOWNLOAD_RENDERER, Requirement.GENERATED, deployment, ".github/workflows/deploy.yml", ValidatorIdentifier.GENERATED_APP_VALIDATOR, RuntimeConsumerIdentifier.DOWNLOAD_EXPORT, condition=ConditionIdentifier.WHEN_DEPLOYMENT_EXPORT_REQUESTED, security=SecurityClass.DEPLOYMENT_METADATA),
        _family(ArtifactKind.APP_DEPLOYMENT_ARTIFACT, LayoutOwner.DOWNLOAD_RENDERER, Requirement.GENERATED, deployment, ".github/workflows/readiness.yml", ValidatorIdentifier.GENERATED_APP_VALIDATOR, RuntimeConsumerIdentifier.DOWNLOAD_EXPORT, condition=ConditionIdentifier.WHEN_DEPLOYMENT_EXPORT_REQUESTED, security=SecurityClass.DEPLOYMENT_METADATA),
        _family(ArtifactKind.APP_MANIFEST, LayoutOwner.PLATFORM, Requirement.REQUIRED, workspace, "app/app.json", ValidatorIdentifier.APP_LOADER, RuntimeConsumerIdentifier.APP_LOADER, inputs=application_inputs),
        _family(ArtifactKind.WORKFLOW_MANIFEST, LayoutOwner.WORKFLOW, Requirement.CONDITIONAL, workspace, "workflows/{workflow_id}/orchestrator.yaml", ValidatorIdentifier.WORKFLOW_MANAGER, RuntimeConsumerIdentifier.WORKFLOW_MANAGER, condition=ConditionIdentifier.WHEN_WORKFLOW_DECLARED, multiplicity=Multiplicity.MANY, inputs=workflow_inputs),
        _family(ArtifactKind.BUILD_CONTEXT_REGISTRY, LayoutOwner.CAPABILITY_PACK, Requirement.OPTIONAL, workspace, "build_context/{pack_id}/context.yaml", ValidatorIdentifier.GENERATED_APP_VALIDATOR, RuntimeConsumerIdentifier.NONE, multiplicity=Multiplicity.MANY),
        _family(ArtifactKind.WORKFLOW_MANIFEST, LayoutOwner.WORKFLOW, Requirement.CONDITIONAL, workflow, "orchestrator.yaml", ValidatorIdentifier.WORKFLOW_MANAGER, RuntimeConsumerIdentifier.WORKFLOW_MANAGER, condition=ConditionIdentifier.WHEN_WORKFLOW_DECLARED, inputs=workflow_inputs),
        _family(ArtifactKind.WORKFLOW_CONFIG, LayoutOwner.WORKFLOW, Requirement.OPTIONAL, workflow, "agents.yaml", ValidatorIdentifier.WORKFLOW_MANAGER, RuntimeConsumerIdentifier.WORKFLOW_MANAGER, deps=(ArtifactKind.WORKFLOW_MANIFEST,)),
        _family(ArtifactKind.WORKFLOW_CONFIG, LayoutOwner.WORKFLOW, Requirement.OPTIONAL, workflow, "context_variables.yaml", ValidatorIdentifier.WORKFLOW_MANAGER, RuntimeConsumerIdentifier.WORKFLOW_MANAGER, deps=(ArtifactKind.WORKFLOW_MANIFEST,)),
        _family(ArtifactKind.WORKFLOW_CONFIG, LayoutOwner.WORKFLOW, Requirement.OPTIONAL, workflow, "structured_outputs.yaml", ValidatorIdentifier.WORKFLOW_MANAGER, RuntimeConsumerIdentifier.WORKFLOW_MANAGER, deps=(ArtifactKind.WORKFLOW_MANIFEST,)),
        _family(ArtifactKind.WORKFLOW_CONFIG, LayoutOwner.WORKFLOW, Requirement.OPTIONAL, workflow, "transition_graph.yaml", ValidatorIdentifier.WORKFLOW_MANAGER, RuntimeConsumerIdentifier.WORKFLOW_MANAGER, deps=(ArtifactKind.WORKFLOW_MANIFEST,)),
        _family(ArtifactKind.WORKFLOW_CONFIG, LayoutOwner.WORKFLOW, Requirement.OPTIONAL, workflow, "tools.yaml", ValidatorIdentifier.WORKFLOW_MANAGER, RuntimeConsumerIdentifier.WORKFLOW_MANAGER, deps=(ArtifactKind.WORKFLOW_MANIFEST,), stubs=(StubKind.PYTHON_BACKEND,)),
        _family(ArtifactKind.WORKFLOW_CONFIG, LayoutOwner.WORKFLOW, Requirement.OPTIONAL, workflow, "ui_config.yaml", ValidatorIdentifier.WORKFLOW_MANAGER, RuntimeConsumerIdentifier.WORKFLOW_MANAGER, deps=(ArtifactKind.WORKFLOW_MANIFEST,), stubs=(StubKind.JS_FRONTEND,)),
        _family(ArtifactKind.WORKFLOW_CONFIG, LayoutOwner.WORKFLOW, Requirement.OPTIONAL, workflow, "middleware.yaml", ValidatorIdentifier.WORKFLOW_MANAGER, RuntimeConsumerIdentifier.WORKFLOW_MANAGER, deps=(ArtifactKind.WORKFLOW_MANIFEST,)),
        _family(ArtifactKind.WORKFLOW_TASK_BATCH, LayoutOwner.WORKFLOW, Requirement.OPTIONAL, workflow, "extended_orchestration/task_batches.yaml", ValidatorIdentifier.WORKFLOW_MANAGER, RuntimeConsumerIdentifier.WORKFLOW_MANAGER, deps=(ArtifactKind.WORKFLOW_MANIFEST,)),
        _family(ArtifactKind.WORKFLOW_TOOL, LayoutOwner.WORKFLOW, Requirement.OPTIONAL, workflow, "tools/{tool_id}.py", ValidatorIdentifier.WORKFLOW_MANAGER, RuntimeConsumerIdentifier.WORKFLOW_MANAGER, multiplicity=Multiplicity.MANY, security=SecurityClass.EXECUTABLE_STUB, deps=(ArtifactKind.WORKFLOW_MANIFEST,)),
        _family(ArtifactKind.WORKFLOW_UI, LayoutOwner.WORKFLOW, Requirement.OPTIONAL, workflow, "ui/{workflow_id}/{component_id}.jsx", ValidatorIdentifier.WORKFLOW_MANAGER, RuntimeConsumerIdentifier.WORKFLOW_MANAGER, multiplicity=Multiplicity.MANY, security=SecurityClass.EXECUTABLE_STUB, deps=(ArtifactKind.WORKFLOW_MANIFEST,)),
        _family(ArtifactKind.MODULE_MANIFEST, LayoutOwner.MODULE, Requirement.REQUIRED, module, "module.yaml", ValidatorIdentifier.MODULE_LOADER, RuntimeConsumerIdentifier.MODULE_LOADER, condition=ConditionIdentifier.WHEN_MODULE_DECLARED, assignment=(AssignmentKind.MODULE_CONTRACT,), stubs=(StubKind.PYTHON_BACKEND,)),
        _family(ArtifactKind.MODULE_CONTRACT, LayoutOwner.MODULE, Requirement.OPTIONAL, module, "contracts/events.yaml", ValidatorIdentifier.MODULE_LOADER, RuntimeConsumerIdentifier.MODULE_LOADER, assignment=(AssignmentKind.MODULE_CONTRACT,), deps=(ArtifactKind.MODULE_MANIFEST,)),
        _family(ArtifactKind.MODULE_CONTRACT, LayoutOwner.MODULE, Requirement.OPTIONAL, module, "contracts/reactions.yaml", ValidatorIdentifier.MODULE_LOADER, RuntimeConsumerIdentifier.MODULE_LOADER, assignment=(AssignmentKind.MODULE_CONTRACT,), deps=(ArtifactKind.MODULE_MANIFEST,)),
        _family(ArtifactKind.MODULE_CONTRACT, LayoutOwner.MODULE, Requirement.OPTIONAL, module, "contracts/notifications.yaml", ValidatorIdentifier.MODULE_LOADER, RuntimeConsumerIdentifier.MODULE_LOADER, assignment=(AssignmentKind.MODULE_CONTRACT,), deps=(ArtifactKind.MODULE_MANIFEST,)),
        _family(ArtifactKind.MODULE_CONTRACT, LayoutOwner.MODULE, Requirement.OPTIONAL, module, "contracts/policy_hooks.yaml", ValidatorIdentifier.MODULE_LOADER, RuntimeConsumerIdentifier.MODULE_LOADER, assignment=(AssignmentKind.MODULE_CONTRACT,), deps=(ArtifactKind.MODULE_MANIFEST,)),
        _family(ArtifactKind.MODULE_CONTRACT, LayoutOwner.MODULE, Requirement.OPTIONAL, module, "contracts/settings.yaml", ValidatorIdentifier.MODULE_LOADER, RuntimeConsumerIdentifier.MODULE_LOADER, assignment=(AssignmentKind.MODULE_CONTRACT,), deps=(ArtifactKind.MODULE_MANIFEST,)),
        _family(ArtifactKind.MODULE_CONTRACT, LayoutOwner.MODULE, Requirement.OPTIONAL, module, "contracts/admin.yaml", ValidatorIdentifier.MODULE_LOADER, RuntimeConsumerIdentifier.MODULE_LOADER, assignment=(AssignmentKind.MODULE_CONTRACT,), deps=(ArtifactKind.MODULE_MANIFEST,)),
        _family(ArtifactKind.MODULE_CONTRACT, LayoutOwner.MODULE, Requirement.OPTIONAL, module, "contracts/profile.yaml", ValidatorIdentifier.MODULE_LOADER, RuntimeConsumerIdentifier.MODULE_LOADER, assignment=(AssignmentKind.MODULE_CONTRACT,), deps=(ArtifactKind.MODULE_MANIFEST,)),
        _family(ArtifactKind.MODULE_CONTRACT, LayoutOwner.MODULE, Requirement.OPTIONAL, module, "contracts/relationships.yaml", ValidatorIdentifier.MODULE_LOADER, RuntimeConsumerIdentifier.MODULE_LOADER, assignment=(AssignmentKind.MODULE_CONTRACT,), deps=(ArtifactKind.MODULE_MANIFEST,)),
        _family(ArtifactKind.MODULE_CONTRACT, LayoutOwner.MODULE, Requirement.OPTIONAL, module, "contracts/service.yaml", ValidatorIdentifier.MODULE_LOADER, RuntimeConsumerIdentifier.MODULE_LOADER, assignment=(AssignmentKind.MODULE_CONTRACT,), deps=(ArtifactKind.MODULE_MANIFEST,)),
        _family(ArtifactKind.MODULE_CONTRACT, LayoutOwner.MODULE, Requirement.OPTIONAL, module, "contracts/commercial.yaml", ValidatorIdentifier.MODULE_LOADER, RuntimeConsumerIdentifier.MODULE_LOADER, assignment=(AssignmentKind.MODULE_CONTRACT,), deps=(ArtifactKind.MODULE_MANIFEST,)),
        _prohibited(module, "contracts/subscriptions.yaml"),
        _family(ArtifactKind.MODULE_RUNTIME_EXTENSIONS, LayoutOwner.MODULE, Requirement.OPTIONAL, module, "runtime_extensions.yaml", ValidatorIdentifier.MODULE_LOADER, RuntimeConsumerIdentifier.PLATFORM_HOST, assignment=(AssignmentKind.MODULE_CONTRACT,), deps=(ArtifactKind.MODULE_MANIFEST,)),
        _family(ArtifactKind.MODULE_BACKEND_HANDLER, LayoutOwner.MODULE, Requirement.REQUIRED, module, "backend/handler.py", ValidatorIdentifier.MODULE_LOADER, RuntimeConsumerIdentifier.MODULE_EXECUTOR, security=SecurityClass.EXECUTABLE_STUB, assignment=(AssignmentKind.BUSINESS_SERVICES,), deps=(ArtifactKind.MODULE_MANIFEST,)),
        _family(ArtifactKind.MODULE_BACKEND_API, LayoutOwner.MODULE, Requirement.OPTIONAL, module, "backend/api.py", ValidatorIdentifier.MODULE_LOADER, RuntimeConsumerIdentifier.PLATFORM_HOST, security=SecurityClass.EXECUTABLE_STUB, assignment=(AssignmentKind.API_SURFACE,), deps=(ArtifactKind.MODULE_MANIFEST,)),
        _family(ArtifactKind.MODULE_BACKEND_SERVICE, LayoutOwner.MODULE, Requirement.OPTIONAL, module, "backend/service.py", ValidatorIdentifier.MODULE_LOADER, RuntimeConsumerIdentifier.MODULE_EXECUTOR, security=SecurityClass.EXECUTABLE_STUB, assignment=(AssignmentKind.BUSINESS_SERVICES,), deps=(ArtifactKind.MODULE_MANIFEST,)),
        _family(ArtifactKind.MODULE_BACKEND_REPO, LayoutOwner.MODULE, Requirement.OPTIONAL, module, "backend/repo.py", ValidatorIdentifier.MODULE_LOADER, RuntimeConsumerIdentifier.MODULE_EXECUTOR, security=SecurityClass.EXECUTABLE_STUB, assignment=(AssignmentKind.BUSINESS_SERVICES,), deps=(ArtifactKind.MODULE_MANIFEST,)),
        _family(ArtifactKind.MODULE_BACKEND_POLICY, LayoutOwner.MODULE, Requirement.OPTIONAL, module, "backend/policy.py", ValidatorIdentifier.MODULE_LOADER, RuntimeConsumerIdentifier.MODULE_EXECUTOR, security=SecurityClass.EXECUTABLE_STUB, assignment=(AssignmentKind.BUSINESS_SERVICES,), deps=(ArtifactKind.MODULE_MANIFEST,)),
        _family(ArtifactKind.MODULE_BACKEND_SCHEMAS, LayoutOwner.MODULE, Requirement.OPTIONAL, module, "backend/schemas.py", ValidatorIdentifier.MODULE_LOADER, RuntimeConsumerIdentifier.MODULE_EXECUTOR, security=SecurityClass.EXECUTABLE_STUB, assignment=(AssignmentKind.DATA_MODELS,), deps=(ArtifactKind.MODULE_MANIFEST,)),
        _family(ArtifactKind.MODULE_BACKEND_HELPER, LayoutOwner.MODULE, Requirement.OPTIONAL, module, "backend/{helper_id}.py", ValidatorIdentifier.MODULE_LOADER, RuntimeConsumerIdentifier.MODULE_EXECUTOR, multiplicity=Multiplicity.MANY, security=SecurityClass.EXECUTABLE_STUB, deps=(ArtifactKind.MODULE_MANIFEST,)),
        _family(ArtifactKind.MODULE_ADMIN_UI, LayoutOwner.MODULE, Requirement.OPTIONAL, module, "ui/admin/{page_id}.jsx", ValidatorIdentifier.GENERATED_APP_VALIDATOR, RuntimeConsumerIdentifier.PLATFORM_HOST, multiplicity=Multiplicity.MANY, security=SecurityClass.EXECUTABLE_STUB, deps=(ArtifactKind.MODULE_MANIFEST,)),
        _family(ArtifactKind.MODULE_UI_EXTENSION_BARREL, LayoutOwner.MODULE, Requirement.OPTIONAL, module, "ui/index.js", ValidatorIdentifier.GENERATED_APP_VALIDATOR, RuntimeConsumerIdentifier.PLATFORM_HOST, multiplicity=Multiplicity.MANY, security=SecurityClass.EXECUTABLE_STUB, deps=(ArtifactKind.MODULE_MANIFEST,)),
        _family(ArtifactKind.GENERATED_APP_STAGING, LayoutOwner.FACTORY, Requirement.GENERATED, generated, "generated/apps/{app_id}/{build_id}/app/app.json", ValidatorIdentifier.GENERATED_APP_VALIDATOR, RuntimeConsumerIdentifier.NONE, condition=ConditionIdentifier.WHEN_GENERATED_STAGING_SELECTED, security=SecurityClass.GENERATED_STAGING),
        _family(ArtifactKind.GENERATED_APP_STAGING, LayoutOwner.FACTORY, Requirement.GENERATED, generated, "generated/apps/{app_id}/{build_id}/app/modules/{module_id}/module.yaml", ValidatorIdentifier.GENERATED_APP_VALIDATOR, RuntimeConsumerIdentifier.NONE, condition=ConditionIdentifier.WHEN_GENERATED_STAGING_SELECTED, security=SecurityClass.GENERATED_STAGING),
        _family(ArtifactKind.GENERATED_WORKFLOW_STAGING, LayoutOwner.FACTORY, Requirement.GENERATED, generated, "generated/workflows/{app_id}/{build_id}/{workflow_id}/orchestrator.yaml", ValidatorIdentifier.GENERATED_APP_VALIDATOR, RuntimeConsumerIdentifier.NONE, condition=ConditionIdentifier.WHEN_GENERATED_STAGING_SELECTED, security=SecurityClass.GENERATED_STAGING),
        *(_prohibited(app, path) for path in _PROHIBITED_APP_BUNDLE_TEMPLATES),
    )


_APP_ROOT_SUPPORT_FILES = (
    "__init__.py",
    "index.html",
    "package-lock.json",
    "package.json",
    "pnpm-lock.yaml",
    "pyproject.toml",
    "requirements.txt",
    "tsconfig.json",
    "vite.config.js",
    "vite.config.ts",
    "yarn.lock",
)

_PROHIBITED_APP_BUNDLE_TEMPLATES = (
    "config/data.json",
    "config/llm.yaml",
    "config/secrets.yaml",
    "config/data_migrations/{migration_id}.json",
    "control_plane/{extension_id}.yaml",
    "services/data/{extension_id}.py",
    "services/security/{extension_id}.py",
    "modules/{module_id}/contracts/subscriptions.yaml",
    "modules/{module_id}/backend/models.py",
    "modules/{module_id}/backend/models/{extension_id}.py",
    "modules/{module_id}/backend/database/schema.json",
    "modules/{module_id}/backend/database/seed.json",
)


def _extension_families(extensions: tuple[LayoutExtension, ...]) -> tuple[ArtifactFamily, ...]:
    result: list[ArtifactFamily] = []
    for extension in extensions:
        if extension.slot == ExtensionSlot.MANAGED_CAPABILITY_CONFIG:
            result.append(
                _family(
                    ArtifactKind.APP_INTEGRATIONS_CONFIG,
                    LayoutOwner.REGISTERED_EXTENSION,
                    Requirement.CONDITIONAL,
                    PathScope.APP_BUNDLE_ROOT,
                    f"config/integrations/{extension.pack_id}.yaml",
                    ValidatorIdentifier.APP_PATHS,
                    RuntimeConsumerIdentifier.PLATFORM_HOST,
                    condition=ConditionIdentifier.WHEN_EXTENSION_SELECTED,
                )
            )
        elif extension.slot == ExtensionSlot.MANAGED_CAPABILITY_CLIENT:
            result.append(
                _family(
                    ArtifactKind.APP_SERVICE_INTEGRATION_CLIENT,
                    LayoutOwner.REGISTERED_EXTENSION,
                    Requirement.CONDITIONAL,
                    PathScope.APP_BUNDLE_ROOT,
                    f"services/integrations/{extension.pack_id}_client.py",
                    ValidatorIdentifier.GENERATED_APP_VALIDATOR,
                    RuntimeConsumerIdentifier.PLATFORM_HOST,
                    condition=ConditionIdentifier.WHEN_EXTENSION_SELECTED,
                    security=SecurityClass.EXECUTABLE_STUB,
                    assignment=(AssignmentKind.SERVICE_FOUNDATION, AssignmentKind.API_SURFACE),
                )
            )
        elif extension.slot == ExtensionSlot.SERVICE_ADAPTER:
            result.append(
                _family(
                    ArtifactKind.APP_SERVICE_ADAPTER,
                    LayoutOwner.REGISTERED_EXTENSION,
                    Requirement.CONDITIONAL,
                    PathScope.APP_BUNDLE_ROOT,
                    f"services/adapters/{extension.pack_id}/stub.py",
                    ValidatorIdentifier.GENERATED_APP_VALIDATOR,
                    RuntimeConsumerIdentifier.PLATFORM_HOST,
                    condition=ConditionIdentifier.WHEN_EXTENSION_SELECTED,
                    security=SecurityClass.EXECUTABLE_STUB,
                    assignment=(AssignmentKind.SERVICE_FOUNDATION,),
                )
            )
        elif extension.slot == ExtensionSlot.SERVICE_ROUTE:
            result.append(
                _family(
                    ArtifactKind.APP_SERVICE_ROUTE,
                    LayoutOwner.REGISTERED_EXTENSION,
                    Requirement.CONDITIONAL,
                    PathScope.APP_BUNDLE_ROOT,
                    f"services/routes/{extension.pack_id}.py",
                    ValidatorIdentifier.GENERATED_APP_VALIDATOR,
                    RuntimeConsumerIdentifier.PLATFORM_HOST,
                    condition=ConditionIdentifier.WHEN_EXTENSION_SELECTED,
                    security=SecurityClass.EXECUTABLE_STUB,
                    assignment=(AssignmentKind.SERVICE_FOUNDATION, AssignmentKind.API_SURFACE),
                )
            )
        elif extension.slot == ExtensionSlot.BUILD_CONTEXT_PACK:
            result.append(
                _family(
                    ArtifactKind.BUILD_CONTEXT_REGISTRY,
                    LayoutOwner.REGISTERED_EXTENSION,
                    Requirement.CONDITIONAL,
                    PathScope.WORKSPACE_ROOT,
                    f"build_context/{extension.pack_id}/context.yaml",
                    ValidatorIdentifier.GENERATED_APP_VALIDATOR,
                    RuntimeConsumerIdentifier.NONE,
                    condition=ConditionIdentifier.WHEN_EXTENSION_SELECTED,
                )
            )
        elif extension.slot == ExtensionSlot.CAPABILITY_PACK_OUTPUT:
            assert extension.path is not None
            result.append(
                _family(
                    ArtifactKind.CAPABILITY_PACK_OUTPUT,
                    LayoutOwner.CAPABILITY_PACK,
                    Requirement.CONDITIONAL,
                    _capability_pack_output_scope(extension.path),
                    extension.path,
                    ValidatorIdentifier.GENERATED_APP_VALIDATOR,
                    _capability_pack_output_consumer(extension.path),
                    condition=ConditionIdentifier.WHEN_EXTENSION_SELECTED,
                    security=_capability_pack_output_security(extension.path),
                )
            )
    return tuple(result)


# Pack contracts may declare only app-bundle interior outputs plus the two
# authentic workspace-support lanes (docs/, scripts/) proven by shipped packs
# such as operator_readiness.  Deployment artifacts stay renderer-owned,
# generated/ staging stays factory-owned, and agent/CI configuration is never
# a pack output.
_PACK_OUTPUT_FORBIDDEN_PREFIXES = (
    ".claude/",
    ".github/",
    "config/data_migrations/",
    "control_plane/",
    "generated/",
    "services/data/",
    "services/security/",
    "tests/",
)
_PACK_OUTPUT_FORBIDDEN_EXACT = frozenset(
    {
        "Dockerfile",
        "docker-compose.yml",
        ".env.example",
        ".env.staging.example",
        ".env.production.example",
        "deployment.manifest.json",
        "config/data.json",
        "config/llm.yaml",
        "config/secrets.yaml",
        "security/secrets.yaml",
    }
)
_PACK_OUTPUT_SECRET_TOKEN_RE = re.compile(
    r"(?:^|[._/-])(?:api[_-]?keys?|credentials?|passwords?|secrets?|tokens?)(?:[._/-]|$)",
    re.IGNORECASE,
)
_PACK_OUTPUT_SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".crt", ".der", ".jks")


def _validate_capability_pack_output_path(path: str) -> None:
    if _PLACEHOLDER_RE.search(path):
        raise ValueError(
            f"capability_pack_output paths must be exact literals, not templates: {path!r}"
        )
    lower = path.lower()
    if path in _PACK_OUTPUT_FORBIDDEN_EXACT or any(
        lower.startswith(prefix) for prefix in _PACK_OUTPUT_FORBIDDEN_PREFIXES
    ):
        raise ValueError(
            f"capability_pack_output path is not a permitted pack output lane: {path!r}"
        )
    basename = PurePosixPath(path).name.lower()
    if (
        basename.startswith(".env")
        or lower.endswith(_PACK_OUTPUT_SECRET_SUFFIXES)
        or _PACK_OUTPUT_SECRET_TOKEN_RE.search(path)
    ):
        raise ValueError(
            f"capability_pack_output path is secret-shaped and not registrable: {path!r}"
        )


def _capability_pack_output_scope(path: str) -> PathScope:
    if path.startswith(("docs/", "scripts/")):
        return PathScope.WORKSPACE_ROOT
    return PathScope.APP_BUNDLE_ROOT


def _capability_pack_output_consumer(path: str) -> RuntimeConsumerIdentifier:
    if _capability_pack_output_scope(path) is PathScope.WORKSPACE_ROOT:
        return RuntimeConsumerIdentifier.NONE
    return RuntimeConsumerIdentifier.PLATFORM_HOST


def _capability_pack_output_security(path: str) -> SecurityClass:
    if path.lower().endswith((".py", ".js", ".jsx", ".ts", ".tsx", ".ps1", ".sh")):
        return SecurityClass.EXECUTABLE_STUB
    return SecurityClass.INTERNAL_CONTRACT


def _family(
    kind: ArtifactKind,
    owner: LayoutOwner,
    requirement: Requirement,
    scope: PathScope,
    template: str,
    validator: ValidatorIdentifier,
    consumer: RuntimeConsumerIdentifier,
    *,
    condition: ConditionIdentifier | None = None,
    multiplicity: Multiplicity = Multiplicity.SINGLE,
    materializer: MaterializerIdentifier | None = None,
    security: SecurityClass = SecurityClass.INTERNAL_CONTRACT,
    assignment: tuple[AssignmentKind, ...] = (),
    stubs: tuple[StubKind, ...] = (),
    deps: tuple[ArtifactKind, ...] = (),
    inputs: tuple[str, ...] = (),
) -> ArtifactFamily:
    resolved_materializer = materializer or _materializer_for_family(
        kind=kind, owner=owner, security=security
    )
    resolved_inputs = inputs or _semantic_inputs_for_family(kind)
    resolved_condition = condition or (
        ConditionIdentifier.ALWAYS if requirement == Requirement.REQUIRED else ConditionIdentifier.WHEN_APP_DECLARED
    )
    return ArtifactFamily(
        kind=kind,
        owner=owner,
        requirement=requirement,
        multiplicity=multiplicity,
        condition=resolved_condition,
        path_scope=scope,
        path_template=template,
        materializer=resolved_materializer,
        validator=validator,
        runtime_consumer=consumer,
        security_class=security,
        assignment_kinds=assignment,
        allowed_stub_kinds=stubs,
        dependency_families=deps,
        semantic_input_kinds=resolved_inputs,
    )


def _materializer_for_family(
    *, kind: ArtifactKind, owner: LayoutOwner, security: SecurityClass
) -> MaterializerIdentifier:
    """Return the truthful historical materializer category for a core row.

    This replaces the former blanket ``APP_GENERATOR`` default.  It declares
    ownership category only; it does not claim that a deterministic renderer
    implementation exists or that its semantic inputs are complete.
    """
    if security is SecurityClass.EXECUTABLE_STUB:
        return MaterializerIdentifier.PRESERVED_OPAQUE
    if kind is ArtifactKind.APP_UI_PAGE_SCHEMA:
        return MaterializerIdentifier.PAGE_SCHEMA_EXECUTOR
    if kind in {
        ArtifactKind.MODULE_MANIFEST,
        ArtifactKind.MODULE_CONTRACT,
        ArtifactKind.MODULE_RUNTIME_EXTENSIONS,
    }:
        return MaterializerIdentifier.MODULE_CONTRACT_EXECUTOR
    if kind is ArtifactKind.WORKFLOW_MANIFEST:
        return MaterializerIdentifier.WORKFLOW_GENERATOR
    if kind in {
        ArtifactKind.WORKFLOW_CONFIG,
        ArtifactKind.WORKFLOW_TASK_BATCH,
        ArtifactKind.WORKFLOW_TOOL,
        ArtifactKind.WORKFLOW_UI,
    }:
        return MaterializerIdentifier.PRESERVED_OPAQUE
    if kind in {ArtifactKind.BUILD_CONTEXT_REGISTRY, ArtifactKind.CAPABILITY_PACK_OUTPUT}:
        return MaterializerIdentifier.CAPABILITY_PACK_MATERIALIZER
    if owner is LayoutOwner.DOWNLOAD_RENDERER or kind is ArtifactKind.APP_DEPLOYMENT_ARTIFACT:
        return MaterializerIdentifier.DOWNLOAD_DEPLOYMENT_RENDERER
    return MaterializerIdentifier.APP_GENERATOR


def _semantic_inputs_for_family(kind: ArtifactKind) -> tuple[str, ...]:
    """Declare graph-v2 input kinds for the bounded renderer corpus."""
    return {
        ArtifactKind.APP_UI_PAGE_SCHEMA: (
            "page",
            "section",
            "action",
            "workflow",
        ),
        ArtifactKind.MODULE_MANIFEST: (
            "module",
            "action",
            "capability",
            "permission",
            "event",
        ),
        ArtifactKind.MODULE_CONTRACT: (
            "module",
            "event",
            "reaction",
            "notification",
        ),
        ArtifactKind.APP_DATA_CONTRACT: (
            "data_collection",
            "data_alias",
            "module",
        ),
        ArtifactKind.APP_SUBSCRIPTION_CONFIG: (
            "plan",
            "product",
            "meter",
            "limit",
            "capability",
        ),
        ArtifactKind.WORKFLOW_MANIFEST: (
            "workflow",
            "trigger",
            "event",
            "capability",
        ),
        ArtifactKind.APP_DEPLOYMENT_ARTIFACT: (
            "deployment_target",
        ),
    }.get(kind, ())


def _prohibited(scope: PathScope, template: str) -> ArtifactFamily:
    return ArtifactFamily(
        kind=ArtifactKind.PROHIBITED_LEGACY,
        owner=LayoutOwner.PROHIBITED,
        requirement=Requirement.PROHIBITED,
        multiplicity=Multiplicity.MANY if "{" in template else Multiplicity.SINGLE,
        condition=ConditionIdentifier.NEVER,
        path_scope=scope,
        path_template=template,
        materializer=MaterializerIdentifier.NONE,
        validator=ValidatorIdentifier.APP_PATHS,
        runtime_consumer=RuntimeConsumerIdentifier.NONE,
        security_class=SecurityClass.PROHIBITED,
    )


def _normalize_template(value: str) -> str:
    text = str(value or "").replace("\\", "/").strip()
    if not text:
        raise ValueError("path_template must be non-empty")
    if text.startswith("/") or "://" in text:
        raise ValueError(f"absolute path templates are not allowed: {value!r}")
    if any(char in text for char in _GLOB_CHARS):
        raise ValueError(f"glob characters are not allowed in path templates: {value!r}")
    path = PurePosixPath(text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"unsafe path template: {value!r}")
    if path.parts and ":" in path.parts[0]:
        raise ValueError(f"absolute drive path templates are not allowed: {value!r}")
    placeholder_names = _PLACEHOLDER_RE.findall(text)
    for name in placeholder_names:
        try:
            PlaceholderIdentifier(name)
        except ValueError as exc:
            raise ValueError(f"unknown path template placeholder: {name!r}") from exc
    without_placeholders = _PLACEHOLDER_RE.sub("", text)
    if "{" in without_placeholders or "}" in without_placeholders:
        raise ValueError(f"ambiguous or malformed path template placeholders: {value!r}")
    # Registry templates share the compiler-wide portable path profile
    # (mozaiks.portable_path.v1). Placeholders are substituted with a benign
    # identifier so only the template's literal structure is profiled.
    from mozaiksai.core.semantics.portable_path import PortablePathError, validate_portable_path

    normalized_template = text.rstrip("/")
    probe = _PLACEHOLDER_RE.sub("x0", normalized_template)
    try:
        validate_portable_path(probe)
    except PortablePathError as exc:
        raise ValueError(
            f"path template violates mozaiks.portable_path.v1 ({exc.reason}): {value!r}"
        ) from exc
    return normalized_template


def _normalize_runtime_path(value: str) -> str:
    text = unicodedata.normalize("NFC", str(value or "")).replace("\\", "/").strip()
    if not text:
        raise ValueError("path must be non-empty")
    if text.startswith("/") or "://" in text:
        raise ValueError(f"absolute paths are not allowed: {value!r}")
    path = PurePosixPath(text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"unsafe path: {value!r}")
    if any(char in text for char in _GLOB_CHARS):
        raise ValueError(f"glob characters are not allowed in paths: {value!r}")
    if path.parts and ":" in path.parts[0]:
        raise ValueError(f"absolute drive paths are not allowed: {value!r}")
    return str(path)


def _match_family(family: ArtifactFamily, normalized_path: str) -> ArtifactMatch | None:
    pattern, placeholder_order = _template_regex(family.path_template)
    match = pattern.fullmatch(normalized_path)
    if match is None:
        return None
    values = {
        PlaceholderIdentifier(name): value
        for name, value in zip(placeholder_order, match.groups(), strict=True)
    }
    return ArtifactMatch(family=family, normalized_path=normalized_path, values=values)


def _template_regex(template: str) -> tuple[re.Pattern[str], tuple[str, ...]]:
    names: list[str] = []
    cursor = 0
    parts: list[str] = ["^"]
    for match in _PLACEHOLDER_RE.finditer(template):
        parts.append(re.escape(template[cursor : match.start()]))
        names.append(match.group(1))
        if match.group(1) == PlaceholderIdentifier.HELPER_ID.value:
            reserved = "|".join(re.escape(item) for item in sorted(_RESERVED_MODULE_BACKEND_HELPERS))
            parts.append(rf"(?!(?:{reserved})\.py)([A-Za-z0-9_-]+)")
        else:
            parts.append(r"([A-Za-z0-9_-]+)")
        cursor = match.end()
    parts.append(re.escape(template[cursor:]))
    parts.append("$")
    return re.compile("".join(parts)), tuple(names)


def _validate_dependency_closure(families: tuple[ArtifactFamily, ...]) -> None:
    """Every declared dependency must resolve, and the graph must be acyclic."""
    registered = {family.kind for family in families}
    edges: dict[ArtifactKind, set[ArtifactKind]] = {kind: set() for kind in registered}
    for family in families:
        for dependency in family.dependency_families:
            if dependency not in registered:
                raise ValueError(
                    f"artifact family {family.kind.value!r} depends on unregistered "
                    f"family {dependency.value!r}"
                )
            edges[family.kind].add(dependency)

    # Iterative DFS cycle detection with deterministic traversal order.
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[ArtifactKind, int] = {kind: WHITE for kind in registered}
    for root in sorted(registered, key=lambda kind: kind.value):
        if color[root] != WHITE:
            continue
        stack: list[tuple[ArtifactKind, list[ArtifactKind]]] = [
            (root, sorted(edges[root], key=lambda kind: kind.value))
        ]
        color[root] = GRAY
        while stack:
            node, pending = stack[-1]
            if pending:
                child = pending.pop(0)
                if color[child] == GRAY:
                    raise ValueError(
                        f"artifact family dependency cycle involving {child.value!r}"
                    )
                if color[child] == WHITE:
                    color[child] = GRAY
                    stack.append((child, sorted(edges[child], key=lambda kind: kind.value)))
            else:
                color[node] = BLACK
                stack.pop()


def _validate_unique_templates(families: tuple[ArtifactFamily, ...]) -> None:
    seen_exact: set[tuple[PathScope, str]] = set()
    seen_shape: dict[tuple[PathScope, str], ArtifactFamily] = {}
    for family in families:
        exact_key = (family.path_scope, family.path_template)
        if exact_key in seen_exact:
            raise ValueError(f"duplicate path template: {family.path_scope.value}:{family.path_template}")
        seen_exact.add(exact_key)

        shape_key = (family.path_scope, _template_shape(family.path_template))
        previous = seen_shape.get(shape_key)
        if previous is not None and previous.path_template != family.path_template:
            raise ValueError(
                "ambiguous path templates with identical shape: "
                f"{previous.path_template!r} and {family.path_template!r}"
            )
        seen_shape[shape_key] = family


def _template_shape(template: str) -> str:
    return _PLACEHOLDER_RE.sub("{}", template)


def _family_sort_key(family: ArtifactFamily) -> tuple[str, str, str]:
    return (family.path_scope.value, family.path_template, family.kind.value)


def _stable_digest(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


__all__ = [
    "AppLayoutRegistry",
    "ArtifactFamily",
    "ArtifactKind",
    "ArtifactMatch",
    "ConditionIdentifier",
    "ExtensionSlot",
    "LayoutExtension",
    "LayoutOwner",
    "MaterializerIdentifier",
    "Multiplicity",
    "PathScope",
    "PlaceholderIdentifier",
    "Requirement",
    "RuntimeConsumerIdentifier",
    "SCHEMA_VERSION",
    "SecurityClass",
    "ValidatorIdentifier",
    "build_app_layout_registry",
    "default_app_layout_registry",
    "iter_artifact_kinds",
    "kinds_for_assignment",
    "match_path",
    "validate_registered_path",
    "resolve_taxonomy_artifact_family",
]
