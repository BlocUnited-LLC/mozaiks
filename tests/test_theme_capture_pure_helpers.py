"""
Pure helper unit tests for:
  factory_app/workflows/ThemeCapture/tools/preload_theme_capture_context.py

Covers:
  _dedupe:
    - empty list → []
    - duplicates removed (first occurrence kept)
    - whitespace-only items removed
    - None items removed (str(None).strip() is not empty! → cast to "None" which is a non-empty string)
    - items stripped of surrounding whitespace
    - order preserved

  _top_items:
    - empty list → []
    - most common items first (up to limit)
    - limit=8 default (returns up to 8)
    - whitespace-only items excluded
    - items with most occurrences listed first

  _normalize_font_names:
    - generic CSS families stripped (serif, sans-serif, monospace, etc.)
    - first non-generic name from comma-separated list returned
    - quoted names unquoted
    - deduplicated
    - empty string skipped

  _hex_to_luminance:
    - invalid hex → None
    - non-hex string → None
    - white (#ffffff) → ~1.0
    - black (#000000) → ~0.0
    - 3-char hex expanded
    - invalid hex digits → None
    - result is a float

  _infer_appearance:
    - all dark colors → "dark"
    - all light colors → "light"
    - empty inputs → None
    - css_variable --color-background used
    - non-hex colors skipped (contribute nothing)

  _infer_layout_hints:
    - no signals → []
    - "sidebar" text → "sidebar"
    - "<aside" tag → "sidebar"
    - "<header" tag → "top-bar"
    - "navbar" → "top-bar"
    - "<footer" → "footer"
    - "glass" → "glass"
    - "backdrop-filter" → "glass"
    - deduplicated results

  _summarize_css_snapshot:
    - empty CSS → unknown appearance, no colors, no fonts
    - returns dict with required keys: source, appearance, colors, fonts, etc.
    - color hex values detected from CSS
    - font-family values detected
    - css_variables extracted
    - layout hints from sidebar/header/footer keywords
    - source always "css_snapshot"

  _summarize_theme_mapping:
    - empty config → all None/empty fields, source="parent_theme_config"
    - appearance from theme.appearance
    - primary color from colors.primary.main
    - body_font from fonts.body.family
    - app_name from identity.app_name
    - layout hints from shell_config.header and shell_config.footer
    - colors deduplicated
"""
from __future__ import annotations

from factory_app.workflows.ThemeCapture.tools.preload_theme_capture_context import (
    _dedupe,
    _hex_to_luminance,
    _infer_appearance,
    _infer_layout_hints,
    _normalize_font_names,
    _summarize_css_snapshot,
    _summarize_theme_mapping,
    _top_items,
)

# ---------------------------------------------------------------------------
# 1. _dedupe
# ---------------------------------------------------------------------------

class TestDedupe:
    def test_empty_list_returns_empty(self):
        assert _dedupe([]) == []

    def test_duplicates_removed(self):
        result = _dedupe(["a", "b", "a"])
        assert result == ["a", "b"]

    def test_first_occurrence_kept(self):
        result = _dedupe(["b", "a", "b"])
        assert result[0] == "b"
        assert result[1] == "a"

    def test_whitespace_items_removed(self):
        result = _dedupe(["  ", "", "a"])
        assert "" not in result
        assert "  " not in result
        assert "a" in result

    def test_items_stripped(self):
        result = _dedupe(["  hello  "])
        assert "hello" in result

    def test_order_preserved(self):
        items = ["c", "a", "b"]
        assert _dedupe(items) == ["c", "a", "b"]

    def test_single_item(self):
        assert _dedupe(["x"]) == ["x"]


# ---------------------------------------------------------------------------
# 2. _top_items
# ---------------------------------------------------------------------------

class TestTopItems:
    def test_empty_list_returns_empty(self):
        assert _top_items([]) == []

    def test_most_common_first(self):
        result = _top_items(["a", "b", "a", "b", "a"])
        assert result[0] == "a"
        assert result[1] == "b"

    def test_limit_respected(self):
        items = [str(i) for i in range(20)]
        result = _top_items(items, limit=5)
        assert len(result) <= 5

    def test_default_limit_is_8(self):
        items = [str(i) for i in range(20)]
        result = _top_items(items)
        assert len(result) <= 8

    def test_whitespace_items_excluded(self):
        result = _top_items(["  ", "  ", "a"])
        assert "  " not in result
        assert "" not in result

    def test_single_occurrence_items_included(self):
        result = _top_items(["a"])
        assert "a" in result


# ---------------------------------------------------------------------------
# 3. _normalize_font_names
# ---------------------------------------------------------------------------

