from __future__ import annotations

from pathlib import Path

import pytest

from factory_app.workflows._shared.generated_ui_contract import audit_app_ui_bundle_integrity
from factory_app.workflows.AppGenerator.tools.save_admin_registry import save_admin_registry

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_route_manifest_component_registered_in_index_passes() -> None:
    warnings = audit_app_ui_bundle_integrity(
        [
            {
                "filename": "ui/route_manifest.json",
                "content": """
                {
                  "pages": [
                    {
                      "id": "analytics",
                      "label": "Analytics",
                      "path": "/analytics",
                      "component": "AnalyticsPage",
                      "requiresAuth": true,
                      "purpose": "Interactive analytics surface."
                    }
                  ]
                }
                """,
            },
            {
                "filename": "ui/pages/custom/AnalyticsPage.jsx",
                "content": "export default function AnalyticsPage(){ return <main />; }\n",
            },
            {
                "filename": "ui/index.js",
                "content": """
                import AnalyticsPage from './pages/custom/AnalyticsPage.jsx';

                export function register(registerComponent) {
                  registerComponent('AnalyticsPage', AnalyticsPage);
                }
                """,
            },
        ]
    )

    assert warnings == []


def test_route_manifest_component_missing_from_index_fails() -> None:
    warnings = audit_app_ui_bundle_integrity(
        [
            {
                "filename": "ui/route_manifest.json",
                "content": """
                {
                  "pages": [
                    {
                      "id": "analytics",
                      "label": "Analytics",
                      "path": "/analytics",
                      "component": "AnalyticsPage",
                      "requiresAuth": true,
                      "purpose": "Interactive analytics surface."
                    }
                  ]
                }
                """,
            },
            {
                "filename": "ui/index.js",
                "content": "export function register(registerComponent) {}\n",
            },
        ]
    )

    assert any("AnalyticsPage" in warning and "/analytics" in warning for warning in warnings)
    assert any("no registerComponent('AnalyticsPage'" in warning for warning in warnings)


def test_registered_component_without_import_or_definition_fails() -> None:
    warnings = audit_app_ui_bundle_integrity(
        [
            {
                "filename": "ui/route_manifest.json",
                "content": '{"pages":[{"path":"/reports","component":"ReportsPage"}]}',
            },
            {
                "filename": "ui/index.js",
                "content": """
                export function register(registerComponent) {
                  registerComponent('ReportsPage', ReportsPage);
                }
                """,
            },
        ]
    )

    assert any(
        "registers component 'ReportsPage' but does not import or define" in warning
        for warning in warnings
    )


def test_registered_component_importing_missing_file_fails() -> None:
    warnings = audit_app_ui_bundle_integrity(
        [
            {
                "filename": "ui/route_manifest.json",
                "content": '{"pages":[{"path":"/reports","component":"ReportsPage"}]}',
            },
            {
                "filename": "ui/index.js",
                "content": """
                import ReportsPage from './pages/custom/ReportsPage.jsx';
                export function register(registerComponent) {
                  registerComponent('ReportsPage', ReportsPage);
                }
                """,
            },
        ]
    )

    assert any(
        "ReportsPage" in warning and "missing file 'ui/pages/custom/ReportsPage.jsx'" in warning
        for warning in warnings
    )


def test_custom_page_file_exists_but_not_registered_fails() -> None:
    warnings = audit_app_ui_bundle_integrity(
        [
            {
                "filename": "ui/pages/custom/AuditLogPage.jsx",
                "content": "export default function AuditLogPage(){ return <main />; }\n",
            },
            {
                "filename": "ui/index.js",
                "content": "export function register(registerComponent) {}\n",
            },
        ]
    )

    assert warnings == [
        "app UI bundle custom page file 'ui/pages/custom/AuditLogPage.jsx' exports component 'AuditLogPage' but that component is not registered in ui/index.js."
    ]


def test_admin_registry_custom_route_misuse_fails() -> None:
    warnings = audit_app_ui_bundle_integrity(
        [
            {
                "filename": "admin/admin_registry.yaml",
                "content": """
                schema_version: mozaiks.admin.registry.v1
                pages:
                  - id: reports
                    label: Reports
                    path: /reports
                    icon: chart
                    scope: app
                    order: 10
                    enabled: true
                    component: ReportsPage
                    registry_key: ReportsPage
                """,
            }
        ]
    )

    assert any("admin/admin_registry.yaml page 'reports'" in warning for warning in warnings)
    assert any("ui/route_manifest.json and ui/index.js" in warning for warning in warnings)


@pytest.mark.parametrize("forbidden_field", ["component", "renderer", "registry_key"])
def test_save_admin_registry_rejects_custom_route_misuse_before_assembly(
    forbidden_field: str,
) -> None:
    field_value = "ReportsPage"
    with pytest.raises(ValueError, match="Admin registry output failed"):
        save_admin_registry(
            admin_registry={
                "schema_version": "mozaiks.admin.registry.v1",
                "pages": [
                    {
                        "id": "reports",
                        "label": "Reports",
                        "path": "/reports",
                        "icon": "chart",
                        "scope": "app",
                        "order": 10,
                        "enabled": True,
                        forbidden_field: field_value,
                    }
                ],
            },
            code_files=[
                {
                    "filename": "admin/admin_registry.yaml",
                    "content": f"""
                    schema_version: mozaiks.admin.registry.v1
                    pages:
                      - id: reports
                        label: Reports
                        path: /reports
                        icon: chart
                        scope: app
                        order: 10
                        enabled: true
                        {forbidden_field}: {field_value}
                    """,
                }
            ],
        )


