"""
Tests for AppGenerator new L3 primitives: Timeline, CodeBlock, ProgressTracker,
AlertBanner, ActionButton, FileList.

Covers:
  - All 6 new primitives are present in get_page_ui_primitive_names()
  - validate_page_ui_primitives accepts each new primitive individually
  - validate_page_ui_primitives accepts all 17 shipped primitives at once
  - validate_page_ui_primitives rejects unknown names with an actionable message
  - validate_page_ui_primitives handles None / empty / whitespace / duplicates
  - structured_outputs.yaml AppPageSection.primitive contains all 6 new names
  - format_page_ui_primitive_guidance() mentions all 6 new names
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest
import yaml

from tests.import_utils import import_module_directly

ui_primitives = import_module_directly("mozaiksai.core.workflow.ui_primitives")

_REPO_ROOT = Path(__file__).resolve().parents[1]
_STRUCTURED_OUTPUTS = (
    _REPO_ROOT
    / "factory_app"
    / "workflows"
    / "AppGenerator"
    / "structured_outputs.yaml"
)

_NEW_PRIMITIVES = (
    "Timeline",
    "CodeBlock",
    "ProgressTracker",
    "AlertBanner",
    "ActionButton",
    "FileList",
)

_ALL_SHIPPED = (
    "ActionButton",
    "Alert",
    "AlertBanner",
    "Badge",
    "Button",
    "Card",
    "CodeBlock",
    "DataTable",
    "Empty",
    "FileList",
    "Form",
    "Grid",
    "Modal",
    "ProgressTracker",
    "Skeleton",
    "Stat",
    "Timeline",
)


# ---------------------------------------------------------------------------
# get_page_ui_primitive_names()
# ---------------------------------------------------------------------------

def test_get_page_ui_primitive_names_returns_all_17() -> None:
    names = ui_primitives.get_page_ui_primitive_names()
    assert set(names) == set(_ALL_SHIPPED), (
        f"Unexpected catalog. Missing: {set(_ALL_SHIPPED) - set(names)}. "
        f"Extra: {set(names) - set(_ALL_SHIPPED)}"
    )


def test_get_page_ui_primitive_names_is_sorted_tuple() -> None:
    names = ui_primitives.get_page_ui_primitive_names()
    assert isinstance(names, tuple)
    assert list(names) == sorted(names)


class TestNewPrimitivesInCatalog:
    @pytest.mark.parametrize("name", _NEW_PRIMITIVES)
    def test_new_primitive_in_catalog(self, name: str) -> None:
        names = ui_primitives.get_page_ui_primitive_names()
        assert name in names, f"'{name}' missing from get_page_ui_primitive_names()"


# ---------------------------------------------------------------------------
# validate_page_ui_primitives()
# ---------------------------------------------------------------------------

class TestValidatePageUIPrimitives:
    @pytest.mark.parametrize("name", _NEW_PRIMITIVES)
    def test_accepts_each_new_primitive(self, name: str) -> None:
        result = ui_primitives.validate_page_ui_primitives(
            [name], context=f"test[{name}]"
        )
        assert result == [name]

    def test_accepts_all_shipped_primitives(self) -> None:
        result = ui_primitives.validate_page_ui_primitives(
            list(_ALL_SHIPPED), context="test.all"
        )
        assert set(result) == set(_ALL_SHIPPED)

    def test_rejects_unknown_primitive(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            ui_primitives.validate_page_ui_primitives(
                ["Wizard"], context="AppSchemaOutput.pages[0].sections"
            )
        message = str(exc_info.value)
        assert "Wizard" in message
        assert "Allowed primitives" in message

    def test_rejects_mixed_valid_and_invalid(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            ui_primitives.validate_page_ui_primitives(
                ["Card", "Wizard", "Timeline", "FakeWidget"],
                context="test.mixed",
            )
        message = str(exc_info.value)
        assert "Wizard" in message
        assert "FakeWidget" in message

    def test_error_message_names_context(self) -> None:
        ctx = "AppSchemaOutput.pages[1].sections[0].primitive"
        with pytest.raises(ValueError) as exc_info:
            ui_primitives.validate_page_ui_primitives(["Ghost"], context=ctx)
        assert ctx in str(exc_info.value)

    def test_accepts_none(self) -> None:
        result = ui_primitives.validate_page_ui_primitives(None, context="test.none")
        assert result == []

    def test_accepts_empty_list(self) -> None:
        result = ui_primitives.validate_page_ui_primitives([], context="test.empty")
        assert result == []

    def test_skips_whitespace_only_entries(self) -> None:
        result = ui_primitives.validate_page_ui_primitives(
            ["  ", "Card", "  "], context="test.ws"
        )
        assert result == ["Card"]

    def test_deduplicates_entries(self) -> None:
        result = ui_primitives.validate_page_ui_primitives(
            ["Card", "Card", "Stat"], context="test.dedup"
        )
        assert result == ["Card", "Stat"]


# ---------------------------------------------------------------------------
# structured_outputs.yaml AppPageSection.primitive
# ---------------------------------------------------------------------------

def _load_so_yaml() -> Dict[str, Any]:
    assert _STRUCTURED_OUTPUTS.exists(), f"Missing {_STRUCTURED_OUTPUTS}"
    return yaml.safe_load(_STRUCTURED_OUTPUTS.read_text(encoding="utf-8"))


def _get_app_page_section_primitive_values() -> List[str]:
    data = _load_so_yaml()
    models = data.get("models", {})
    app_page_section = models.get("AppPageSection", {})
    fields = app_page_section.get("fields", {})
    primitive_field = fields.get("primitive", {})
    values = primitive_field.get("values", [])
    assert isinstance(values, list), "AppPageSection.primitive.values must be a list"
    return values


class TestStructuredOutputsYaml:
    @pytest.fixture(scope="class")
    def primitive_values(self) -> List[str]:
        return _get_app_page_section_primitive_values()

    @pytest.mark.parametrize("name", _NEW_PRIMITIVES)
    def test_new_primitive_in_structured_outputs(
        self, name: str, primitive_values: List[str]
    ) -> None:
        assert name in primitive_values, (
            f"'{name}' missing from AppPageSection.primitive.values in structured_outputs.yaml"
        )

    def test_all_shipped_primitives_in_structured_outputs(
        self, primitive_values: List[str]
    ) -> None:
        missing = [n for n in _ALL_SHIPPED if n not in primitive_values]
        assert not missing, (
            f"These shipped primitives are absent from structured_outputs.yaml: {missing}"
        )

    def test_no_unknown_primitives_in_structured_outputs(
        self, primitive_values: List[str]
    ) -> None:
        catalog = set(ui_primitives.get_page_ui_primitive_names())
        unknown = [v for v in primitive_values if v not in catalog]
        assert not unknown, (
            f"structured_outputs.yaml lists primitives not in the shipped catalog: {unknown}"
        )

    def test_primitive_field_type_is_literal(self) -> None:
        data = _load_so_yaml()
        models = data.get("models", {})
        primitive_type = (
            models.get("AppPageSection", {})
            .get("fields", {})
            .get("primitive", {})
            .get("type")
        )
        assert primitive_type == "literal", (
            f"AppPageSection.primitive must have type: literal, got: {primitive_type!r}"
        )


# ---------------------------------------------------------------------------
# format_page_ui_primitive_guidance()
# ---------------------------------------------------------------------------

class TestPagePrimitiveGuidance:
    @pytest.fixture(scope="class")
    def guidance(self) -> str:
        return ui_primitives.format_page_ui_primitive_guidance()

    @pytest.mark.parametrize("name", _NEW_PRIMITIVES)
    def test_new_primitive_in_guidance(self, name: str, guidance: str) -> None:
        assert name in guidance, (
            f"'{name}' missing from format_page_ui_primitive_guidance() output"
        )

    def test_guidance_references_primitive_registry(self, guidance: str) -> None:
        assert "PrimitiveRegistry" in guidance