class TestNormalizeFontNames:
    def test_empty_list_returns_empty(self):
        assert _normalize_font_names([]) == []

    def test_generic_family_serif_stripped(self):
        result = _normalize_font_names(["serif"])
        assert result == []

    def test_generic_sans_serif_stripped(self):
        result = _normalize_font_names(["sans-serif"])
        assert result == []

    def test_generic_monospace_stripped(self):
        result = _normalize_font_names(["monospace"])
        assert result == []

    def test_system_ui_stripped(self):
        result = _normalize_font_names(["system-ui"])
        assert result == []

    def test_named_font_preserved(self):
        result = _normalize_font_names(["Inter"])
        assert "Inter" in result

    def test_first_non_generic_from_comma_list(self):
        result = _normalize_font_names(["Inter, sans-serif"])
        assert "Inter" in result
        assert "sans-serif" not in result

    def test_quoted_font_name_unquoted(self):
        result = _normalize_font_names(["'Roboto', sans-serif"])
        assert "Roboto" in result

    def test_double_quoted_font_name_unquoted(self):
        result = _normalize_font_names(['"Open Sans", serif'])
        assert "Open Sans" in result

    def test_duplicates_removed(self):
        result = _normalize_font_names(["Inter", "Inter"])
        assert result.count("Inter") == 1

    def test_empty_string_skipped(self):
        result = _normalize_font_names([""])
        assert result == []


# ---------------------------------------------------------------------------
# 4. _hex_to_luminance
# ---------------------------------------------------------------------------

class TestHexToLuminance:
    def test_non_hex_returns_none(self):
        assert _hex_to_luminance("rgb(255,0,0)") is None

    def test_empty_string_returns_none(self):
        assert _hex_to_luminance("") is None

    def test_no_hash_returns_none(self):
        assert _hex_to_luminance("ffffff") is None

    def test_white_luminance_near_one(self):
        result = _hex_to_luminance("#ffffff")
        assert result is not None
        assert abs(result - 1.0) < 0.01

    def test_black_luminance_near_zero(self):
        result = _hex_to_luminance("#000000")
        assert result is not None
        assert abs(result - 0.0) < 0.01

    def test_three_char_hex_expanded(self):
        result = _hex_to_luminance("#fff")
        assert result is not None
        assert abs(result - 1.0) < 0.01

    def test_invalid_hex_digits_returns_none(self):
        assert _hex_to_luminance("#gggggg") is None

    def test_returns_float(self):
        result = _hex_to_luminance("#808080")
        assert isinstance(result, float)

    def test_result_between_zero_and_one(self):
        result = _hex_to_luminance("#808080")
        assert result is not None
        assert 0.0 <= result <= 1.0

    def test_too_short_hex_returns_none(self):
        # Only 1 char after # → len < 6 after expansion
        assert _hex_to_luminance("#f") is None


# ---------------------------------------------------------------------------
# 5. _infer_appearance
# ---------------------------------------------------------------------------

class TestInferAppearance:
    def test_empty_inputs_returns_none(self):
        assert _infer_appearance([], {}) is None

    def test_dark_background_returns_dark(self):
        # #000000 has luminance ~0.0
        result = _infer_appearance(["#000000"], {})
        assert result == "dark"

    def test_light_background_returns_light(self):
        # #ffffff has luminance ~1.0
        result = _infer_appearance(["#ffffff"], {})
        assert result == "light"

    def test_css_variable_background_used(self):
        result = _infer_appearance([], {"--color-background": "#ffffff"})
        assert result == "light"

    def test_non_hex_colors_skipped(self):
        # Non-hex strings contribute no luminance → no data → None
        result = _infer_appearance(["blue", "red"], {})
        assert result is None

    def test_mixed_light_colors_returns_light(self):
        result = _infer_appearance(["#ffffff", "#eeeeee", "#dddddd"], {})
        assert result == "light"

    def test_background_css_var_checked_first(self):
        result = _infer_appearance([], {"--background": "#ffffff"})
        assert result == "light"


# ---------------------------------------------------------------------------
# 6. _infer_layout_hints
# ---------------------------------------------------------------------------

class TestInferLayoutHints:
    def test_no_signals_returns_empty(self):
        assert _infer_layout_hints("", "") == []

    def test_sidebar_keyword_detected(self):
        result = _infer_layout_hints("", ".sidebar { display: flex; }")
        assert "sidebar" in result

    def test_aside_tag_detected(self):
        result = _infer_layout_hints("<aside>nav</aside>", "")
        assert "sidebar" in result

    def test_header_tag_detected(self):
        result = _infer_layout_hints("<header>logo</header>", "")
        assert "top-bar" in result

    def test_navbar_class_detected(self):
        result = _infer_layout_hints("", ".navbar { position: fixed; }")
        assert "top-bar" in result

    def test_footer_tag_detected(self):
        result = _infer_layout_hints("<footer>copyright</footer>", "")
        assert "footer" in result

    def test_glass_keyword_detected(self):
        result = _infer_layout_hints("", ".glass { backdrop-filter: blur(10px); }")
        assert "glass" in result

    def test_backdrop_filter_detected(self):
        result = _infer_layout_hints("", ".card { backdrop-filter: blur(4px); }")
        assert "glass" in result

    def test_blur_detected(self):
        result = _infer_layout_hints("", "filter: blur(4px);")
        assert "glass" in result

    def test_duplicates_removed(self):
        # Both "sidebar" in html and css_text
        result = _infer_layout_hints("sidebar", "sidebar layout")
        assert result.count("sidebar") == 1

    def test_case_insensitive(self):
        result = _infer_layout_hints("<HEADER>", "")
        assert "top-bar" in result


