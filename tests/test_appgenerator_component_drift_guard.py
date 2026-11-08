from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from factory_app.workflows._shared.generated_ui_contract import audit_app_ui_bundle_integrity
from factory_app.workflows.AppGenerator.tools.save_admin_registry import save_admin_registry

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _neutral_generated_app_contract_files(*, route_auth_action: str = "authorize_project_route") -> list[dict[str, str]]:
    return [
        {
            "filename": "ui/pages/ProjectSettings.yaml",
            "content": """
            name: ProjectSettings
            route: /projects/:projectId/settings
            title: Project Settings
            page_type: settings
            navigation:
              id: project-settings
              label: Project Settings
              scope: local
              icon: settings
              requiresRole: project_admin
            meta:
              requiresAuth: true
              requiresRole: project_admin
              routeAuth:
                module: project_access
                action: authorize_project_route
                params:
                  project_id: $route.projectId
            sections:
              - primitive: PageHeader
                config:
                  title: Project Settings
            """,
        },
        {
            "filename": "modules/project_access/module.yaml",
            "content": f"""
            schema_version: mozaiks.module.v1
            module:
              id: project_access
              display_name: Project Access
              version: 0.1.0
              type: standard
              description: Authorizes scoped project routes.
              owner: app
              visibility: internal
              handler: backend.handler:ProjectAccessModule
            permissions:
              - id: project.manage
                description: Manage project configuration.
            actions:
              - id: {route_auth_action}
                description: Authorize access to a scoped project route.
                handler_method: {route_auth_action}
                api_surface: internal
                input_schema:
                  type: object
                  required: [project_id]
                  properties:
                    project_id:
                      type: string
                output_schema:
                  type: object
                  required: [allowed]
                  properties:
                    allowed:
                      type: boolean
                permissions: [project.manage]
                emits: []
            capabilities: []
            """,
        },
        {
            "filename": "admin/admin_registry.yaml",
            "content": """
            schema_version: mozaiks.admin.registry.v1
            pages:
              - id: overview
                label: Overview
                path: /admin
                icon: home
                scope: app
                order: 0
                enabled: true
              - id: settings
                label: Settings
                path: /admin/settings
                icon: settings
                scope: app
                order: 60
                enabled: true
            """,
        },
    ]


def _route_auth_output_files(output_schema, *, custom):
    files = _neutral_generated_app_contract_files()
    module = yaml.safe_load(files[1]["content"])
    module["actions"][0]["output_schema"] = output_schema
    files[1]["content"] = yaml.safe_dump(module)
    if custom:
        page = yaml.safe_load(files[0]["content"])
        files[0] = {"filename": "ui/route_manifest.json", "content": json.dumps({"pages": [{
            "id": "project-settings", "path": page["route"], "component": "ProjectSettings",
            "meta": page["meta"],
        }]})}
        files.extend([
            {"filename": "ui/pages/custom/ProjectSettings.jsx", "content": "export default function ProjectSettings(){ return <main />; }"},
            {"filename": "ui/index.js", "content": "import ProjectSettings from './pages/custom/ProjectSettings.jsx'; export function register(registerComponent){registerComponent('ProjectSettings', ProjectSettings);}"},
        ])
    return files


@pytest.mark.parametrize("custom", [False, True])
@pytest.mark.parametrize("schema", [
    None,
    {"type": "object", "required": ["authorized"], "properties": {"authorized": {"type": "boolean"}}},
    {"type": "object", "properties": {"allowed": {"type": "boolean"}}},
    {"type": "object", "required": ["allowed"], "properties": {"allowed": {"type": "string"}}},
    {"type": "object", "required": ["allowed"], "properties": {"allowed": {"type": ["boolean", "null"]}}},
    {"type": "array", "items": {"type": "boolean"}},
])
def test_routeauth_requires_declared_nonnullable_boolean_allowed(schema, custom):
    warnings = audit_app_ui_bundle_integrity(_route_auth_output_files(schema, custom=custom))
    assert any("project_access/authorize_project_route" in warning and "required boolean 'allowed'" in warning for warning in warnings)


@pytest.mark.parametrize("custom", [False, True])
def test_routeauth_accepts_boolean_allowed_with_optional_response_metadata(custom):
    schema = {"type": "object", "required": ["allowed"], "properties": {
        "allowed": {"type": "boolean"}, "reason": {"type": "string"},
        "status": {"type": "string"}, "capabilities": {"type": "array", "items": {"type": "string"}},
    }}
    assert audit_app_ui_bundle_integrity(_route_auth_output_files(schema, custom=custom)) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("custom", [False, True])
