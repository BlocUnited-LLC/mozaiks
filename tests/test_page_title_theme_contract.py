from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from pathlib import Path
from typing import get_args

import pytest
import yaml
from fastapi import HTTPException
from pydantic import ValidationError

from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import _apply_app_config_contracts
from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import scan_generated_bundle
from factory_app.workflows.AppGenerator.tools.save_app_schema import resolve_app_theme_config
from factory_app.workflows.ThemeCapture.tools.save_captured_theme import save_captured_theme
from mozaiksai.core.runtime.app.page_schema import AppPageHeaderConfig
from mozaiksai.core.runtime.app.theme_contract import PageTitleStyle, validate_page_title_theme
from mozaiksai.core.workflow.context.adapter import create_context_container
from mozaiksai.core.workflow.outputs.structured import build_models_from_config
from mozaiksai.hosts.routers import shell
from tests.factory_context import factory_context

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def workflow_models():
    compiled = {}
    for workflow in ("ThemeCapture", "AppGenerator"):
        definitions = yaml.safe_load((ROOT / "factory_app" / "workflows" / workflow / "structured_outputs.yaml").read_text(encoding="utf-8"))["models"]
        compiled[workflow] = build_models_from_config(definitions, exact_model_ids=frozenset(definitions))
    return compiled


def test_title_taxonomy_matches_both_workflow_schemas(workflow_models):
    captured = workflow_models["ThemeCapture"]["PageTitleStyle"]
    patch = workflow_models["AppGenerator"]["AppPageTitleStylePatch"]
    defaults = PageTitleStyle().model_dump()
    for field_name, field in PageTitleStyle.model_fields.items():
        for value in get_args(field.annotation):
            candidate = {**defaults, field_name: value}
            assert captured.model_validate(candidate).model_dump(mode="json") == candidate
            assert patch.model_validate({field_name: value}).model_dump(mode="json", exclude_none=True) == {field_name: value}
        for model in (captured, patch):
            with pytest.raises(ValidationError):
                model.model_validate({**defaults, field_name: "unsupported"})
    with pytest.raises(ValidationError):
        captured.model_validate({**defaults, "css": "font-size:99px"})


def test_captured_title_survives_partial_patch_and_materialization(workflow_models):
    title = workflow_models["ThemeCapture"]["PageTitleStyle"].model_validate({
        "font": "body", "weight": "regular", "size": "xl", "lineHeight": "normal",
        "letterSpacing": "tight", "textTransform": "none",
    }).model_dump(mode="json")
    captured = {"identity": {"name": "Editorial"}, "ui": {"page": {"title": title, "sectionGap": "3rem"}}}
    original = deepcopy(captured)
    patch = workflow_models["AppGenerator"]["AppThemeConfigPatch"].model_validate({
        "ui": {"page": {"title": {"weight": "medium"}}},
    }).model_dump(mode="json")
    context = create_context_container(initial={"captured_theme_config": captured})
    files = _apply_app_config_contracts(
        [{"filename": "brand/theme_config.json", "content": json.dumps(patch)}],
        app_id="editorial", app_build_plan={}, context_variables=context,
    )
    materialized = json.loads(next(file["content"] for file in files if file["filename"] == "brand/theme_config.json"))
    assert materialized["ui"]["page"] == {"sectionGap": "3rem", "title": {**title, "weight": "medium"}}
    assert resolve_app_theme_config(captured, patch) == materialized
    assert captured == original
    validate_page_title_theme(materialized)


@pytest.mark.parametrize("title", [None, "bold", {"font": "logo"}, {"weight": "900"}, {"size": "99px"}, {"lineHeight": 1}, {"letterSpacing": "2px"}, {"textTransform": "random"}, {"css": "color:red"}])
def test_invalid_explicit_title_is_rejected_before_persistence_or_promotion(title):
    config = {"ui": {"page": {"title": title}}}
    with pytest.raises(ValueError, match="ui.page.title"):
        validate_page_title_theme(config)
    with pytest.raises(ValueError, match="ui.page.title"):
        resolve_app_theme_config(config, None)
    result = asyncio.run(save_captured_theme(factory_context({"structured_output": config})))
    assert result["success"] is False
    assert "ui.page.title" in result["error"]
    errors = scan_generated_bundle({"brand/theme_config.json": json.dumps(config)})
    assert any("brand/theme_config.json: ui.page.title" in error for error in errors)