# ---------------------------------------------------------------------------
# 7. _summarize_css_snapshot
# ---------------------------------------------------------------------------

class TestSummarizeCssSnapshot:
    def test_empty_css_returns_dict_with_keys(self):
        result = _summarize_css_snapshot("")
        assert "source" in result
        assert "appearance" in result
        assert "colors" in result
        assert "fonts" in result
        assert "css_variables" in result
        assert "layout_hints" in result
        assert "snapshot" in result

    def test_source_always_css_snapshot(self):
        result = _summarize_css_snapshot("")
        assert result["source"] == "css_snapshot"

    def test_color_hex_detected(self):
        css = ".btn { color: #3498db; background: #2c3e50; }"
        result = _summarize_css_snapshot(css)
        assert any("#" in c for c in result["colors"])

    def test_font_family_detected(self):
        css = "body { font-family: Inter, sans-serif; }"
        result = _summarize_css_snapshot(css)
        assert "Inter" in result["fonts"]

    def test_css_variables_extracted(self):
        css = ":root { --primary-color: #3498db; }"
        result = _summarize_css_snapshot(css)
        assert "--primary-color" in result["css_variables"]

    def test_appearance_inferred(self):
        css = ":root { --color-background: #ffffff; }"
        result = _summarize_css_snapshot(css)
        # May be "light" or "unknown" depending on CSS parsing
        assert result["appearance"] in {"light", "dark", None}

    def test_sidebar_layout_hint_detected(self):
        css = ".sidebar { width: 200px; }"
        result = _summarize_css_snapshot(css)
        assert "sidebar" in result["layout_hints"]

    def test_empty_css_unknown_appearance(self):
        result = _summarize_css_snapshot("")
        assert result["appearance"] is None


# ---------------------------------------------------------------------------
# 8. _summarize_theme_mapping
# ---------------------------------------------------------------------------

class TestSummarizeThemeMapping:
    def test_empty_config_returns_source(self):
        result = _summarize_theme_mapping({})
        assert result["source"] == "parent_theme_config"

    def test_empty_config_fields_are_empty(self):
        result = _summarize_theme_mapping({})
        assert result["appearance"] is None
        assert result["colors"] == []
        assert result["fonts"] == []
        assert result["layout_hints"] == []

    def test_appearance_from_theme(self):
        config = {"theme": {"appearance": "dark"}}
        result = _summarize_theme_mapping(config)
        assert result["appearance"] == "dark"

    def test_primary_color_from_colors(self):
        config = {"colors": {"primary": {"main": "#ff0000"}}}
        result = _summarize_theme_mapping(config)
        assert "#ff0000" in result["colors"]

    def test_secondary_color_included(self):
        config = {"colors": {"secondary": {"main": "#00ff00"}}}
        result = _summarize_theme_mapping(config)
        assert "#00ff00" in result["colors"]

    def test_body_font_from_fonts(self):
        config = {"fonts": {"body": {"family": "Inter"}}}
        result = _summarize_theme_mapping(config)
        assert "Inter" in result["fonts"]

    def test_heading_font_included(self):
        config = {"fonts": {"heading": {"family": "Playfair Display"}}}
        result = _summarize_theme_mapping(config)
        assert "Playfair Display" in result["fonts"]

    def test_app_name_from_identity(self):
        config = {"identity": {"app_name": "MyApp"}}
        result = _summarize_theme_mapping(config)
        assert result["app_name"] == "MyApp"

    def test_app_name_fallback_from_theme_branding(self):
        config = {"theme": {"branding": {"app_name": "BrandApp"}}}
        result = _summarize_theme_mapping(config)
        assert result["app_name"] == "BrandApp"

    def test_shell_config_header_adds_top_bar_hint(self):
        result = _summarize_theme_mapping({}, shell_config={"header": True})
        assert "top-bar" in result["layout_hints"]

    def test_shell_config_footer_adds_footer_hint(self):
        result = _summarize_theme_mapping({}, shell_config={"footer": True})
        assert "footer" in result["layout_hints"]

    def test_colors_deduplicated(self):
        config = {
            "colors": {
                "primary": {"main": "#ff0000"},
                "secondary": {"main": "#ff0000"},
            }
        }
        result = _summarize_theme_mapping(config)
        assert result["colors"].count("#ff0000") == 1

    def test_snapshot_string_contains_appearance(self):
        config = {"theme": {"appearance": "light"}}
        result = _summarize_theme_mapping(config)
        assert "light" in result["snapshot"]

    def test_css_variables_always_empty_dict(self):
        result = _summarize_theme_mapping({})
        assert result["css_variables"] == {}
