from __future__ import annotations

from .definition import AppDefinition, AppFeatureFlags, ModuleRef, ExecutionMode, WorkflowRef, PageRef
from .loader import AppLoader, AppLoadError, AppLoadResult
from .module_loader import ModuleLoader, ModuleLoadError, LoadedModule, ModuleDefinition, ActionDef

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
    *_STUDIO_SUMMARY_EXPORTS,
]


def __getattr__(name: str):
    if name not in _STUDIO_SUMMARY_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from . import studio_summary as _studio_summary

    return getattr(_studio_summary, name)
