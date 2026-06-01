from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


def _load_save_app_schema_module():
    workspace = Path(__file__).resolve().parents[1]
    file_path = (
        workspace
        / "factory_app"
        / "workflows"
        / "AppGenerator"
        / "tools"
        / "save_app_schema.py"
    )
    module_name = "tests.appgenerator_save_app_schema_direct"
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load module spec for {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


save_app_schema_module = _load_save_app_schema_module()


class _Context:
    def __init__(self, initial=None) -> None:
        self.data = dict(initial or {})

    def set(self, key, value) -> None:
        self.data[key] = value

    def get(self, key, default=None):
        return self.data.get(key, default)


def _base_manifest():
    return {
        "app_name": "Ops Portal",
        "version": "1.0.0",
        "default_route": "/dashboard",
        "pages": ["Dashboard"],
        "custom_routes": [],
    }


def _base_page():
    return {
        "name": "Dashboard",
        "route": "/dashboard",
        "title": "Dashboard",
        "layout": "grid",
        "sections": [{"id": "hero", "primitive": "Panel", "config": {"title": "Overview"}}],
    }


def _canonical_page():
    return {
        "name": "Dashboard",
        "route": "/dashboard",
        "title": "Dashboard",
        "layout": "grid",
        "sections": [
            {
                "id": "overview",
                "primitive": "Grid",
                "config": {
                    "columns": 2,
                    "gap": "md",
                    "children": [
                        {
                            "primitive": "Metric",
                            "config": {
                                "label": "Total Users",
                                "value": 12,
                                "detail": "Active user count",
                            },
                        },
                        {
                            "primitive": "Panel",
                            "config": {
                                "title": "Create User",
                                "subtitle": "Open the create user modal from the page actions.",
                            },
                        },
                    ],
                },
            },
            {
                "id": "user-table",
                "primitive": "DataTable",
                "config": {
                    "columns": [
                        {"key": "name", "label": "Name", "sortable": True},
                        "email",
                    ],
                    "api_endpoint": "/api/users",
                    "selection": "single",
                    "actions": [
                        {
                            "id": "open-user",
                            "label": "Open User",
                            "action_type": "navigate",
                            "href": "/users/{id}",
                            "requires_selection": True,
                        }
                    ],
                },
            },
            {
                "id": "create-user-modal",
                "primitive": "Modal",
                "config": {
                    "title": "Add User",
                    "size": "medium",
                    "children": [
                        {
                            "primitive": "Form",
                            "config": {
                                "fields": [
                                    {"name": "email", "label": "Email", "type": "email", "required": True},
                                    {
                                        "name": "role",
                                        "label": "Role",
                                        "type": "select",
                                        "options": [
                                            {"label": "Admin", "value": "admin"},
                                            {"label": "Member", "value": "member"},
                                        ],
                                    },
                                ],
                                "submit_label": "Create User",
                                "submit_action": {
                                    "label": "Submit Create User",
                                    "action_type": "submit",
                                    "href": "/api/users",
                                },
                            },
                        }
                    ],
                },
            },
        ],
    }


def _custom_route_bundle():
    return {
        "route_manifest": [
            {
                "id": "deal-room",
                "label": "Deal Room",
                "path": "/deal-room",
                "component": "InvestorDealRoomPage",
                "requiresAuth": True,
                "order": 2,
                "meta": {"title": "Investor Deal Room"},
                "purpose": "Complex investor collaboration surface with live state and nested interactions.",
            }
        ],
        "page_files": [
            {
                "route_id": "deal-room",
                "path": "ui/pages/custom/InvestorDealRoom.jsx",
                "component_name": "InvestorDealRoom",
                "registry_key": "InvestorDealRoomPage",
                "purpose": "Bounded custom marketplace deal room page.",
                "contract_refs": ["custom_route_bundle.route_manifest[deal-room]"],
                "content": (
                    "import { PageFrame, useChatUI } from '@mozaiks/chat-ui';\n\n"
                    "export default function InvestorDealRoom() {\n"
                    "  const { user } = useChatUI();\n"
                    "  return (\n"
                    "    <PageFrame name=\"investor-deal-room\" layout=\"full-width\">\n"
                    "      <section className=\"flex flex-col gap-4\">\n"
                    "        <h1 className=\"text-2xl font-semibold text-foreground\">Deal Room</h1>\n"
                    "        <p className=\"text-sm text-muted-foreground\">{user?.displayName || 'Investor'}</p>\n"
                    "      </section>\n"
                    "    </PageFrame>\n"
                    "  );\n"
                    "}\n"
                ),
            }
        ],
    }


def _data_contract():
    return {
        "version": "1",
        "app_id": "app_123",
        "artifact_version_id": None,
        "surfaces": [
            {
                "surface_id": "users",
                "surface_kind": "module",
                "collections": [
                    {
                        "name": "users",
                        "scope": "app",
                        "ownership": {"surface_id": "users", "surface_kind": "module"},
                        "fields": [
                            {"name": "app_id", "type": "string", "required": True},
                            {"name": "user_id", "type": "string", "required": True},
                        ],
                        "indexes": [
                            {"keys": [["app_id", 1], ["user_id", 1]], "unique": True}
                        ],
                        "search_by": "user_id",
                        "lifecycle": {
                            "write_mode": "module_action",
                            "migration_policy": "additive_only",
                        },
                    }
                ],
            }
        ],
        "shared_collections": [],
        "policies": {
            "default_scope_field": "app_id",
            "allow_destructive_migrations": False,
        },
    }


def test_save_app_schema_rejects_unknown_top_level_primitive(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda **_: tmp_path)
    page = _base_page()
    page["sections"] = [{"id": "hero", "primitive": "Wizard", "config": {}}]

    with pytest.raises(ValueError, match="Wizard"):
        save_app_schema_module.save_app_schema(
            manifest=_base_manifest(),
            pages=[page],
            context_variables=_Context(),
        )


def test_save_app_schema_rejects_unknown_nested_grid_child(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda **_: tmp_path)
    page = _base_page()
    page["sections"] = [
        {
            "id": "grid",
            "primitive": "Grid",
            "config": {
                "columns": 2,
                "children": [
                    {"primitive": "Metric", "config": {"label": "Users", "value": 12}},
                    {"primitive": "Wizard", "config": {}},
                ]
            },
        }
    ]

    with pytest.raises(ValueError, match="Wizard"):
        save_app_schema_module.save_app_schema(
            manifest=_base_manifest(),
            pages=[page],
            context_variables=_Context(),
        )


def test_save_app_schema_accepts_empty_primitive(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda **_: tmp_path)
    context = _Context()
    page = _base_page()
    page["sections"] = [{"id": "empty-state", "primitive": "Empty", "config": {}}]

    result = save_app_schema_module.save_app_schema(
        manifest=_base_manifest(),
        pages=[page],
        theme_config_patch=None,
        context_variables=context,
    )

    assert "App: Ops Portal" in result
    assert "Empty" in context.data["available_page_primitives"]


def test_save_app_schema_accepts_workflow_action(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda **_: tmp_path)
    context = _Context()
    page = _base_page()
    page["sections"] = [
        {
            "id": "launch-workflow",
            "primitive": "Button",
            "config": {
                "label": "Open Customer Support",
                "action_type": "workflow",
                "workflow_id": "CustomerSupport",
                "context_variables": {
                    "customer_id": "{id}",
                    "source_page": "dashboard",
                },
            },
        }
    ]

    result = save_app_schema_module.save_app_schema(
        manifest=_base_manifest(),
        pages=[page],
        theme_config_patch=None,
        context_variables=context,
    )

    assert "App: Ops Portal" in result


def test_save_app_schema_accepts_canonical_declarative_config(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda **_: tmp_path)
    context = _Context()

    result = save_app_schema_module.save_app_schema(
        manifest=_base_manifest(),
        pages=[_canonical_page()],
        theme_config_patch=None,
        context_variables=context,
    )

    dashboard_yaml = (tmp_path / "ui" / "pages" / "Dashboard.yaml").read_text(encoding="utf-8")
    app_json = json.loads((tmp_path / "app.json").read_text(encoding="utf-8"))

    assert "App: Ops Portal" in result
    assert app_json["appName"] == "Ops Portal"
    assert app_json["startup"]["landing_spot"] == "/dashboard"
    assert not (tmp_path / "app.yaml").exists()
    assert "create-user-modal" in dashboard_yaml
    assert context.data["app_pages"][0]["sections"][0]["primitive"] == "Grid"


def test_save_app_schema_writes_custom_route_bundle(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda **_: tmp_path)
    context = _Context()
    manifest = {
        "app_name": "Investor Room",
        "version": "1.0.0",
        "default_route": "/deal-room",
        "pages": [],
        "custom_routes": ["deal-room"],
    }

    result = save_app_schema_module.save_app_schema(
        manifest=manifest,
        pages=[],
        custom_route_bundle=_custom_route_bundle(),
        context_variables=context,
    )

    route_manifest = json.loads((tmp_path / "ui" / "route_manifest.json").read_text(encoding="utf-8"))
    custom_page = (tmp_path / "ui" / "pages" / "custom" / "InvestorDealRoom.jsx").read_text(encoding="utf-8")
    ui_index = (tmp_path / "ui" / "index.js").read_text(encoding="utf-8")
    app_json = json.loads((tmp_path / "app.json").read_text(encoding="utf-8"))

    assert "ui/route_manifest.json" in result
    assert route_manifest["pages"][0]["id"] == "deal-room"
    assert "PageFrame" in custom_page
    assert "useChatUI" in custom_page
    assert "registerComponent('InvestorDealRoomPage'" in ui_index
    assert app_json["startup"]["landing_spot"] == "/deal-room"
    assert context.data["app_custom_route_bundle"]["route_manifest"][0]["component"] == "InvestorDealRoomPage"
    assert context.data["app_schema_ready"] is True


def test_save_app_schema_preserves_custom_route_requiresrole_metadata(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda **_: tmp_path)
    context = _Context()
    manifest = {
        "app_name": "Investor Room",
        "version": "1.0.0",
        "default_route": "/deal-room",
        "pages": [],
        "custom_routes": ["deal-room"],
    }
    custom_bundle = _custom_route_bundle()
    custom_bundle["route_manifest"][0]["meta"]["requiresRole"] = "admin"

    save_app_schema_module.save_app_schema(
        manifest=manifest,
        pages=[],
        custom_route_bundle=custom_bundle,
        context_variables=context,
    )

    route_manifest = json.loads((tmp_path / "ui" / "route_manifest.json").read_text(encoding="utf-8"))

    assert route_manifest["pages"][0]["meta"]["requiresRole"] == "admin"
    assert context.data["app_custom_route_bundle"]["route_manifest"][0]["meta"]["requiresRole"] == "admin"



def test_save_app_schema_canonicalizes_manifest_page_index(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda **_: tmp_path)
    context = _Context()
    manifest = _base_manifest()
    manifest["pages"] = ["StaleName"]

    save_app_schema_module.save_app_schema(
        manifest=manifest,
        pages=[_base_page()],
        context_variables=context,
    )

    assert context.data["app_manifest"]["pages"] == ["Dashboard"]


def test_save_app_schema_rejects_custom_route_overlap(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda **_: tmp_path)
    manifest = _base_manifest()
    manifest["custom_routes"] = ["deal-room"]
    custom_bundle = _custom_route_bundle()
    custom_bundle["route_manifest"][0]["path"] = "/dashboard"

    with pytest.raises(ValueError, match="must not overlap"):
        save_app_schema_module.save_app_schema(
            manifest=manifest,
            pages=[_base_page()],
            custom_route_bundle=custom_bundle,
            context_variables=_Context(),
        )


def test_save_app_schema_rejects_custom_route_component_without_matching_registration(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda **_: tmp_path)
    manifest = {
        "app_name": "Analytics App",
        "version": "1.0.0",
        "default_route": "/analytics",
        "pages": [],
        "custom_routes": ["analytics"],
    }
    custom_bundle = _custom_route_bundle()
    custom_bundle["route_manifest"][0]["id"] = "analytics"
    custom_bundle["route_manifest"][0]["path"] = "/analytics"
    custom_bundle["route_manifest"][0]["component"] = "AnalyticsPage"
    custom_bundle["page_files"][0]["route_id"] = "analytics"
    custom_bundle["page_files"][0]["path"] = "ui/pages/custom/AnalyticsPage.jsx"
    custom_bundle["page_files"][0]["component_name"] = "AnalyticsPage"
    custom_bundle["page_files"][0]["registry_key"] = "WrongPage"

    with pytest.raises(ValueError) as exc_info:
        save_app_schema_module.save_app_schema(
            manifest=manifest,
            pages=[],
            custom_route_bundle=custom_bundle,
            context_variables=_Context(),
        )

    message = str(exc_info.value)
    assert "WrongPage" in message
    assert "AnalyticsPage" in message
    assert "/analytics" in message
    assert "ui/index.js registerComponent key" in message


def test_save_app_schema_rejects_custom_page_file_without_route_owner(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda **_: tmp_path)
    manifest = {
        "app_name": "Reports App",
        "version": "1.0.0",
        "default_route": "/reports",
        "pages": [],
        "custom_routes": ["reports"],
    }
    custom_bundle = _custom_route_bundle()
    custom_bundle["route_manifest"][0]["id"] = "reports"
    custom_bundle["route_manifest"][0]["path"] = "/reports"
    custom_bundle["route_manifest"][0]["component"] = "ReportsPage"
    custom_bundle["page_files"][0]["route_id"] = "orphan"
    custom_bundle["page_files"][0]["path"] = "ui/pages/custom/OrphanPage.jsx"
    custom_bundle["page_files"][0]["component_name"] = "OrphanPage"
    custom_bundle["page_files"][0]["registry_key"] = "OrphanPage"

    with pytest.raises(ValueError) as exc_info:
        save_app_schema_module.save_app_schema(
            manifest=manifest,
            pages=[],
            custom_route_bundle=custom_bundle,
            context_variables=_Context(),
        )

    message = str(exc_info.value)
    assert "route_id 'orphan'" in message
    assert "route path, component key, custom page file, and ui/index.js registration" in message


def test_save_app_schema_rejects_duplicate_section_ids(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda **_: tmp_path)
    page = _base_page()
    page["sections"] = [
        {"id": "hero", "primitive": "Panel", "config": {}},
        {"id": "hero", "primitive": "Empty", "config": {}},
    ]

    with pytest.raises(ValueError, match="duplicate section id"):
        save_app_schema_module.save_app_schema(
            manifest=_base_manifest(),
            pages=[page],
            context_variables=_Context(),
        )


def test_save_app_schema_rejects_invalid_form_select_options(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda **_: tmp_path)
    page = _base_page()
    page["sections"] = [
        {
            "id": "create-user",
            "primitive": "Form",
            "config": {
                "fields": [
                    {
                        "name": "role",
                        "label": "Role",
                        "type": "select",
                        "options": ["admin"],
                    }
                ]
            },
        }
    ]

    with pytest.raises(ValueError, match=r"options\[0\] must be an object"):
        save_app_schema_module.save_app_schema(
            manifest=_base_manifest(),
            pages=[page],
            context_variables=_Context(),
        )


def test_save_app_schema_strips_blank_optional_form_placeholder(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda **_: tmp_path)
    page = _base_page()
    page["sections"] = [
        {
            "id": "settings",
            "primitive": "Form",
            "config": {
                "fields": [
                    {
                        "name": "sla_hours",
                        "label": "SLA hours",
                        "type": "number",
                        "placeholder": "",
                    }
                ],
                "submit_action": {
                    "label": "Save",
                    "action_type": "submit",
                    "href": "/api/modules/tickets/save_settings",
                    "event_type": "",
                },
            },
        }
    ]

    save_app_schema_module.save_app_schema(
        manifest=_base_manifest(),
        pages=[page],
        context_variables=_Context(),
    )

    dashboard_yaml = (tmp_path / "ui" / "pages" / "Dashboard.yaml").read_text(
        encoding="utf-8"
    )
    assert "placeholder" not in dashboard_yaml
    assert "event_type" not in dashboard_yaml
    assert "href: /api/modules/tickets/save_settings" in dashboard_yaml


def test_save_app_schema_rejects_submit_action_without_resolvable_href(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda **_: tmp_path)
    page = _base_page()
    page["sections"] = [
        {
            "id": "settings",
            "primitive": "Form",
            "config": {
                "fields": [
                    {
                        "name": "default_queue",
                        "label": "Default queue",
                        "type": "text",
                        "required": True,
                    }
                ],
                "submit_label": "Save settings",
                "submit_action": {
                    "label": "Save settings",
                    "action_type": "submit",
                },
            },
        }
    ]

    with pytest.raises(ValueError, match=r"submit_action\.href is required for submit actions"):
        save_app_schema_module.save_app_schema(
            manifest=_base_manifest(),
            pages=[page],
            context_variables=_Context(),
        )


def test_save_app_schema_rejects_api_endpoint_query_strings(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda **_: tmp_path)
    page = _base_page()
    page["sections"] = [
        {
            "id": "tickets",
            "primitive": "DataTable",
            "config": {
                "columns": [{"key": "title", "label": "Title"}],
                "api_endpoint": "/api/modules/tickets/list_tickets?limit=12",
            },
        }
    ]

    with pytest.raises(ValueError, match="query strings"):
        save_app_schema_module.save_app_schema(
            manifest=_base_manifest(),
            pages=[page],
            context_variables=_Context(),
        )


def test_save_app_schema_derives_missing_submit_href_from_build_plan(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda **_: tmp_path)
    context = _Context(
        {
            "app_build_plan": {
                "modules": [{"module_id": "tickets"}],
                "pages": [
                    {
                        "name": "QueueSettings",
                        "route": "/dashboard",
                        "primary_actions": ["save_settings"],
                    }
                ],
            }
        }
    )
    page = _base_page()
    page["sections"] = [
        {
            "id": "settings",
            "primitive": "Form",
            "config": {
                "fields": [
                    {
                        "name": "default_queue",
                        "label": "Default queue",
                        "type": "text",
                    }
                ],
                "submit_label": "Save settings",
                "submit_action": {
                    "label": "Save settings",
                    "action_type": "submit",
                },
            },
        }
    ]

    save_app_schema_module.save_app_schema(
        manifest=_base_manifest(),
        pages=[page],
        context_variables=context,
    )

    dashboard_yaml = (tmp_path / "ui" / "pages" / "Dashboard.yaml").read_text(
        encoding="utf-8"
    )
    assert "href: /api/modules/tickets/save_settings" in dashboard_yaml


def test_save_app_schema_writes_shell_and_deep_merges_theme_patch(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda **_: tmp_path)
    context = _Context()

    brand_dir = tmp_path / "brand"
    config_dir = tmp_path / "config"
    brand_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)

    (brand_dir / "theme_config.json").write_text(
        json.dumps(
            {
                "theme": {"appearance": "dark", "density": "comfortable"},
                "ui": {"page": {"sectionGap": "2rem"}},
                "colors": {"primary": {"main": "#06b6d4"}},
            }
        ),
        encoding="utf-8",
    )
    (config_dir / "shell.json").write_text(
        json.dumps(
            {
                "header": {"logo": {"src": "logo.svg", "href": "/"}},
                "footer": {"links": [{"label": "Docs", "href": "/docs"}], "visible": True},
            }
        ),
        encoding="utf-8",
    )

    result = save_app_schema_module.save_app_schema(
        manifest=_base_manifest(),
        pages=[_base_page()],
        theme_config_patch={
            "theme": {"density": "spacious"},
            "ui": {"page": {"sectionGap": "2.5rem"}, "shell": {"header": {"height": "4.5rem"}}},
        },
        shell_config={
            "header": {
                "actions": [
                    {
                        "id": "launch",
                        "intent": "custom",
                        "label": "Launch",
                        "path": "/launch",
                        "variant": "gradient",
                        "variants": [
                            {
                                "when": {"surface": "workflow_session"},
                                "label": "Open App",
                                "pathTemplate": "/apps/{appId}",
                                "fallbackPath": "/apps",
                            }
                        ],
                    }
                ]
            },
            "footer": {"visible": False},
            "navigation": {
                "policy": {
                    "desktop": {"global": "header", "local": "sidebar", "footer": "visible"},
                    "mobile": {"global": "bottomBar", "local": "sheet", "footer": "hidden"},
                    "maxMobileItems": 4,
                    "autoFromPages": False,
                }
            },
            "chrome": {
                "defaultMode": "standard",
                "modes": {
                    "conversation": {
                        "desktop": {"header": True, "footer": False, "bottomBar": False, "localNav": False},
                        "mobile": {"header": True, "footer": False, "bottomBar": False, "localNav": False},
                    }
                },
            },
        },
        context_variables=context,
    )

    merged_theme = json.loads((brand_dir / "theme_config.json").read_text(encoding="utf-8"))
    merged_shell = json.loads((config_dir / "shell.json").read_text(encoding="utf-8"))

    assert merged_theme["theme"]["appearance"] == "dark"
    assert merged_theme["theme"]["density"] == "spacious"
    assert merged_theme["ui"]["page"]["sectionGap"] == "2.5rem"
    assert merged_theme["ui"]["shell"]["header"]["height"] == "4.5rem"
    assert merged_theme["colors"]["primary"]["main"] == "#06b6d4"

    assert merged_shell["header"]["logo"]["src"] == "logo.svg"
    assert merged_shell["header"]["actions"][0]["label"] == "Launch"
    assert merged_shell["header"]["actions"][0]["variants"][0]["when"]["surface"] == "workflow_session"
    assert merged_shell["footer"]["visible"] is False
    assert merged_shell["footer"]["links"][0]["label"] == "Docs"
    assert merged_shell["navigation"]["policy"]["mobile"]["local"] == "sheet"
    assert merged_shell["navigation"]["policy"]["maxMobileItems"] == 4
    assert merged_shell["chrome"]["defaultMode"] == "standard"
    assert merged_shell["chrome"]["modes"]["conversation"]["mobile"]["bottomBar"] is False

    assert context.data["app_theme_config_patch"]["theme"]["density"] == "spacious"
    assert context.data["app_shell_config"]["footer"]["visible"] is False
    assert context.data["app_asset_manifest"] is None
    assert "brand/theme_config.json" in result
    assert "config/shell.json" in result


