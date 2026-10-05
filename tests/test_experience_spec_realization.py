"""Design owns the finite rendering decision; local interaction is not persistence."""

from copy import deepcopy
from pathlib import Path

import pytest

from factory_app.workflows.DesignDocs.tools.save_design_doc import _canonical_experience_spec
from mozaiksai.core.workflow.outputs.structured import load_workflow_structured_outputs
from tests.test_factory_auto_tool_acceptance import factory_manager  # noqa: F401


def _page(surface="custom_react_page"):
    return {
        "name": "Focus", "route": "/focus", "ui_surface": surface,
        "layout": "full-width", "intent": "Local countdown with pause, resume and reset.",
        "sections": [],
    }


@pytest.fixture
def page_model(request):
    manager = request.getfixturevalue("factory_manager")
    assert not manager.reload_workflow("DesignDocs").get("error")
    models, _ = load_workflow_structured_outputs("DesignDocs")
    return models["UIPage"]


@pytest.mark.parametrize("surface", ["declarative_page", "custom_react_page"])
def test_production_ui_page_requires_known_explicit_renderer(page_model, surface):
    assert page_model.model_validate(_page(surface)).model_dump(mode="json")["ui_surface"] == surface


@pytest.mark.parametrize("surface", [None, "canvas_page", "agent_tool", "transition", True])
def test_other_runtime_surfaces_cannot_become_app_renderers(page_model, surface):
    with pytest.raises(ValueError):
        page_model.model_validate(_page(surface))


def test_missing_renderer_is_not_silently_declarative(page_model):
    page = _page()
    page.pop("ui_surface")
    with pytest.raises(ValueError):
        page_model.model_validate(page)
    with pytest.raises(ValueError, match="explicitly"):
        _canonical_experience_spec({"navigation_model": "One page", "pages": [page]}, surface_map={})


def test_custom_page_can_preserve_local_interaction_without_fake_primitive_config(page_model):
    page = page_model.model_validate(_page()).model_dump(mode="json")
    spec = {"navigation_model": "One page", "brand_direction": "Calm", "pages": [page]}
    before = deepcopy(spec)
    result = _canonical_experience_spec(spec, surface_map={})
    assert result == before
    assert result["pages"][0]["sections"] == []
    assert result["pages"][0]["intent"] == _page()["intent"]


def test_declarative_page_still_requires_primitive_composition():
    with pytest.raises(ValueError, match="non-empty list"):
        _canonical_experience_spec(
            {"navigation_model": "One page", "pages": [_page("declarative_page")]}, surface_map={},
        )


def test_design_prompt_preserves_typed_local_interaction():
    source = (Path(__file__).resolve().parents[1] / "factory_app/workflows/DesignDocs/agents.yaml").read_text(encoding="utf-8")
    assert "ui_surface: custom_react_page" in source
    assert "Custom pages may have empty `sections[]`" in source
    assert "Do NOT describe custom full-page React in experience_spec" not in source
    assert "saved task changes and completed-session records still belong to their declared app modules" in source
