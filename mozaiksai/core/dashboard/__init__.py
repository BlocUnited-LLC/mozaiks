"""Canonical dashboard contracts for Workspace Studio and App Studio."""

from .manifest import (
    DASHBOARD_MANIFEST_RELATIVE_PATH,
    DASHBOARD_SCHEMA_VERSION,
    DashboardAction,
    DashboardManifest,
    DashboardPanel,
    DashboardPortal,
    DashboardSurface,
    build_dashboard_shell_routes,
    build_default_dashboard_manifest,
    load_dashboard_manifest,
    merge_dashboard_manifest_overlay,
)

__all__ = [
    "DASHBOARD_MANIFEST_RELATIVE_PATH",
    "DASHBOARD_SCHEMA_VERSION",
    "DashboardAction",
    "DashboardManifest",
    "DashboardPanel",
    "DashboardPortal",
    "DashboardSurface",
    "build_dashboard_shell_routes",
    "build_default_dashboard_manifest",
    "load_dashboard_manifest",
    "merge_dashboard_manifest_overlay",
]
