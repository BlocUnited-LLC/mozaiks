from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from mozaiksai.core.dashboard import (
    DASHBOARD_SCHEMA_VERSION,
    DashboardAction,
    DashboardManifest,
    build_dashboard_shell_routes,
    build_default_dashboard_manifest,
    load_dashboard_manifest,
    merge_dashboard_manifest_overlay,
)


def _portal_ids(manifest: DashboardManifest, scope: str) -> list[str]:
    return [portal.id for portal in manifest.surface_for_scope(scope).enabled_portals()]


def test_default_dashboard_manifest_declares_workspace_and_app_scopes() -> None:
    manifest = build_default_dashboard_manifest()

    assert manifest.schema_version == DASHBOARD_SCHEMA_VERSION
    assert manifest.workspace.scope == "workspace"
    assert manifest.workspace.route_pattern == "/apps"
    assert manifest.app.scope == "app"
    assert manifest.app.route_pattern == "/apps/:appId"
    assert manifest.app.default_portal == "overview"
    assert {"overview", "building", "branding", "launch", "growth", "users", "usage", "support", "settings"} <= set(
        _portal_ids(manifest, "app")
    )


def test_dashboard_manifest_overlay_can_customize_default_portals() -> None:
    raw = yaml.safe_load(
        """
        schema_version: mozaiks.dashboard.v1
        extends: default
        workspace:
          portals:
            - id: integrations
              enabled: false
        app:
          portals:
            - id: branding
              label: Brand
              order: 5
              panels:
                - id: media
                  title: Asset Library
                - id: moodboard
                  type: media_asset_gallery
                  title: Moodboard
                  source: media
            - id: community
              label: Community
              route: /apps/:appId/community
              icon: users
              order: 80
              panels:
                - id: members
                  type: module_panel_ref
                  title: Members
                  source: module
                  module_id: community_membership
        """
    )

    manifest = DashboardManifest.model_validate(merge_dashboard_manifest_overlay(raw))
    workspace_ids = _portal_ids(manifest, "workspace")
    branding = next(portal for portal in manifest.app.portals if portal.id == "branding")
    media = next(panel for panel in branding.panels if panel.id == "media")
    community = next(portal for portal in manifest.app.portals if portal.id == "community")

    assert "integrations" not in workspace_ids
    assert branding.label == "Brand"
    assert branding.order == 5
    assert media.type == "media_asset_gallery"
    assert media.title == "Asset Library"
    assert any(panel.id == "moodboard" for panel in branding.panels)
    assert community.route == "/apps/:appId/community"


def test_load_dashboard_manifest_reads_app_root_dashboard_yaml(tmp_path: Path) -> None:
    dashboard_dir = tmp_path / "dashboard"
    dashboard_dir.mkdir()
    (dashboard_dir / "dashboard.yaml").write_text(
        """
        schema_version: mozaiks.dashboard.v1
        extends: default
        app:
          portals:
            - id: growth
              enabled: false
        """,
        encoding="utf-8",
    )

    manifest = load_dashboard_manifest(tmp_path)

    assert "growth" not in _portal_ids(manifest, "app")
    assert "branding" in _portal_ids(manifest, "app")


def test_invalid_dashboard_action_targets_are_rejected() -> None:
    with pytest.raises(ValidationError, match="module_action"):
        DashboardAction.model_validate(
            {
                "id": "open_billing",
                "label": "Open Billing",
                "type": "module_action",
                "target": "open_billing_portal",
            }
        )


def test_dashboard_shell_routes_are_route_manifest_compatible() -> None:
    manifest = build_default_dashboard_manifest()

    routes = build_dashboard_shell_routes(manifest, scopes=["app"])
    by_path = {route["path"]: route for route in routes}

    assert "/apps/:appId/branding" in by_path
    assert by_path["/apps/:appId/branding"]["component"] == "DashboardPortalPage"
    assert by_path["/apps/:appId/branding"]["meta"]["dashboard"] == {
        "surface": "app",
        "scope": "app",
        "portal": "branding",
        "panel_count": 2,
    }
    assert by_path["/apps/:appId/branding"]["meta"]["navigation"]["group"] == "app-studio"


def test_generator_file_contract_keeps_dashboard_separate_from_workflow_routing() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    contract = (repo_root / "factory_app/build_context/AppGenerator/file_contracts.yaml").read_text(
        encoding="utf-8"
    )
    architecture_doc = (repo_root / "docs/architecture/app/app-dashboard-contract.md").read_text(
        encoding="utf-8"
    )

    assert "dashboard/dashboard.yaml" in contract
    assert "Workspace/App Dashboard portal structure belongs in dashboard/dashboard.yaml" in contract
    assert "workflows/extended_orchestration/extension_registry.json" in contract
    assert "The dashboard manifest may reference workflow sequences, but it does not define" in architecture_doc