@pytest.mark.parametrize("valid", [False, True])
async def test_wiring_checks_routeauth_from_exact_module_companion(custom, valid):
    from factory_app.workflows.AppGenerator.tools.validate_wiring import validate_wiring

    schema = {"type": "object", "required": ["allowed"], "properties": {"allowed": {"type": "boolean"}}}
    files = _route_auth_output_files(schema if valid else {"type": "object", "properties": {"authorized": {"type": "boolean"}}}, custom=custom)
    # An identically named action in another module cannot satisfy this reference.
    other = yaml.safe_load(files[1]["content"])
    other["module"]["id"] = "other_access"
    other["actions"][0]["output_schema"] = {"type": "object", "required": ["allowed"], "properties": {"allowed": {"type": "boolean"}}}
    files.append({"filename": "modules/other_access/module.yaml", "content": yaml.safe_dump(other)})
    result = await validate_wiring({"generated_files": {item["filename"]: item["content"] for item in files}})
    assert result["passed"] is valid
    if not valid:
        assert any("required boolean 'allowed'" in item["error"] for item in result["failed_tests"])


@pytest.mark.asyncio
@pytest.mark.parametrize("custom", [False, True])
async def test_routeauth_missing_companion_defers_partial_but_blocks_complete_snapshot(custom):
    from factory_app.workflows.AppGenerator.tools.validate_wiring import validate_wiring

    files = [item for item in _route_auth_output_files(None, custom=custom)
             if not item["filename"].startswith("modules/")]
    assert audit_app_ui_bundle_integrity(files) == []
    warnings = audit_app_ui_bundle_integrity(files, require_module_contracts=True)
    assert any("module 'project_access'" in warning for warning in warnings)
    result = await validate_wiring({"generated_files": {item["filename"]: item["content"] for item in files}})
    assert result["passed"] is False
    assert any("module 'project_access'" in item["error"] for item in result["failed_tests"])


@pytest.mark.parametrize("required", [False, True])
def test_routeauth_checks_materialized_typed_output_contract(required):
    from mozaiksai.core.workflow.generator_support.code_files import (
        extract_code_file_map_from_payload,
    )

    files = _neutral_generated_app_contract_files()
    module = yaml.safe_load(files[1]["content"])
    module["actions"][0]["output_schema"] = {
        "type": "object", "properties": [{
            "name": "allowed", "type": "boolean", "required": required,
            "description": "Whether this principal may enter the selected route.",
        }],
    }
    materialized = extract_code_file_map_from_payload({"module_contract": {
        "module_id": "project_access", "module_yaml": module,
    }})
    files[1]["content"] = materialized[files[1]["filename"]]
    warnings = audit_app_ui_bundle_integrity(files, require_module_contracts=True)
    assert bool(warnings) is not required
    if not required:
        assert any("required boolean 'allowed'" in warning for warning in warnings)


@pytest.mark.parametrize("custom", [False, True])
def test_staged_route_validation_keeps_module_output_contracts(custom):
    from mozaiksai.control_plane.validation_runner import _route_component_validation

    files = _route_auth_output_files({"type": "object", "properties": {"authorized": {"type": "boolean"}}}, custom=custom)
    result = _route_component_validation({item["filename"]: item["content"] for item in files})
    assert result.status == "failed"
    assert "required boolean 'allowed'" in result.reason


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


def test_route_manifest_invalid_routeauth_fails_bundle_audit() -> None:
    warnings = audit_app_ui_bundle_integrity(
        [
            {
                "filename": "ui/route_manifest.json",
                "content": """
                {
                  "pages": [
                    {
                      "id": "project-settings",
                      "label": "Project Settings",
                      "path": "/projects/:projectId/settings",
                      "component": "ProjectSettingsPage",
                      "requiresAuth": true,
                      "meta": {
                        "routeAuth": {
                          "module": "",
                          "action": "authorize_project_route",
                          "params": { "project_id": "$route.projectId" }
                        }
                      },
                      "purpose": "Scoped project settings surface."
                    }
                  ]
                }
                """,
            },
            {
                "filename": "ui/pages/custom/ProjectSettingsPage.jsx",
                "content": "export default function ProjectSettingsPage(){ return <main />; }\n",
            },
            {
                "filename": "ui/index.js",
                "content": """
                import ProjectSettingsPage from './pages/custom/ProjectSettingsPage.jsx';

                export function register(registerComponent) {
                  registerComponent('ProjectSettingsPage', ProjectSettingsPage);
                }
                """,
            },
        ]
    )

    assert any("routeAuth.module is required" in warning for warning in warnings)


def test_neutral_generated_app_contract_passes_routeauth_admin_and_settings_audit() -> None:
    warnings = audit_app_ui_bundle_integrity(_neutral_generated_app_contract_files())

    assert warnings == []


def test_routeauth_missing_module_action_fails_bundle_audit() -> None:
    warnings = audit_app_ui_bundle_integrity(
        _neutral_generated_app_contract_files(route_auth_action="list_projects")
    )

    assert any(
        "ui/pages/ProjectSettings.yaml.meta.routeAuth" in warning
        and "project_access/authorize_project_route" in warning
        and "does not declare that action" in warning
        for warning in warnings
    )


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
        "app UI bundle custom page file 'ui/pages/custom/AuditLogPage.jsx' is not imported by a registered component in ui/index.js."
    ]