def test_runtime_loader_reports_explicit_invalid_title_and_preserves_omission(monkeypatch, tmp_path):
    path = tmp_path / "theme_config.json"
    monkeypatch.setattr(shell, "_resolve_theme_config_path", lambda: path)
    path.write_text(json.dumps({"ui": {"page": {"title": {"size": "huge"}}}}), encoding="utf-8")
    with pytest.raises(HTTPException) as caught:
        asyncio.run(shell.get_theme_config())
    assert caught.value.status_code == 422
    assert "ui.page.title.size" in caught.value.detail
    config = {"identity": {"name": "Plain"}, "ui": {"page": {"sectionGap": "1rem"}}}
    path.write_text(json.dumps(config), encoding="utf-8")
    assert asyncio.run(shell.get_theme_config()) == config
    assert PageTitleStyle().textTransform == "none"


def test_page_header_font_override_uses_only_existing_slots(workflow_models):
    for model in (AppPageHeaderConfig, workflow_models["AppGenerator"]["AppPageHeaderConfig"]):
        for value in (None, "body", "heading"):
            assert model.model_validate({"title": "Case Preserved", "title_font": value}).title == "Case Preserved"
        with pytest.raises(ValidationError):
            model.model_validate({"title": "Case Preserved", "title_font": "fantasy"})


def _patch_payload(path: str) -> dict:
    """A theme_config_patch that sets exactly the dotted ``path``."""
    payload: dict = {}
    node = payload
    parts = path.split(".")
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = None
    return payload


def _accepts(patch_model, path: str) -> bool:
    try:
        patch_model.model_validate(_patch_payload(path))
    except ValidationError:
        return False
    return True


def test_theme_patch_guidance_names_only_what_the_patch_accepts(workflow_models):
    """The AppSchemaAgent prompt and the provider-facing field description name exactly the patchable keys."""
    import re

    models = workflow_models["AppGenerator"]
    patch = models["AppThemeConfigPatch"]
    selectors = set(models["AppThemeCorePatch"].model_fields)
    agents = (ROOT / "factory_app/workflows/AppGenerator/agents.yaml").read_text(encoding="utf-8")
    bullet = next(line for line in agents.splitlines() if "`theme_config_patch` owns visual tokens only" in line)
    accepted, refused = bullet.split("It cannot patch", 1)
    named_selectors = set(re.findall(r"`([^`]+)`", re.search(r"compact `theme` selectors \(([^)]*)\)", accepted)[1]))
    assert named_selectors == selectors
    for key in ("identity", "fonts", "ui.page.title", *(f"theme.{name}" for name in selectors)):
        assert f"`{key.removeprefix('theme.')}`" in accepted
        assert _accepts(patch, key), key
    for key in ("primitives", "colors", "shadows", "ui.chat", "ui.shell", "ui.page.sectionGap"):
        assert not _accepts(patch, key), key
    for key in ("primitives", "colors", "shadows", "ui.chat", "ui.shell"):
        assert f"`{key}`" in refused
    outputs = yaml.safe_load((ROOT / "factory_app/workflows/AppGenerator/structured_outputs.yaml").read_text(encoding="utf-8"))
    description = " ".join(outputs["models"]["AppSchemaOutput"]["fields"]["theme_config_patch"]["description"].split())
    assert "accepts only `theme` selectors, `identity`, `fonts`, and `ui.page.title` deltas" in description
    assert "This may include `theme`, `primitives`" not in description


def test_scanner_reads_only_the_bundle_relative_theme_path():
    """A workspace-prefixed theme copy is rejected by layout, never title-validated as a second path."""
    bad = json.dumps({"ui": {"page": {"title": {"size": "huge"}}}})
    errors = scan_generated_bundle({"app.json": "{}", "app/brand/theme_config.json": bad})
    assert any("app/brand/theme_config.json" in error and "canonical app planes" in error for error in errors), errors
    assert not any("ui.page.title" in error for error in errors), errors
    assert any(
        "brand/theme_config.json: ui.page.title" in error
        for error in scan_generated_bundle({"app.json": "{}", "brand/theme_config.json": bad})
    )