def test_admin_registry_supported_page_config_passes() -> None:
    warnings = audit_app_ui_bundle_integrity(
        [
            {
                "filename": "admin/admin_registry.yaml",
                "content": """
                schema_version: mozaiks.admin.registry.v1
                pages:
                  - id: overview
                    label: Overview
                    path: /admin
                    icon: dashboard
                    scope: app
                    order: 0
                    enabled: true
                  - id: reports
                    label: Reports
                    path: /apps/:appId/reports
                    icon: chart
                    scope: app
                    order: 10
                    enabled: true
                """,
            }
        ]
    )

    assert warnings == []


def test_schema_page_route_without_custom_component_is_unaffected() -> None:
    warnings = audit_app_ui_bundle_integrity(
        [
            {
                "filename": "ui/pages/Reports.yaml",
                "content": """
                name: Reports
                route: /reports
                title: Reports
                page_type: record_list
                sections: []
                """,
            }
        ]
    )

    assert warnings == []


def test_docs_and_test_fixtures_are_ignored() -> None:
    warnings = audit_app_ui_bundle_integrity(
        [
            {
                "filename": "docs/examples/ui/route_manifest.json",
                "content": '{"pages":[{"path":"/bad","component":"BadPage"}]}',
            },
            {
                "filename": "tests/fixtures/ui/pages/custom/BadPage.jsx",
                "content": "export default function BadPage(){ return null; }\n",
            },
        ]
    )

    assert warnings == []


def test_appgenerator_guidance_mentions_route_manifest_and_registration() -> None:
    agents = _read("factory_app/workflows/AppGenerator/agents.yaml")

    assert "ui/route_manifest.json" in agents
    assert "ui/pages/custom/*.jsx" in agents
    assert "ui/index.js" in agents
    assert "registerComponent" in agents
    assert "Do NOT add full-page custom route ownership fields" in agents


def test_add_page_skill_mentions_route_manifest_and_registration() -> None:
    skill = _read(".claude/skills/add-page/SKILL.md")

    assert "app/ui/route_manifest.json" in skill
    assert "app/ui/index.js" in skill
    assert "registerComponent" in skill
    assert "`admin/admin_registry.yaml` is for admin page and panel metadata" in skill


def test_frontend_rules_mention_route_component_registry_drift() -> None:
    frontend_rules = _read(".claude/rules/frontend.md")

    assert "Every `route_manifest.json` component key must match a registration" in frontend_rules
    assert "Missing or mismatched custom route registrations are export/download blockers" in frontend_rules
    assert "Custom React routes are not auto-discovered" in frontend_rules


def test_docs_and_prompts_do_not_describe_removed_custom_route_ownership() -> None:
    guidance_files = {
        "agents": _read("factory_app/workflows/AppGenerator/agents.yaml"),
        "frontend_rules": _read(".claude/rules/frontend.md"),
        "add_page": _read(".claude/skills/add-page/SKILL.md"),
        "surface_contract": _read(
            "docs/architecture/frontend/ui-system/generated-frontend-surface-contract.md"
        ),
        "assembly_contract": _read(
            "docs/architecture/builder/appgenerator-output-assembly-contract.md"
        ),
        "adding_pages": _read("docs/guides/adding-pages/01-overview.md"),
    }

    forbidden_phrases = (
        "auto-discover custom",
        "auto discovered custom",
        "auto-discovered custom",
        "optional registration",
        "admin_registry.yaml can own",
        "admin_registry.yaml owns full-page",
        "admin_registry.yaml is a route registry",
    )
    for label, content in guidance_files.items():
        lowered = content.lower()
        for phrase in forbidden_phrases:
            assert phrase not in lowered, f"{label} still contains removed route-ownership phrase: {phrase}"


def test_docs_and_prompts_state_no_implicit_custom_route_discovery() -> None:
    guidance = "\n".join(
        [
            _read("factory_app/workflows/AppGenerator/agents.yaml"),
            _read(".claude/skills/add-page/SKILL.md"),
            _read(".claude/rules/frontend.md"),
            _read("docs/guides/adding-pages/01-overview.md"),
        ]
    ).lower()

    assert "not auto-discovered" in guidance or "never rely on implicit custom page discovery" in guidance
    assert "admin/admin_registry.yaml" in guidance
    assert "is not a route registry" in guidance


def test_route_component_guidance_uses_neutral_examples() -> None:
    changed_guidance = "\n".join(
        [
            _read("factory_app/workflows/AppGenerator/agents.yaml"),
            _read(".claude/skills/add-page/SKILL.md"),
            _read(".claude/rules/frontend.md"),
            _read("docs/architecture/frontend/ui-system/generated-frontend-surface-contract.md"),
        ]
    )

    for forbidden in ("MozaiksPay", "proprietary app"):
        assert forbidden not in changed_guidance