@pytest.mark.parametrize("defect", [None, "missing_binding", "missing_file", "unregistered_file"])
def test_custom_registry_resolves_key_through_actual_import_binding(defect) -> None:
    from mozaiksai.core.workflow.generator_support.code_files import _custom_route_bundle_code_files
    from tests.test_appgenerator_save_app_schema import _custom_route_bundle

    bundle = _custom_route_bundle()
    bundle["page_files"][0]["path"] = "ui/pages/custom/deal_room.jsx"
    files = {entry["filename"]: entry["content"] for entry in _custom_route_bundle_code_files(bundle)}
    if defect == "missing_binding":
        # An imported variable exists, but the registration uses a different,
        # undefined one. The registry key cannot stand in for that binding.
        files["ui/index.js"] = files["ui/index.js"].replace(
            "'InvestorDealRoomPage', InvestorDealRoom,", "'InvestorDealRoomPage', MissingPage,",
        )
    elif defect == "missing_file":
        files.pop("ui/pages/custom/deal_room.jsx")
    elif defect == "unregistered_file":
        files["ui/pages/custom/unused.jsx"] = "export default function Unused() { return null; }"
    warnings = audit_app_ui_bundle_integrity([
        {"filename": path, "content": content} for path, content in files.items()
    ])
    if defect == "missing_binding":
        assert any("registered binding 'MissingPage'" in warning for warning in warnings)
    elif defect == "missing_file":
        assert any("missing file 'ui/pages/custom/deal_room.jsx'" in warning for warning in warnings)
    elif defect == "unregistered_file":
        assert any("custom page file 'ui/pages/custom/unused.jsx' is not imported" in warning for warning in warnings)
    else:
        assert warnings == []


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
                    path: /admin/reports
                    icon: chart
                    scope: app
                    order: 10
                    enabled: true
                """,
            }
        ]
    )

    assert warnings == []


def test_admin_registry_rejects_hosted_studio_paths_for_standard_generated_apps() -> None:
    warnings = audit_app_ui_bundle_integrity(
        [
            {
                "filename": "admin/admin_registry.yaml",
                "content": """
                schema_version: mozaiks.admin.registry.v1
                pages:
                  - id: settings
                    label: Settings
                    path: /apps/:appId/settings
                    icon: settings
                    scope: app
                    order: 60
                    enabled: true
                """,
            }
        ]
    )

    assert any("Standard generated-app AdminPortal pages must use /admin paths" in warning for warning in warnings)


def test_admin_registry_allows_explicit_host_operator_surface_paths() -> None:
    warnings = audit_app_ui_bundle_integrity(
        [
            {
                "filename": "admin/admin_registry.yaml",
                "content": """
                schema_version: mozaiks.admin.registry.v1
                pages:
                  - id: hosting
                    label: Hosting
                    path: /apps/:appId/hosting
                    icon: server
                    scope: app
                    surfaces: [studio]
                    order: 90
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
    assert "meta.routeAuth" in agents
    assert "authorize_*_route" in agents


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
    agents_route_guidance = "\n".join(
        line
        for line in _read("factory_app/workflows/AppGenerator/agents.yaml").splitlines()
        if any(
            marker in line
            for marker in (
                "custom route",
                "custom_route_bundle",
                "route_manifest",
                "ui/pages/custom",
                "ui/index.js",
                "registerComponent",
                "admin/admin_registry.yaml",
            )
        )
    )
    route_guidance = "\n".join(
        [
            agents_route_guidance,
            _read(".claude/skills/add-page/SKILL.md"),
            _read(".claude/rules/frontend.md"),
            _read("docs/architecture/frontend/ui-system/generated-frontend-surface-contract.md"),
        ]
    )

    for forbidden in ("MozaiksPay", "proprietary app"):
        assert forbidden not in route_guidance


def test_routeauth_guidance_uses_first_class_neutral_contract() -> None:
    guidance = "\n".join(
        [
            _read("factory_app/workflows/DesignDocs/agents.yaml"),
            _read("factory_app/workflows/AppGenerator/agents.yaml"),
            _read("docs/architecture/builder/appgenerator-output-assembly-contract.md"),
            _read("docs/architecture/frontend/ui-system/generated-frontend-surface-contract.md"),
        ]
    )

    assert "routeAuth" in guidance
    assert "$route.projectId" in guidance
    assert "allowed" in guidance
    assert "app-owned module action" in guidance
    assert "pre-render" in guidance


def test_admingenerator_uses_admin_route_family_not_hosted_studio_paths() -> None:
    agents = _read("factory_app/workflows/AppGenerator/agents.yaml")
    start = agents.index("- name: AdminRegistryAgent")
    admin_guidance = agents[start:]

    assert "Standard generated-app admin registry paths must stay in the `/admin`" in admin_guidance
    assert 'path `/admin/access`' in admin_guidance
    assert 'path `/admin/settings`' in admin_guidance
    assert 'path `/apps/:appId/settings`' not in admin_guidance
    assert 'path `/apps/:appId/access`' not in admin_guidance

