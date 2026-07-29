from __future__ import annotations

from .definition import (
    AppDefinition,
    AppFeatureFlags,
    ExecutionMode,
    ModuleRef,
    PageRef,
    WorkflowRef,
)
from .entitlements import ConfiguredEntitlementAdapter
from .loader import AppLoader, AppLoadError, AppLoadResult
from .module_loader import ActionDef, LoadedModule, ModuleDefinition, ModuleLoader, ModuleLoadError
from .provenance import (
    DEFAULT_APP_CONTRACTS,
    PROVENANCE_SCHEMA_VERSION,
    AppProvenance,
    AppProvenanceArtifactRefs,
    AppProvenanceKind,
    AppProvenanceLoadError,
    AppProvenanceMode,
    AppProvenanceRunRef,
    build_default_app_provenance,
    current_mozaiks_package_ref,
    dump_app_provenance_yaml,
    load_app_provenance,
    resolve_app_provenance_path,
    write_app_provenance,
)

_STUDIO_SUMMARY_EXPORTS = {
    "build_app_overview_summary",
    "build_apps_summary",
    "build_build_section",
    "build_build_summary",
    "build_integrations_summary",
    "get_missing_studio_surfaces",
}

__all__ = [
    "AppDefinition",
    "AppFeatureFlags",
    "AppLoadError",
    "AppLoadResult",
    "AppLoader",
    "ExecutionMode",
    "WorkflowRef",
    "ModuleRef",
    "PageRef",
    "ModuleLoader",
    "ModuleLoadError",
    "LoadedModule",
    "ModuleDefinition",
    "ActionDef",
    "ConfiguredEntitlementAdapter",
    "DEFAULT_APP_CONTRACTS",
    "PROVENANCE_SCHEMA_VERSION",
    "AppProvenance",
    "AppProvenanceArtifactRefs",
    "AppProvenanceKind",
    "AppProvenanceLoadError",
    "AppProvenanceMode",
    "AppProvenanceRunRef",
    "build_default_app_provenance",
    "current_mozaiks_package_ref",
    "dump_app_provenance_yaml",
    "load_app_provenance",
    "resolve_app_provenance_path",
    "write_app_provenance",
    *_STUDIO_SUMMARY_EXPORTS,
]


def __getattr__(name: str):
    if name not in _STUDIO_SUMMARY_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from . import studio_summary as _studio_summary

    return getattr(_studio_summary, name)
