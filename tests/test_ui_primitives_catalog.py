from __future__ import annotations

from tests.import_utils import import_module_directly


ui_primitives = import_module_directly("mozaiksai.core.workflow.ui_primitives")


def test_component_and_page_primitive_catalogs_match_runtime_exports() -> None:
    expected = (
        "Alert",
        "Badge",
        "Button",
        "Card",
        "DataTable",
        "Empty",
        "Form",
        "Grid",
        "Modal",
        "Skeleton",
        "Stat",
    )

    assert ui_primitives.get_component_ui_primitive_names() == expected
    assert ui_primitives.get_page_ui_primitive_names() == expected


def test_component_guidance_includes_live_import_paths() -> None:
    guidance = ui_primitives.format_component_ui_primitive_guidance()

    assert "DataTable" in guidance
    assert "../../ui/primitives/DataTable.jsx" in guidance
    assert "../../ui/primitives/Skeleton.jsx" in guidance


def test_page_primitive_validation_rejects_unknown_names() -> None:
    try:
        ui_primitives.validate_page_ui_primitives(
            ["Card", "Wizard"],
            context="AppSchemaOutput.pages[0].sections",
        )
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected ValueError for unsupported primitive")

    assert "Wizard" in message
    assert "Allowed primitives" in message
