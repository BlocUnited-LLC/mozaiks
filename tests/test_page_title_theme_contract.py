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
