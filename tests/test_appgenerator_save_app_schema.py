from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


def _load_save_app_schema_module():
    workspace = Path(__file__).resolve().parents[1]
    file_path = (
        workspace
        / "mozaiks-platform"
        / "app"
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
    def __init__(self) -> None:
        self.data = {}

    def set(self, key, value) -> None:
        self.data[key] = value


def _base_manifest():
    return {
        "app_name": "Ops Portal",
        "version": "1.0.0",
        "default_route": "/dashboard",
        "pages": ["Dashboard"],
    }


def _base_page():
    return {
        "name": "Dashboard",
        "route": "/dashboard",
        "title": "Dashboard",
        "layout": "grid",
        "sections": [{"id": "hero", "primitive": "Card", "config": {}}],
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
                            "primitive": "Stat",
                            "config": {
                                "label": "Total Users",
                                "value_key": "totals.users",
                                "format": "number",
                            },
                        },
                        {
                            "primitive": "Card",
                            "config": {
                                "title": "Create User",
                                "actions": [
                                    {
                                        "label": "Open Create Form",
                                        "action_type": "event",
                                        "event_type": "ui.modal.open",
                                        "payload": {"modal_id": "create-user-modal"},
                                    }
                                ],
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


def test_save_app_schema_rejects_unknown_top_level_primitive(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda: tmp_path)
    page = _base_page()
    page["sections"] = [{"id": "hero", "primitive": "Wizard", "config": {}}]

    with pytest.raises(ValueError, match="Wizard"):
        save_app_schema_module.save_app_schema(
            manifest=_base_manifest(),
            pages=[page],
            context_variables=_Context(),
        )


def test_save_app_schema_rejects_unknown_nested_grid_child(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda: tmp_path)
    page = _base_page()
    page["sections"] = [
        {
            "id": "grid",
            "primitive": "Grid",
            "config": {
                "columns": 2,
                "children": [
                    {"primitive": "Stat", "config": {"label": "Users", "value": 12}},
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
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda: tmp_path)
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


def test_save_app_schema_accepts_canonical_declarative_config(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda: tmp_path)
    context = _Context()

    result = save_app_schema_module.save_app_schema(
        manifest=_base_manifest(),
        pages=[_canonical_page()],
        theme_config_patch=None,
        context_variables=context,
    )

    dashboard_yaml = (tmp_path / "pages" / "Dashboard.yaml").read_text(encoding="utf-8")
    app_json = json.loads((tmp_path / "app.json").read_text(encoding="utf-8"))

    assert "App: Ops Portal" in result
    assert app_json["appName"] == "Ops Portal"
    assert app_json["startup"]["landing_spot"] == "/dashboard"
    assert not (tmp_path / "app.yaml").exists()
    assert "create-user-modal" in dashboard_yaml
    assert context.data["app_pages"][0]["sections"][0]["primitive"] == "Grid"


def test_save_app_schema_rejects_manifest_page_mismatch(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda: tmp_path)
    manifest = _base_manifest()
    manifest["pages"] = ["Users"]

    with pytest.raises(ValueError, match="manifest.pages"):
        save_app_schema_module.save_app_schema(
            manifest=manifest,
            pages=[_base_page()],
            context_variables=_Context(),
        )


def test_save_app_schema_rejects_duplicate_section_ids(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda: tmp_path)
    page = _base_page()
    page["sections"] = [
        {"id": "hero", "primitive": "Card", "config": {}},
        {"id": "hero", "primitive": "Empty", "config": {}},
    ]

    with pytest.raises(ValueError, match="duplicate section id"):
        save_app_schema_module.save_app_schema(
            manifest=_base_manifest(),
            pages=[page],
            context_variables=_Context(),
        )


def test_save_app_schema_rejects_invalid_form_select_options(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda: tmp_path)
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


def test_save_app_schema_writes_shell_and_deep_merges_theme_patch(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda: tmp_path)
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
            "header": {"actions": [{"id": "launch", "label": "Launch", "variant": "gradient"}]},
            "footer": {"visible": False},
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
    assert merged_shell["footer"]["visible"] is False
    assert merged_shell["footer"]["links"][0]["label"] == "Docs"

    assert context.data["app_theme_config_patch"]["theme"]["density"] == "spacious"
    assert context.data["app_shell_config"]["footer"]["visible"] is False
    assert context.data["app_asset_manifest"] is None
    assert "brand/theme_config.json" in result
    assert "config/shell.json" in result


def test_save_app_schema_writes_and_merges_asset_manifest(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda: tmp_path)
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


def test_save_app_schema_rejects_invalid_asset_manifest(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(save_app_schema_module, "_resolve_output_dir", lambda: tmp_path)

    with pytest.raises(ValueError, match="asset_manifest.assets"):
        save_app_schema_module.save_app_schema(
            manifest=_base_manifest(),
            pages=[_base_page()],
            asset_manifest={"version": "1.0", "assets": {}},
            context_variables=_Context(),
        )
