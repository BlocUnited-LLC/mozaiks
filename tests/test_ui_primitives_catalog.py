from __future__ import annotations

from tests.import_utils import import_module_directly


ui_primitives = import_module_directly("mozaiksai.core.workflow.ui_primitives")


def test_component_and_page_primitive_catalogs_match_runtime_exports() -> None:
    expected = (
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

    assert ui_primitives.get_component_ui_primitive_names() == expected
    assert ui_primitives.get_page_ui_primitive_names() == expected


def test_component_guidance_includes_live_import_paths() -> None:
    guidance = ui_primitives.format_component_ui_primitive_guidance()

    assert "DataTable" in guidance
    # format_component_ui_primitive_guidance() uses the @mozaiks/chat-ui package alias
    assert "@mozaiks/chat-ui/ui/primitives/DataTable.jsx" in guidance
    assert "@mozaiks/chat-ui/ui/primitives/Skeleton.jsx" in guidance


def test_component_guidance_includes_new_primitives() -> None:
    guidance = ui_primitives.format_component_ui_primitive_guidance()

    for name in ("Timeline", "CodeBlock", "ProgressTracker", "AlertBanner", "ActionButton", "FileList"):
        assert name in guidance, f"Expected '{name}' in component guidance"


def test_page_guidance_includes_new_primitives() -> None:
    guidance = ui_primitives.format_page_ui_primitive_guidance()

    for name in ("Timeline", "CodeBlock", "ProgressTracker", "AlertBanner", "ActionButton", "FileList"):
        assert name in guidance, f"Expected '{name}' in page primitive guidance"


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