def test_save_app_schema_rejects_invalid_shell_mode(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda **_: tmp_path)
    page = _base_page()
    page["shell_mode"] = "floating-toolbar"

    with pytest.raises(ValueError, match="shell_mode"):
        save_app_schema_module.save_app_schema(
            manifest=_base_manifest(),
            pages=[page],
            context_variables=_Context(),
        )


def test_save_app_schema_rejects_non_contract_shell_shortcut_fields(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda **_: tmp_path)

    with pytest.raises(ValueError, match="desktopHeader"):
        save_app_schema_module.save_app_schema(
            manifest=_base_manifest(),
            pages=[_base_page()],
            shell_config={"shortcuts": {"desktopHeader": ["dashboard"]}},
            context_variables=_Context(),
        )


def test_save_app_schema_rejects_non_contract_shell_policy_fields(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda **_: tmp_path)

    with pytest.raises(ValueError, match="max_mobile_items"):
        save_app_schema_module.save_app_schema(
            manifest=_base_manifest(),
            pages=[_base_page()],
            shell_config={"navigation": {"policy": {"max_mobile_items": 4}}},
            context_variables=_Context(),
        )


def test_save_app_schema_rejects_non_contract_chrome_fields(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda **_: tmp_path)

    with pytest.raises(ValueError, match="default_mode"):
        save_app_schema_module.save_app_schema(
            manifest=_base_manifest(),
            pages=[_base_page()],
            shell_config={"chrome": {"default_mode": "standard"}},
            context_variables=_Context(),
        )


