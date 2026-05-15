from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from tests.import_utils import import_module_directly


ui_surface_taxonomy = import_module_directly("mozaiksai.core.workflow.ui_surface_taxonomy")
ui_primitives = import_module_directly("mozaiksai.core.workflow.ui_primitives")


def _workspace() -> Path:
    return Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (_workspace() / relative_path).read_text(encoding="utf-8")


def _read_yaml(relative_path: str):
    return yaml.safe_load(_read(relative_path))


def test_ui_surface_ids_match_generator_contracts() -> None:
    assert ui_surface_taxonomy.get_ui_surface_ids() == (
        "declarative_page",
        "custom_react_page",
        "agent_tool",
        "transition",
    )


def test_ui_surface_guidance_defines_ownership_and_paths() -> None:
    guidance = ui_surface_taxonomy.format_ui_surface_taxonomy_guidance()

    for expected in (
        "`declarative_page`",
        "`custom_react_page`",
        "`agent_tool`",
        "`transition`",
        "`ui/pages/*.yaml`",
        "`ui/route_manifest.json`",
        "`extended_orchestration/extension_registry.json`",
        "Use `declarative_page` by default",
        "Do not create placeholder dashboards",
    ):
        assert expected in guidance


def test_ui_surface_validation_rejects_unknown_ids() -> None:
    with pytest.raises(ValueError, match="unsupported UI surfaces"):
        ui_surface_taxonomy.validate_ui_surface_ids(
            ["declarative_page", "dashboard_page"],
            context="test",
        )


def test_appgenerator_pages_have_finite_ui_surface_contract() -> None:
    structured = _read_yaml("factory_app/workflows/AppGenerator/structured_outputs.yaml")
    field = structured["models"]["AppBuildPage"]["fields"]["ui_surface"]

    assert field["type"] == "literal"
    assert field["values"] == ["declarative_page", "custom_react_page"]


def test_agentgenerator_surface_contract_uses_matching_ids() -> None:
    structured = _read_yaml("factory_app/workflows/AgentGenerator/structured_outputs.yaml")

    ui_contract = structured["models"]["UIToolContract"]["fields"]["surface_kind"]
    ui_requirement = structured["models"]["WorkflowUIComponent"]["fields"]["surface_kind"]

    assert ui_contract["values"] == ["agent_tool"]
    assert ui_requirement["values"] == ["agent_tool", "transition"]


def test_generator_hooks_inject_surface_taxonomy() -> None:
    app_hook = _read("factory_app/workflows/AppGenerator/tools/hook_primitive_catalog.py")
    agent_hook = _read("factory_app/workflows/AgentGenerator/tools/hook_primitive_catalog.py")
    universal_hook = _read("factory_app/workflows/AgentGenerator/tools/hook_universal_prompts.py")

    assert "format_ui_surface_taxonomy_guidance" in app_hook
    assert "format_generated_page_ui_primitive_guidance" in app_hook
    assert '"[UI SURFACE TAXONOMY]"' in app_hook
    assert "format_ui_surface_taxonomy_guidance" in agent_hook
    assert "format_generated_component_ui_primitive_guidance" in agent_hook
    assert '"[UI SURFACE TAXONOMY]"' in agent_hook
    assert "[UI SURFACE LANE DISCIPLINE]" in universal_hook
    assert "Do NOT generate persistent app pages" in universal_hook


def test_frontend_surface_docs_reference_taxonomy() -> None:
    doc = _read("docs/architecture/frontend/ui-system/generated-frontend-surface-contract.md")

    assert "Finite surface IDs" in doc
    assert "mozaiksai/core/workflow/ui_surface_taxonomy.py" in doc
    assert "`declarative_page`" in doc
    assert "`custom_react_page`" in doc
    assert "`agent_tool`" in doc
    assert "`transition`" in doc


def test_global_primitives_own_reusable_console_patterns() -> None:
    registry = _read("chat-ui/src/ui/page-renderer/PrimitiveRegistry.js")
    schemas = _read("chat-ui/src/ui/page-renderer/primitive_schemas.json")
    console_shared = _read("factory_app/app/ui/components/ConsoleShared.jsx")

    for primitive_name in (
        "StatusPill",
        "SurfaceCard",
        "Metric",
        "Panel",
        "SegmentedBar",
        "InlineEmptyState",
        "LoadingState",
        "ErrorState",
    ):
        assert primitive_name in registry
        assert f'"{primitive_name}"' in schemas

    assert "from '@mozaiks/chat-ui/ui'" in console_shared
    assert "export function StatusPill" not in console_shared
    assert "export function SurfaceCard" not in console_shared
    assert "export function Metric" not in console_shared
    assert not (_workspace() / "factory_app/app/admin/pages/ConsoleShared.jsx").exists()
    assert not (_workspace() / "factory_app/app/admin/pages/ConsolePrimitives.jsx").exists()


def test_component_primitive_guidance_uses_public_ui_import() -> None:
    guidance = ui_primitives.format_component_ui_primitive_guidance()

    assert "import {" in guidance
    assert "from '@mozaiks/chat-ui/ui'" in guidance
    assert "from '@mozaiks/chat-ui/ui/primitives/" not in guidance


def test_primitive_schema_catalog_is_generated() -> None:
    result = subprocess.run(
        ["node", "scripts/export-primitive-schemas.js", "--check"],
        cwd=_workspace(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