def test_declarative_and_custom_pages_compose_under_the_retained_page_title_role():
    """Owner composition (c): declarative and custom UI in one bundle under the app's page-title role.

    The bundle holds the Messaging pack's custom page (titled through
    PageHeader, the page-title role, reading only theme tokens), an app-owned
    custom board, and a declarative page whose PageHeader omits title_font
    (the role's font) or names an existing slot, composed through the
    canonical UI registry, with an app theme that sets ``ui.page.title``. It
    scans clean, and an invalid title role fails the same bundle naming the
    field. Nothing in it reaches the removed BrandingProvider: the chat-ui
    package root exports the theme consumers (``useTheme``, ``themeProvider``)
    and no branding provider.
    """
    from factory_app.workflows._shared.generated_ui_contract import (
        resolve_registered_component_files,
    )
    from factory_app.workflows.AppGenerator.tools.assembly_phase import _merge_code_files
    from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
        resolve_managed_capability_templates,
    )
    from mozaiksai.core.runtime.app.page_schema import validate_page_schema
    from mozaiksai.core.workflow.generator_support.ui_registry_composition import (
        compose_ui_registry,
    )
    from tests.test_app_ui_composition import component, contribution

    messaging = ROOT / "factory_app" / "build_context" / "messaging"
    rows = [{"id": "messaging", "pack_source_path": str(messaging), "capability_source": "generated_module"}]
    board = component(path="ui/pages/custom/Board.jsx")
    contributions = [contribution("app:board", board)]
    emitted = resolve_managed_capability_templates(rows, ui_contributions=contributions)
    shared = compose_ui_registry(contributions, {**board, **{item["filename"]: item["content"] for item in emitted}})
    # Assembly merges the pack, the app's files and the composed roots, and compiles the data contract.
    pack = {item["filename"]: item["content"] for item in _merge_code_files(
        [{"code_files": emitted}, {"code_files": [{"filename": path, "content": content} for path, content in sorted(shared.items())]}],
        app_build_plan={"capability_packs": [{
            "capability_pack_id": "messages", "capability_source": "generated_module",
            "primary_entities": ["Thread", "Message", "ThreadRead"],
        }]},
        data_contract={"version": "1", "surfaces": [], "shared_collections": []}, context_variables={"capability_packs": rows},
    )}
    projects = {
        "schema_version": "mozaiks.app_page.v1", "name": "Projects", "route": "/projects", "title": "Projects",
        "page_type": "record_list", "layout": "full-width", "sections": [
            {"id": "header", "primitive": "PageHeader", "config": {"title": "Projects", "subtitle": "Every project"}},
            {"id": "pinned", "primitive": "PageHeader", "config": {"title": "Pinned", "title_font": "heading"}},
        ],
    }
    validate_page_schema(projects)
    for section in projects["sections"]:
        AppPageHeaderConfig.model_validate(section["config"])
    title = {"font": "heading", "weight": "semibold", "size": "xl", "lineHeight": "tight",
             "letterSpacing": "tight", "textTransform": "none"}
    files = {
        **board, **pack, "app.json": json.dumps({"appId": "ui-theme", "appName": "Theme"}),
        "ui/pages/projects.yaml": yaml.safe_dump(projects),
        "brand/theme_config.json": json.dumps({"identity": {"name": "Theme"}, "ui": {"page": {"title": title}}}),
    }
    registered, issues = resolve_registered_component_files(files, source_label="composed")
    assert not issues and {"Board", "Messages"} <= set(registered)
    messages = files["ui/pages/custom/Messages.jsx"]
    assert 'import { PageHeader } from "@mozaiks/chat-ui/ui";' in messages and "<h1" not in messages
    assert scan_generated_bundle(files, capability_packs=rows) == []

    invalid = {**files, "brand/theme_config.json": json.dumps({"ui": {"page": {"title": {**title, "size": "huge"}}}})}
    errors = scan_generated_bundle(invalid, capability_packs=rows)
    assert [error.split(":", 2)[:2] for error in errors] == [["brand/theme_config.json", " ui.page.title.size"]], errors

    assert not any("BrandingProvider" in content or "useBranding" in content for content in files.values())
    package_root = (ROOT / "chat-ui" / "src" / "index.js").read_text(encoding="utf-8")
    assert "export { default as useTheme } from './styles/useTheme';" in package_root
    assert "export * as themeProvider from './styles/themeProvider';" in package_root
    assert "BrandingProvider" not in package_root and "useBranding" not in package_root
    assert not (ROOT / "chat-ui" / "src" / "providers" / "BrandingProvider.jsx").exists()
