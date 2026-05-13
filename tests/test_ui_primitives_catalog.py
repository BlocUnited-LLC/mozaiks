from __future__ import annotations

from tests.import_utils import import_module_directly


ui_primitives = import_module_directly("mozaiksai.core.workflow.ui_primitives")


def test_component_and_page_primitive_catalogs_match_runtime_exports() -> None:
    expected_page = (
        "ActionButton",
        "Alert",
        "AlertBanner",
        "Button",
        "CodeBlock",
        "DataTable",
        "Empty",
        "ErrorState",
        "FileList",
        "Form",
        "Grid",
        "InlineEmptyState",
        "LoadingState",
        "Metric",
        "Modal",
        "PageHeader",
        "Panel",
        "ProgressTracker",
        "ResourceTable",
        "SegmentedBar",
        "Skeleton",
        "StatusPill",
        "SummaryStrip",
        "SurfaceCard",
        "Timeline",
    )
    expected_component = tuple(
        sorted(
            set(expected_page)
            | {
                "CollectionToolbar",
                "IconButton",
                "LinkButton",
                "ResourceList",
                "SegmentedControl",
                "SlideOver",
            }
        )
    )

    assert ui_primitives.get_component_ui_primitive_names() == expected_component
    assert ui_primitives.get_page_ui_primitive_names() == expected_page
    for removed_name in ("Badge", "Card", "Stat"):
        assert removed_name not in expected_component
        assert removed_name not in expected_page


def test_component_guidance_includes_live_import_paths() -> None:
    guidance = ui_primitives.format_component_ui_primitive_guidance()

    assert "DataTable" in guidance
    assert "from '@mozaiks/chat-ui/ui'" in guidance
    assert "@mozaiks/chat-ui/ui/primitives/" not in guidance


def test_component_guidance_includes_new_primitives() -> None:
    guidance = ui_primitives.format_component_ui_primitive_guidance()

    for name in ("PageHeader", "ResourceTable", "Timeline", "CodeBlock", "ProgressTracker", "AlertBanner", "ActionButton", "FileList"):
        assert name in guidance, f"Expected '{name}' in component guidance"


def test_page_guidance_includes_new_primitives() -> None:
    guidance = ui_primitives.format_page_ui_primitive_guidance()

    for name in ("PageHeader", "ResourceTable", "Timeline", "CodeBlock", "ProgressTracker", "AlertBanner", "ActionButton", "FileList"):
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

    assert "Card" in message
    assert "Wizard" in message
    assert "Allowed primitives" in message


def test_component_primitive_validation_rejects_removed_names() -> None:
    try:
        ui_primitives.validate_component_ui_primitives(
            ["Card", "Stat", "Badge"],
            context="generated workflow UI imports",
        )
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected ValueError for removed component primitives")

    assert "Card" in message
    assert "Stat" in message
    assert "Badge" in message