def test_save_app_schema_rejects_route_override_shell_actions(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda **_: tmp_path)

    with pytest.raises(ValueError, match="route_overrides"):
        save_app_schema_module.save_app_schema(
            manifest=_base_manifest(),
            pages=[_base_page()],
            shell_config={
                "header": {
                    "actions": [
                        {
                            "id": "create",
                            "label": "Create",
                            "path": "/create",
                            "route_overrides": [{"when": {"pathPrefix": "/chat"}, "path": "/apps"}],
                        }
                    ]
                }
            },
            context_variables=_Context(),
        )


def test_save_app_schema_rejects_path_based_shell_action_variant(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda **_: tmp_path)

    with pytest.raises(ValueError, match="when.path"):
        save_app_schema_module.save_app_schema(
            manifest=_base_manifest(),
            pages=[_base_page()],
            shell_config={
                "header": {
                    "actions": [
                        {
                            "id": "create",
                            "label": "Create",
                            "path": "/create",
                            "variants": [
                                {"when": {"path": "/chat"}, "label": "Open", "path": "/apps"}
                            ],
                        }
                    ]
                }
            },
            context_variables=_Context(),
        )


def test_save_app_schema_writes_and_merges_asset_manifest(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda **_: tmp_path)
    context = _Context()

    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "asset_manifest.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "assets": [
                    {
                        "asset_id": "brand-logo",
                        "kind": "logo",
                        "source": "local",
                        "path": "brand/assets/logo.svg",
                        "url": None,
                        "alt": "Brand logo",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = save_app_schema_module.save_app_schema(
        manifest=_base_manifest(),
        pages=[_base_page()],
        asset_manifest={
            "version": "1.0",
            "assets": [
                {
                    "asset_id": "landing-hero",
                    "kind": "hero_image",
                    "source": "remote",
                    "path": None,
                    "url": "https://cdn.example.com/hero.png",
                    "alt": "Landing hero image",
                    "usage": ["Dashboard"],
                }
            ],
        },
        context_variables=context,
    )

    merged_manifest = json.loads((config_dir / "asset_manifest.json").read_text(encoding="utf-8"))
    assert merged_manifest["version"] == "1.0"
    assert merged_manifest["assets"][0]["asset_id"] == "landing-hero"
    assert context.data["app_asset_manifest"]["assets"][0]["asset_id"] == "landing-hero"
    assert "config/asset_manifest.json" in result


def test_save_app_schema_writes_data_contract_from_context(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda **_: tmp_path)
    context = _Context({"data_contract": _data_contract()})

    result = save_app_schema_module.save_app_schema(
        manifest=_base_manifest(),
        pages=[_base_page()],
        context_variables=context,
    )

    data_contract = json.loads((tmp_path / "config" / "data.json").read_text(encoding="utf-8"))
    assert data_contract["surfaces"][0]["surface_id"] == "users"
    assert context.data["app_data_contract"]["policies"]["default_scope_field"] == "app_id"
    assert "config/data.json" in result


def test_save_app_schema_rejects_invalid_asset_manifest(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda **_: tmp_path)

    with pytest.raises(ValueError, match="asset_manifest.assets"):
        save_app_schema_module.save_app_schema(
            manifest=_base_manifest(),
            pages=[_base_page()],
            asset_manifest={"version": "1.0", "assets": {}},
            context_variables=_Context(),
        )


def test_save_app_schema_writes_to_generated_artifact_root(monkeypatch, tmp_path: Path) -> None:
    generated_root = tmp_path / "generated"
    monkeypatch.setenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", str(generated_root))
    monkeypatch.delenv("MOZAIKS_APP_ID", raising=False)
    context = _Context({"app_id": "app/one", "build_id": "build one"})

    save_app_schema_module.save_app_schema(
        manifest=_base_manifest(),
        pages=[_base_page()],
        context_variables=context,
    )

    output_dir = generated_root / "apps" / "app-one" / "build-one" / "app"
    assert (output_dir / "app.json").exists()
    assert (output_dir / "ui" / "pages" / "Dashboard.yaml").exists()
    assert context.data["generated_app_dir"] == str(output_dir)


def test_save_app_schema_defaults_generated_root_to_repo_generated(monkeypatch) -> None:
    workspace = Path(__file__).resolve().parents[1]
    monkeypatch.delenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", raising=False)

    assert save_app_schema_module._resolve_generated_artifacts_root() == (workspace / "generated").resolve()


def test_promote_generated_app_copies_allowlisted_artifacts(tmp_path: Path) -> None:
    source = tmp_path / "generated" / "apps" / "app-1" / "build-1" / "app"
    target = tmp_path / "active-app"
    (source / "ui" / "pages").mkdir(parents=True)
    (source / "brand").mkdir()
    (source / "config").mkdir()
    (source / "services" / "integrations").mkdir(parents=True)
    (source / "app.json").write_text('{"appName": "Demo"}', encoding="utf-8")
    (source / "ui" / "pages" / "Dashboard.yaml").write_text("name: Dashboard\n", encoding="utf-8")
    (source / "brand" / "theme_config.json").write_text("{}", encoding="utf-8")
    (source / "config" / "shell.json").write_text("{}", encoding="utf-8")
    (source / "services" / "integrations" / "email_client.py").write_text(
        "class EmailClient: pass\n",
        encoding="utf-8",
    )

    result = save_app_schema_module.promote_generated_app(source, target)

    assert result["status"] == "success"
    assert sorted(result["copied"]) == ["app.json", "brand", "config", "services", "ui"]
    assert (target / "app.json").exists()
    assert (target / "ui" / "pages" / "Dashboard.yaml").exists()
    assert (target / "services" / "integrations" / "email_client.py").exists()
